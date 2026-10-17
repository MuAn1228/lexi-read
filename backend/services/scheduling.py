"""Deterministic explicit-assessment intervals, separate from mastery scoring."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import ReviewSchedule, Vocabulary
from backend.schemas import Status

KNOWN_DAYS = (3, 7, 14, 30, 60)


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)


def next_intervals(known_streak: int) -> dict[Status, int]:
    return {Status.UNKNOWN: 600, Status.UNCERTAIN: 86400,
            Status.KNOWN: KNOWN_DAYS[min(known_streak, len(KNOWN_DAYS) - 1)] * 86400}


def schedule_mark(session: Session, entry: Vocabulary, status: Status, *, review: bool, now: datetime) -> None:
    # Caller owns the transaction / SQLite writer reservation. No passive read,
    # lookup, or observation invokes this function.
    state = session.scalar(select(ReviewSchedule).where(
        ReviewSchedule.vocabulary_id == entry.id, ReviewSchedule.user_id == entry.user_id,
    ).execution_options(populate_existing=True))
    if state is None:
        state = ReviewSchedule(vocabulary_id=entry.id, user_id=entry.user_id, known_streak=0)
        session.add(state)
    if review:
        seconds = next_intervals(state.known_streak)[status]
        state.known_streak = min(state.known_streak + 1, len(KNOWN_DAYS)) if status == Status.KNOWN else 0
    else:
        seconds = 3 * 86400 if status == Status.KNOWN else 0
        state.known_streak = 0
    state.due_at = utc(now) + timedelta(seconds=seconds)
