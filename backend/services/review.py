"""Read-only due queue and local-calendar review totals."""
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from backend.models import Document, LearningEvent, ReviewSchedule, Vocabulary, WordOccurrence
from backend.models.entities import utcnow
from backend.schemas import ReviewItem, ReviewOccurrence, ReviewOut
from backend.services.adaptive_review import (ReviewProfile, memory_state, priority_score,
                                               read_review_profiles, review_plan_metrics,
                                               selection_reason)
from backend.services.scheduling import next_intervals, utc
from backend.services.review_context import review_target_range


class ReviewService:
    def __init__(self, session: Session, user_id: int = 1, *, now: datetime | None = None):
        self.session, self.user_id = session, user_id
        self.now = utc(now) if now is not None else None

    def read(self, limit: int = 10, tz_offset_minutes: int = 480,
             vocabulary_ids: list[int] | None = None,
             plan_minutes: int | None = None) -> ReviewOut:
        # A shared session may contain unrelated pending writes. Reading the
        # queue must never flush those writes or record a review impression.
        with self.session.no_autoflush:
            return self._read(limit, tz_offset_minutes, vocabulary_ids, plan_minutes)

    def _read(self, limit: int, tz_offset_minutes: int, vocabulary_ids: list[int] | None,
              plan_minutes: int | None) -> ReviewOut:
        if plan_minutes is not None and (type(plan_minutes) is not int or plan_minutes not in {10, 20, 30}):
            raise ValueError("Invalid daily study duration")
        now = self.now or utcnow()
        local_zone = timezone(timedelta(minutes=tz_offset_minutes))
        local_date = now.astimezone(local_zone).date()
        today = datetime.combine(local_date, time.min, local_zone).astimezone(timezone.utc)
        tomorrow = today + timedelta(days=1)
        join = and_(ReviewSchedule.vocabulary_id == Vocabulary.id,
                    ReviewSchedule.user_id == self.user_id)
        # Unscheduled unfamiliar words still enter the queue; legacy KNOWN
        # entries are scheduled by migration, never by a read operation.
        due = or_(ReviewSchedule.due_at <= now, and_(
            ReviewSchedule.vocabulary_id.is_(None), Vocabulary.status.in_(("UNKNOWN", "UNCERTAIN")),
        ))
        future = ReviewSchedule.due_at > now
        review_events = (
            LearningEvent.user_id == self.user_id,
            LearningEvent.event_type.in_(("MARK_KNOWN", "MARK_UNKNOWN", "MARK_UNCERTAIN")),
            LearningEvent.details["origin"].as_string() == "review",
        )
        reviewed_today = select(func.count(func.distinct(LearningEvent.lemma))).where(
            *review_events, LearningEvent.created_at >= today, LearningEvent.created_at < tomorrow,
        ).scalar_subquery()
        summary = self.session.execute(select(
            func.coalesce(func.sum(case((due, 1), else_=0)), 0).label("total"),
            func.coalesce(func.sum(case((and_(future, ReviewSchedule.due_at < tomorrow), 1), else_=0)), 0)
            .label("later_today"),
            func.min(case((future, ReviewSchedule.due_at))).label("next_due_at"),
            reviewed_today.label("reviewed_today"),
        ).select_from(Vocabulary).outerjoin(ReviewSchedule, join).where(
            Vocabulary.user_id == self.user_id, Vocabulary.status.is_not(None),
        )).mappings().one()
        plan = review_plan_metrics(
            self.session, self.user_id, today=today, tomorrow=tomorrow,
            minutes=plan_minutes, due_now=int(summary["total"]),
            later_today=int(summary["later_today"]),
        ) if plan_minutes is not None else None
        result = dict(summary, as_of=now, plan=plan)
        reviewed = select(
            LearningEvent.lemma.label("lemma"),
            func.max(LearningEvent.created_at).label("last_reviewed_at"),
        ).where(*review_events).group_by(LearningEvent.lemma).subquery()
        query = select(Vocabulary, ReviewSchedule, reviewed.c.last_reviewed_at).outerjoin(
            ReviewSchedule, join,
        ).outerjoin(reviewed, reviewed.c.lemma == Vocabulary.lemma).where(
            Vocabulary.user_id == self.user_id, Vocabulary.status.is_not(None), due,
        )
        if vocabulary_ids is not None:
            query = query.where(Vocabulary.id.in_(vocabulary_ids))
        candidate_limit = min(200, max(limit * 4, 50)) if plan is not None and vocabulary_ids is None else limit
        rows = list(self.session.execute(query.order_by(
            ReviewSchedule.due_at.asc(),
            case((reviewed.c.last_reviewed_at.is_(None), 0), else_=1),
            reviewed.c.last_reviewed_at.asc(),
            func.coalesce(Vocabulary.mastery_score, .5).asc(),
            Vocabulary.seen_count.desc(),
            Vocabulary.lemma.asc(), Vocabulary.id.asc(),
        ).limit(candidate_limit)))
        if not rows:
            return ReviewOut(items=[], **result)

        profiles = read_review_profiles(self.session, self.user_id, [entry.lemma for entry, _, _ in rows])
        metadata = {}
        for entry, state, _ in rows:
            profile = profiles.get(entry.lemma, ReviewProfile())
            streak = state.known_streak if state else 0
            due_at = state.due_at if state else None
            metadata[entry.id] = {
                "profile": profile,
                "priority": priority_score(entry.status, entry.mastery_score, entry.seen_count,
                                             due_at, now, profile),
                "reason": selection_reason(entry.status, streak, profile, due_at, now, entry.seen_count),
                "memory_state": memory_state(entry.status, streak, profile),
            }
        if plan is not None and vocabulary_ids is None:
            rows.sort(key=lambda row: (-metadata[row[0].id]["priority"],
                                      utc(row[1].due_at).timestamp() if row[1] else float("-inf"),
                                      row[0].lemma, row[0].id))
        if plan is not None:
            effective_limit = min(limit, 10, plan["available_now"]) if vocabulary_ids is None else min(limit, 10)
        else:
            effective_limit = limit
        rows = rows[:effective_limit]
        if not rows:
            return ReviewOut(items=[], **result)

        # Only fetch context for the bounded batch. A recommendation may have
        # a source document without ever occurring there, so match the actual
        # lemma in the user's documents instead of using an event's document.
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
            Document.user_id == self.user_id,
            Document.deleted_at.is_(None),
            WordOccurrence.lemma.in_([entry.lemma for entry, _, _ in rows]),
        ).subquery()
        context_rows = self.session.execute(select(occurrences, Document).join(
            Document, Document.id == occurrences.c.document_id,
        ).where(
            occurrences.c.position == 1,
        )).mappings()
        # Load original text only for this bounded batch of contexts, keeping
        # the queue at three SELECTs rather than adding one query per card.
        contexts = {}
        for row in context_rows:
            target = review_target_range(row["Document"], SimpleNamespace(**row))
            contexts[row["lemma"]] = ReviewOccurrence(**{key: row[key] for key in (
                "id", "document_id", "document_title", "word", "sentence",
            )}, target_start=target[0] if target else None, target_end=target[1] if target else None)
        return ReviewOut(items=[ReviewItem(
            vocabulary=entry, occurrence=contexts.get(entry.lemma), last_reviewed_at=last_at,
            due_at=state.due_at if state else None, known_streak=state.known_streak if state else 0,
            next_intervals_seconds=next_intervals(
                state.known_streak if state else 0, entry.mastery_score,
                metadata[entry.id]["profile"],
            ), priority=metadata[entry.id]["priority"], plan_reason=metadata[entry.id]["reason"],
            memory_state=metadata[entry.id]["memory_state"],
        ) for entry, state, last_at in rows], **result)
