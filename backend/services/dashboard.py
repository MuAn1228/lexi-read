"""Read-only SQL views of a user's recorded learning behavior.

Counts are descriptive, not proficiency estimates. Historical lookup_count is
retained, while the rolling lookup metric requires an explicit intentional flag.
"""
from datetime import datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from backend.models import Document, LearningEvent, Vocabulary, WordOccurrence
from backend.models.entities import utcnow
from backend.schemas import DashboardOut, DashboardSummary, DashboardWord, LearningProfile


class DashboardService:
    def __init__(self, session: Session, user_id: int = 1):
        self.session, self.user_id = session, user_id

    def read(self, now: datetime | None = None) -> DashboardOut:
        # A caller may reuse a session: a read must not flush pending changes.
        with self.session.no_autoflush:
            return self._read(now or utcnow())

    def _read(self, now: datetime) -> DashboardOut:
        week, month = now - timedelta(days=7), now - timedelta(days=30)
        unfamiliar = select(
            LearningEvent.lemma.label("lemma"),
            func.min(LearningEvent.created_at).label("first_unfamiliar_at"),
        ).where(
            LearningEvent.user_id == self.user_id,
            LearningEvent.event_type.in_(("MARK_UNKNOWN", "MARK_UNCERTAIN")),
            LearningEvent.created_at <= now,
        ).group_by(LearningEvent.lemma).subquery()
        recent_seen = select(
            WordOccurrence.lemma.label("lemma"),
            func.count(WordOccurrence.id).label("recent_seen_count"),
        ).join(Document, Document.id == WordOccurrence.document_id).where(
            Document.user_id == self.user_id,
            Document.created_at >= month,
            Document.created_at <= now,
        ).group_by(WordOccurrence.lemma).subquery()
        unresolved = Vocabulary.status.in_(("UNKNOWN", "UNCERTAIN"))
        seen_count = func.coalesce(recent_seen.c.recent_seen_count, 0)
        base = select(Vocabulary, seen_count, unfamiliar.c.first_unfamiliar_at).outerjoin(
            recent_seen, recent_seen.c.lemma == Vocabulary.lemma,
        ).outerjoin(unfamiliar, unfamiliar.c.lemma == Vocabulary.lemma).where(
            Vocabulary.user_id == self.user_id,
        )

        def items(filters, ordering):
            rows = self.session.execute(base.where(*filters).order_by(
                *ordering, Vocabulary.lemma.asc(), Vocabulary.id.asc(),
            ).limit(10))
            return [DashboardWord(vocabulary=entry, recent_seen_count=recent_count,
                                  first_unfamiliar_at=first_at)
                    for entry, recent_count, first_at in rows]

        counts = self.session.execute(select(
            func.count(Vocabulary.id).label("total"),
            func.count(case((Vocabulary.status == "KNOWN", 1))).label("known"),
            func.count(case((Vocabulary.status == "UNKNOWN", 1))).label("unknown"),
            func.count(case((Vocabulary.status == "UNCERTAIN", 1))).label("uncertain"),
            func.count(case((Vocabulary.status.is_(None), 1))).label("unassessed"),
            func.count(Vocabulary.mastery_score).label("scored_count"),
        ).where(Vocabulary.user_id == self.user_id)).mappings().one()
        new_unknown = self.session.scalar(select(func.count(Vocabulary.id)).join(
            unfamiliar, unfamiliar.c.lemma == Vocabulary.lemma,
        ).where(Vocabulary.user_id == self.user_id, unresolved,
                unfamiliar.c.first_unfamiliar_at >= week))
        lookups = self.session.scalar(select(func.count(LearningEvent.id)).where(
            LearningEvent.user_id == self.user_id, LearningEvent.event_type == "LOOKUP",
            LearningEvent.created_at >= week, LearningEvent.created_at <= now,
            LearningEvent.details["intentional"].as_boolean().is_(True),
        ))
        document_count = self.session.scalar(select(func.count(Document.id)).where(
            Document.user_id == self.user_id,
        ))
        assessed = counts["total"] - counts["unassessed"]
        return DashboardOut(
            summary=DashboardSummary(**counts, new_unknown_7d=new_unknown,
                                     lookups_7d=lookups, document_count=document_count),
            recent_frequent_unknown=items((unresolved, seen_count >= 2), (seen_count.desc(),)),
            repeated_lookups=items((Vocabulary.lookup_count >= 2,), (Vocabulary.lookup_count.desc(),)),
            recent_unknown=items((unresolved, unfamiliar.c.first_unfamiliar_at >= week),
                                 (unfamiliar.c.first_unfamiliar_at.desc(),)),
            longstanding_unknown=items((unresolved, unfamiliar.c.first_unfamiliar_at <= month),
                                      (unfamiliar.c.first_unfamiliar_at.asc(),)),
            lowest_mastery=items((Vocabulary.mastery_score.is_not(None),),
                                 (Vocabulary.mastery_score.asc(),)),
            most_seen_unknown=items((unresolved, Vocabulary.seen_count > 0),
                                    (Vocabulary.seen_count.desc(),)),
            most_queried=items((Vocabulary.lookup_count > 0,), (Vocabulary.lookup_count.desc(),)),
            profile=LearningProfile(assessed_count=assessed,
                                    assessment_coverage=assessed / counts["total"] if counts["total"] else 0,
                                    unscored_count=counts["total"] - counts["scored_count"]),
        )
