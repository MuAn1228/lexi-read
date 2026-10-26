"""Deterministic explicit-assessment intervals, separate from mastery scoring."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import ReviewSchedule, Vocabulary
from backend.schemas import Status
from backend.services.adaptive_review import (SCHEDULE_VERSION, ReviewProfile,
                                               memory_state, read_review_profiles)

KNOWN_DAYS = (3, 7, 14, 30, 60)


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)


def next_intervals(known_streak: int, mastery_score: float | None = None,
                   profile: ReviewProfile | None = None) -> dict[Status, int]:
    """Return deterministic V2 choices from the evidence available before a mark."""
    profile = profile or ReviewProfile()
    streak = max(0, min(known_streak, len(KNOWN_DAYS)))
    base_days = KNOWN_DAYS[min(streak, len(KNOWN_DAYS) - 1)]
    expected_mastery = min(1.0, .5 + .15 * streak)
    mastery = .5 if mastery_score is None else max(0.0, min(1.0, mastery_score))
    mastery_adjustment = max(-.15, min(.15, (mastery - expected_mastery) * .4))
    factor = max(.65, min(1.15, 1 - profile.failure_ratio * .45 + mastery_adjustment
                          + profile.response_adjustment))
    known_days = max(1, min(180, round(base_days * factor)))
    if streak >= 4 and profile.failure_ratio < .15:
        uncertain_days = 3
    elif streak >= 2 and profile.failure_ratio < .35:
        uncertain_days = 2
    else:
        uncertain_days = 1
    return {Status.UNKNOWN: 600, Status.UNCERTAIN: uncertain_days * 86400,
            Status.KNOWN: known_days * 86400}


def schedule_mark(session: Session, entry: Vocabulary, status: Status, *, review: bool,
                  now: datetime, mastery_before: float | None = None) -> dict:
    # Caller owns the transaction / SQLite writer reservation. No passive read,
    # lookup, or observation invokes this function.
    profile = (read_review_profiles(session, entry.user_id, [entry.lemma]).get(entry.lemma, ReviewProfile())
               if review else ReviewProfile())
    state = session.scalar(select(ReviewSchedule).where(
        ReviewSchedule.vocabulary_id == entry.id, ReviewSchedule.user_id == entry.user_id,
    ).execution_options(populate_existing=True))
    if state is None:
        state = ReviewSchedule(vocabulary_id=entry.id, user_id=entry.user_id, known_streak=0)
        session.add(state)
    if review:
        intervals = next_intervals(state.known_streak, mastery_before, profile)
        seconds = intervals[status]
        state.known_streak = min(state.known_streak + 1, len(KNOWN_DAYS)) if status == Status.KNOWN else 0
        if status == Status.UNKNOWN:
            reason = "暂时遗忘，10 分钟后快速重试"
        elif status == Status.UNCERTAIN:
            reason = f"仍有些模糊，{seconds // 86400} 天后再次确认"
        elif profile.failure_ratio >= .2:
            reason = "近期有遗忘，已缩短巩固间隔"
        else:
            reason = "根据当前记忆阶段安排下次复习"
        schedule_details = {
            "schedule_version": SCHEDULE_VERSION,
            "review_interval_seconds": seconds,
            "adaptive_reason": reason,
            "memory_state": memory_state(entry.status, state.known_streak, profile),
        }
    else:
        seconds = 3 * 86400 if status == Status.KNOWN else 0
        state.known_streak = 0
        schedule_details = {}
    state.due_at = utc(now) + timedelta(seconds=seconds)
    return schedule_details
