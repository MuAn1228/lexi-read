"""Read-only due queue and daily plan for individual dictionary senses."""
from datetime import datetime, time, timedelta, timezone
from statistics import median
from types import SimpleNamespace

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from backend.models import Document, LearningEvent, Vocabulary, VocabularySense, WordOccurrence
from backend.models.entities import utcnow
from backend.schemas import ReviewOccurrence, SenseReviewItem, SenseReviewOut
from backend.services.adaptive_review import (ReviewProfile, memory_state, priority_score,
                                               selection_reason, valid_response_ms)
from backend.services.offline_dictionary import OfflineDictionaryUnavailable, sense_catalog
from backend.services.review_context import review_target_range
from backend.services.scheduling import next_intervals, utc
from backend.services.sense_learning import (SENSE_REVIEW_EVENTS, read_sense_profiles,
                                              sense_output)


def sense_review_plan_metrics(session: Session, user_id: int, *, today: datetime, tomorrow: datetime,
                              minutes: int, due_now: int, later_today: int) -> dict:
    conditions = (
        LearningEvent.user_id == user_id,
        LearningEvent.event_type.in_(SENSE_REVIEW_EVENTS),
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
        "progress_percent": min(100, round(completed / target * 100)) if target else 0,
        "status": status,
    }


class SenseReviewService:
    def __init__(self, session: Session, user_id: int = 1, *, now: datetime | None = None):
        self.session, self.user_id = session, user_id
        self.now = utc(now) if now is not None else None

    def read(self, limit: int = 10, tz_offset_minutes: int = 480,
             sense_ids: list[int] | None = None, plan_minutes: int | None = None) -> SenseReviewOut:
        with self.session.no_autoflush:
            return self._read(limit, tz_offset_minutes, sense_ids, plan_minutes)

    def _read(self, limit: int, tz_offset_minutes: int, sense_ids: list[int] | None,
              plan_minutes: int | None) -> SenseReviewOut:
        if plan_minutes is not None and (type(plan_minutes) is not int or plan_minutes not in {10, 20, 30}):
            raise ValueError("Invalid daily study duration")
        now = self.now or utcnow()
        local_zone = timezone(timedelta(minutes=tz_offset_minutes))
        local_date = now.astimezone(local_zone).date()
        today = datetime.combine(local_date, time.min, local_zone).astimezone(timezone.utc)
        tomorrow = today + timedelta(days=1)
        due = VocabularySense.due_at <= now
        future = VocabularySense.due_at > now
        review_events = (
            LearningEvent.user_id == self.user_id,
            LearningEvent.event_type.in_(SENSE_REVIEW_EVENTS),
            LearningEvent.details["origin"].as_string() == "review",
        )
        reviewed_today = select(func.count(LearningEvent.id)).where(
            *review_events, LearningEvent.created_at >= today, LearningEvent.created_at < tomorrow,
        ).scalar_subquery()
        summary = self.session.execute(select(
            func.coalesce(func.sum(case((due, 1), else_=0)), 0).label("total"),
            func.coalesce(func.sum(case((and_(future, VocabularySense.due_at < tomorrow), 1), else_=0)), 0)
            .label("later_today"),
            func.min(case((future, VocabularySense.due_at))).label("next_due_at"),
            reviewed_today.label("reviewed_today"),
        ).select_from(VocabularySense).where(VocabularySense.user_id == self.user_id)).mappings().one()
        plan = sense_review_plan_metrics(
            self.session, self.user_id, today=today, tomorrow=tomorrow,
            minutes=plan_minutes, due_now=int(summary["total"]), later_today=int(summary["later_today"]),
        ) if plan_minutes is not None else None
        result = dict(summary, as_of=now, plan=plan)

        sense_id = LearningEvent.details["sense_id"].as_integer()
        reviewed = select(
            sense_id.label("sense_id"), func.max(LearningEvent.created_at).label("last_reviewed_at"),
        ).where(*review_events).group_by(sense_id).subquery()
        query = select(VocabularySense, Vocabulary, reviewed.c.last_reviewed_at).join(
            Vocabulary, and_(Vocabulary.id == VocabularySense.vocabulary_id,
                             Vocabulary.user_id == self.user_id),
        ).outerjoin(reviewed, reviewed.c.sense_id == VocabularySense.id).where(
            VocabularySense.user_id == self.user_id, due,
        )
        if sense_ids is not None:
            query = query.where(VocabularySense.id.in_(sense_ids))
        candidate_limit = min(200, max(limit * 4, 50)) if plan is not None and sense_ids is None else limit
        rows = list(self.session.execute(query.order_by(
            VocabularySense.due_at.asc(),
            case((reviewed.c.last_reviewed_at.is_(None), 0), else_=1),
            reviewed.c.last_reviewed_at.asc(), VocabularySense.mastery_score.asc(),
            Vocabulary.seen_count.desc(), Vocabulary.lemma.asc(),
            VocabularySense.source_ordinal.asc(), VocabularySense.id.asc(),
        ).limit(candidate_limit)))
        if not rows:
            return SenseReviewOut(items=[], **result)

        profiles = read_sense_profiles(self.session, self.user_id, [sense.id for sense, _, _ in rows])
        metadata = {}
        for sense, vocabulary, _ in rows:
            profile = profiles.get(sense.id, ReviewProfile())
            metadata[sense.id] = {
                "profile": profile,
                "priority": priority_score(sense.status, sense.mastery_score, vocabulary.seen_count,
                                             sense.due_at, now, profile),
                "reason": selection_reason(sense.status, sense.known_streak, profile,
                                            sense.due_at, now, vocabulary.seen_count),
                "memory_state": memory_state(sense.status, sense.known_streak, profile),
            }
        if plan is not None and sense_ids is None:
            rows.sort(key=lambda row: (-metadata[row[0].id]["priority"],
                                       utc(row[0].due_at).timestamp(), row[1].lemma,
                                       row[0].source_ordinal, row[0].id))
        if plan is not None:
            effective_limit = min(limit, 10, plan["available_now"]) if sense_ids is None else min(limit, 10)
        else:
            effective_limit = limit
        rows = rows[:effective_limit]
        if not rows:
            return SenseReviewOut(items=[], **result)

        lemmas = list(dict.fromkeys(vocabulary.lemma for _, vocabulary, _ in rows))
        occurrences = select(
            WordOccurrence.lemma.label("lemma"), WordOccurrence.id.label("id"),
            Document.id.label("document_id"), Document.title.label("document_title"),
            WordOccurrence.word.label("word"), WordOccurrence.sentence.label("sentence"),
            WordOccurrence.text_start.label("text_start"), WordOccurrence.text_end.label("text_end"),
            func.row_number().over(
                partition_by=WordOccurrence.lemma,
                order_by=(Document.created_at.desc(), Document.id.desc(), WordOccurrence.id.desc()),
            ).label("position"),
        ).join(Document, Document.id == WordOccurrence.document_id).where(
            Document.user_id == self.user_id, Document.deleted_at.is_(None),
            WordOccurrence.lemma.in_(lemmas),
        ).subquery()
        context_rows = self.session.execute(select(occurrences, Document).join(
            Document, Document.id == occurrences.c.document_id,
        ).where(occurrences.c.position == 1)).mappings()
        contexts = {}
        for row in context_rows:
            target = review_target_range(row["Document"], SimpleNamespace(**row))
            contexts[row["lemma"]] = ReviewOccurrence(**{key: row[key] for key in (
                "id", "document_id", "document_title", "word", "sentence",
            )}, target_start=target[0] if target else None, target_end=target[1] if target else None)

        availability: dict[tuple[str, str], set[str]] = {}
        for _, vocabulary, _ in rows:
            identity = (vocabulary.word, vocabulary.lemma)
            if identity not in availability:
                try:
                    availability[identity] = {item["sense_key"] for item in sense_catalog(*identity)}
                except OfflineDictionaryUnavailable:
                    availability[identity] = set()
        return SenseReviewOut(items=[SenseReviewItem(
            sense=sense_output(sense, available=sense.sense_key in availability[(vocabulary.word,
                                                                                 vocabulary.lemma)]),
            vocabulary=vocabulary, occurrence=contexts.get(vocabulary.lemma), last_reviewed_at=last_at,
            due_at=sense.due_at, known_streak=sense.known_streak,
            next_intervals_seconds=next_intervals(sense.known_streak, sense.mastery_score,
                                                  metadata[sense.id]["profile"]),
            priority=metadata[sense.id]["priority"], plan_reason=metadata[sense.id]["reason"],
            memory_state=metadata[sense.id]["memory_state"],
        ) for sense, vocabulary, last_at in rows], **result)
