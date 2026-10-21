from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import LearningEvent, Vocabulary
from backend.services.lemmatization import normalize_word


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
        record = LearningEvent(user_id=self.user_id, word=word, lemma=normalize_word(word), event_type=event_type,
                               document_id=document_id, details=details or {})
        self.session.add(record)
        return record

    def history(self, limit: int = 100) -> list[LearningEvent]:
        return list(self.session.scalars(select(LearningEvent).where(
            LearningEvent.user_id == self.user_id,
            LearningEvent.event_type != "IMPORT_MAIMEMO").order_by(
                LearningEvent.created_at.desc(), LearningEvent.id.desc()).limit(limit)))

    def word_history(self, lemma: str, limit: int = 20) -> list[LearningEvent]:
        return list(self.session.scalars(select(LearningEvent).where(
            LearningEvent.user_id == self.user_id, LearningEvent.lemma == lemma,
            LearningEvent.event_type != "IMPORT_MAIMEMO").order_by(
                LearningEvent.created_at.desc(), LearningEvent.id.desc()).limit(limit)))

    def utility_history(self, source_lemma: str) -> list[LearningEvent]:
        # Keep pair-specific feedback even after unrelated events push it out
        # of the model's recent-history window. Rank uses only each latest pair.
        return list(self.session.scalars(select(LearningEvent).where(
            LearningEvent.user_id == self.user_id,
            LearningEvent.event_type.in_(("RECOMMENDATION_USEFUL", "RECOMMENDATION_NOT_USEFUL")),
            LearningEvent.details["source_lemma"].as_string() == source_lemma,
        ).order_by(LearningEvent.created_at.desc(), LearningEvent.id.desc())))

    def last_mark(self, lemma: str) -> LearningEvent | None:
        # Query independently: many queries/recommendations may follow a mark.
        return self.session.scalar(select(LearningEvent).where(
            LearningEvent.user_id == self.user_id, LearningEvent.lemma == lemma,
            LearningEvent.event_type.in_(("MARK_KNOWN", "MARK_UNKNOWN", "MARK_UNCERTAIN")),
        ).order_by(LearningEvent.created_at.desc(), LearningEvent.id.desc()).limit(1))

    def last_maimemo_import(self, lemma: str) -> LearningEvent | None:
        return self.session.scalar(select(LearningEvent).where(
            LearningEvent.user_id == self.user_id, LearningEvent.lemma == lemma,
            LearningEvent.event_type == "IMPORT_MAIMEMO",
        ).order_by(LearningEvent.id.desc()).limit(1))
