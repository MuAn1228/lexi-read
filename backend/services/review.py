"""Read-only review candidates from each user's existing learning records."""
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from backend.models import Document, LearningEvent, Vocabulary, WordOccurrence
from backend.schemas import ReviewItem, ReviewOccurrence, ReviewOut


class ReviewService:
    def __init__(self, session: Session, user_id: int = 1):
        self.session, self.user_id = session, user_id

    def read(self, limit: int = 10) -> ReviewOut:
        # A shared session may contain unrelated pending writes. Reading the
        # queue must never flush those writes or record a review impression.
        with self.session.no_autoflush:
            return self._read(limit)

    def _read(self, limit: int) -> ReviewOut:
        candidates = (
            Vocabulary.user_id == self.user_id,
            Vocabulary.status.in_(("UNKNOWN", "UNCERTAIN")),
        )
        total = self.session.scalar(select(func.count(Vocabulary.id)).where(*candidates))
        reviewed = select(
            LearningEvent.lemma.label("lemma"),
            func.max(LearningEvent.created_at).label("last_reviewed_at"),
        ).where(
            LearningEvent.user_id == self.user_id,
            LearningEvent.event_type.in_(("MARK_KNOWN", "MARK_UNKNOWN", "MARK_UNCERTAIN")),
            LearningEvent.details["origin"].as_string() == "review",
        ).group_by(LearningEvent.lemma).subquery()
        rows = list(self.session.execute(select(
            Vocabulary, reviewed.c.last_reviewed_at,
        ).outerjoin(reviewed, reviewed.c.lemma == Vocabulary.lemma).where(
            *candidates,
        ).order_by(
            case((reviewed.c.last_reviewed_at.is_(None), 0), else_=1),
            reviewed.c.last_reviewed_at.asc(),
            func.coalesce(Vocabulary.mastery_score, .5).asc(),
            Vocabulary.seen_count.desc(),
            Vocabulary.lemma.asc(), Vocabulary.id.asc(),
        ).limit(limit)))
        if not rows:
            return ReviewOut(total=total, items=[])

        # Only fetch context for the bounded batch. A recommendation may have
        # a source document without ever occurring there, so match the actual
        # lemma in the user's documents instead of using an event's document.
        occurrences = select(
            WordOccurrence.lemma.label("lemma"), WordOccurrence.id.label("id"),
            Document.id.label("document_id"), Document.title.label("document_title"),
            WordOccurrence.word.label("word"), WordOccurrence.sentence.label("sentence"),
            func.row_number().over(
                partition_by=WordOccurrence.lemma,
                order_by=(Document.created_at.desc(), Document.id.desc(), WordOccurrence.id.desc()),
            ).label("position"),
        ).join(Document, Document.id == WordOccurrence.document_id).where(
            Document.user_id == self.user_id,
            WordOccurrence.lemma.in_([entry.lemma for entry, _ in rows]),
        ).subquery()
        contexts = {
            row["lemma"]: ReviewOccurrence(**{key: row[key] for key in (
                "id", "document_id", "document_title", "word", "sentence",
            )})
            for row in self.session.execute(select(occurrences).where(
                occurrences.c.position == 1,
            )).mappings()
        }
        return ReviewOut(total=total, items=[
            ReviewItem(vocabulary=entry, occurrence=contexts.get(entry.lemma), last_reviewed_at=last_at)
            for entry, last_at in rows
        ])
