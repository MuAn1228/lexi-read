from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import LearningEvent, Vocabulary


class VocabularyRepository:
    def __init__(self, session: Session, user_id: int = 1):
        self.session, self.user_id = session, user_id

    def get(self, lemma: str) -> Vocabulary | None:
        return self.session.scalar(select(Vocabulary).where(
            Vocabulary.user_id == self.user_id, Vocabulary.lemma == lemma))

    def all(self) -> list[Vocabulary]:
        return list(self.session.scalars(select(Vocabulary).where(
            Vocabulary.user_id == self.user_id).order_by(Vocabulary.updated_at.desc(), Vocabulary.id.desc())))

    def ensure(self, word: str, lemma: str) -> Vocabulary:
        entry = self.get(lemma)
        if entry is None:
            entry = Vocabulary(user_id=self.user_id, word=word, lemma=lemma,
                               status=None, mastery_score=None, seen_count=0, lookup_count=0)
            self.session.add(entry)
            self.session.flush()
        return entry

    def event(self, word: str, event_type: str, document_id: int | None = None,
              details: dict | None = None) -> LearningEvent:
        record = LearningEvent(user_id=self.user_id, word=word, event_type=event_type,
                               document_id=document_id, details=details or {})
        self.session.add(record)
        return record

    def history(self, limit: int = 100) -> list[LearningEvent]:
        return list(self.session.scalars(select(LearningEvent).where(
            LearningEvent.user_id == self.user_id).order_by(
                LearningEvent.created_at.desc(), LearningEvent.id.desc()).limit(limit)))
