"""Bounded, explainable signals for adaptive review and daily time plans."""
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import median
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models import LearningEvent


REVIEW_EVENTS = ("MARK_KNOWN", "MARK_UNKNOWN", "MARK_UNCERTAIN")
SCHEDULE_VERSION = "adaptive_v2"
MemoryState = Literal["FRAGILE", "BUILDING", "STABLE"]
PlanReason = Literal["FIRST_REVIEW", "OVERDUE", "RECENT_LAPSE", "REPEATED_DIFFICULTY",
                     "FREQUENT_IN_READING", "DUE"]


@dataclass(frozen=True)
class ReviewProfile:
    """Newest-first explicit review outcomes; never includes passive learning."""

    outcomes: tuple[str, ...] = ()
    response_times_ms: tuple[int | None, ...] = ()

    @property
    def attempts(self) -> int:
        return len(self.outcomes)

    @property
    def failure_ratio(self) -> float:
        if not self.outcomes:
            return 0.0
        weighted = total = 0.0
        values = {"MARK_UNKNOWN": 1.0, "MARK_UNCERTAIN": .45, "MARK_KNOWN": 0.0}
        for index, event_type in enumerate(self.outcomes):
            weight = .82 ** index
            weighted += values[event_type] * weight
            total += weight
        return weighted / total if total else 0.0

    @property
    def response_adjustment(self) -> float:
        """Small interval multiplier from valid prior recall times only."""
        samples = [value for value in self.response_times_ms[:5] if value is not None]
        if len(samples) < 3:
            return 0.0
        typical = median(samples)
        if typical >= 45_000:
            return -.08
        if typical <= 5_000 and self.failure_ratio < .15:
            return .05
        return 0.0


def read_review_profiles(session: Session, user_id: int, lemmas: list[str], *, per_word: int = 8) -> dict[str, ReviewProfile]:
    """Read at most ``per_word`` outcomes per bounded candidate lemma in one query."""
    if not lemmas:
        return {}
    unique = list(dict.fromkeys(lemmas))
    ranked = select(
        LearningEvent.lemma.label("lemma"), LearningEvent.event_type.label("event_type"),
        LearningEvent.details.label("details"),
        func.row_number().over(
            partition_by=LearningEvent.lemma,
            order_by=(LearningEvent.created_at.desc(), LearningEvent.id.desc()),
        ).label("position"),
    ).where(
        LearningEvent.user_id == user_id,
        LearningEvent.lemma.in_(unique),
        LearningEvent.event_type.in_(REVIEW_EVENTS),
        LearningEvent.details["origin"].as_string() == "review",
    ).subquery()
    rows = session.execute(select(ranked.c.lemma, ranked.c.event_type, ranked.c.details,
                                 ranked.c.position).where(
        ranked.c.position <= per_word,
    ).order_by(ranked.c.lemma, ranked.c.position)).all()
    grouped: dict[str, list[tuple[str, int | None]]] = {lemma: [] for lemma in unique}
    for lemma, event_type, details, _ in rows:
        grouped[lemma].append((event_type, valid_response_ms(details)))
    return {lemma: ReviewProfile(tuple(event for event, _ in values),
                                 tuple(response for _, response in values))
            for lemma, values in grouped.items()}


def memory_state(status: str | None, known_streak: int, profile: ReviewProfile) -> MemoryState:
    failure = profile.failure_ratio
    # A word explicitly marked unknown is fragile even before it has a review
    # history. This keeps the first card's explanation consistent with the
    # learner's current assessment instead of labelling it as merely building.
    if status == "UNKNOWN":
        return "FRAGILE"
    if profile.attempts == 0:
        return "BUILDING"
    if failure >= .45:
        return "FRAGILE"
    if status == "KNOWN" and known_streak >= 3 and failure < .12:
        return "STABLE"
    return "BUILDING"


def selection_reason(status: str | None, known_streak: int, profile: ReviewProfile,
                     due_at: datetime | None, now: datetime, seen_count: int = 0) -> str:
    if profile.attempts == 0:
        return "FIRST_REVIEW"
    if profile.attempts >= 2 and profile.failure_ratio >= .45:
        return "REPEATED_DIFFICULTY"
    if profile.outcomes[0] == "MARK_UNKNOWN":
        return "RECENT_LAPSE"
    comparable_due = due_at.replace(tzinfo=due_at.tzinfo or timezone.utc) if due_at is not None else None
    if comparable_due is not None and (now - comparable_due).total_seconds() >= 6 * 3600:
        return "OVERDUE"
    if seen_count >= 20:
        return "FREQUENT_IN_READING"
    return "DUE"


def priority_score(status: str | None, mastery_score: float | None, seen_count: int,
                   due_at: datetime | None, now: datetime, profile: ReviewProfile) -> int:
    status_weight = {"UNKNOWN": 32, "UNCERTAIN": 23, "KNOWN": 8}.get(status, 15)
    if due_at is None:
        overdue = 28
    else:
        comparable_due = due_at.replace(tzinfo=due_at.tzinfo or timezone.utc)
        seconds = max(0.0, (now - comparable_due).total_seconds())
        overdue = min(30, int(seconds / 86400 * 4))
    mastery = .5 if mastery_score is None else mastery_score
    score = status_weight + overdue + round(profile.failure_ratio * 25) + round((1 - mastery) * 12)
    score += min(5, max(0, seen_count) // 5)
    score += round(-profile.response_adjustment * 35)
    return max(0, min(100, score))


def valid_response_ms(details: object) -> int | None:
    if not isinstance(details, dict):
        return None
    value = details.get("response_ms")
    return value if type(value) is int and 500 <= value <= 600_000 else None


def review_plan_metrics(session: Session, user_id: int, *, today: datetime, tomorrow: datetime,
                        minutes: int, due_now: int, later_today: int) -> dict:
    """Estimate a bounded daily plan without recording an impression or changing a due date."""
    conditions = (
        LearningEvent.user_id == user_id,
        LearningEvent.event_type.in_(REVIEW_EVENTS),
        LearningEvent.details["origin"].as_string() == "review",
    )
    recent = list(session.scalars(select(LearningEvent.details).where(*conditions).order_by(
        LearningEvent.created_at.desc(), LearningEvent.id.desc(),
    ).limit(200)))
    samples = [value for details in recent if (value := valid_response_ms(details)) is not None][:30]
    estimated = int(round(median(samples) / 1000)) if samples else 35
    estimated = max(15, min(120, estimated))
    today_details = list(session.scalars(select(LearningEvent.details).where(
        *conditions, LearningEvent.created_at >= today, LearningEvent.created_at < tomorrow,
    ).order_by(LearningEvent.created_at.desc(), LearningEvent.id.desc()).limit(500)))
    spent = sum(max(1, round(value / 1000)) if (value := valid_response_ms(details)) is not None else estimated
                for details in today_details)
    budget = minutes * 60
    remaining = max(0, budget - spent)
    capacity = min(50, max(0, budget // estimated))
    completed = len(today_details)
    target = min(50, capacity, completed + due_now + later_today)
    remaining_cards = min(max(0, target - completed), max(0, remaining // estimated),
                          due_now + later_today)
    available_now = min(due_now, remaining_cards)
    time_exhausted = remaining // estimated == 0
    status = ("COMPLETE" if target > 0 and (completed >= target or time_exhausted) else
              "WAITING" if not due_now and later_today and remaining_cards else
              "LIGHT" if target < capacity and remaining_cards else
              "READY" if available_now else "LIGHT")
    return {
        "target_minutes": minutes,
        "estimated_seconds_per_card": estimated,
        "capacity_cards": capacity,
        "target_cards": target,
        "completed_cards": completed,
        "remaining_cards": remaining_cards,
        "available_now": available_now,
        "due_now": due_now,
        "later_today": later_today,
        "progress_percent": (min(100, round(completed / target * 100)) if target else 0),
        "status": status,
    }
