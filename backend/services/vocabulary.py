from backend.models.entities import utcnow
from backend.repositories.vocabulary import VocabularyRepository
from backend.schemas import Status
from backend.services.lemmatization import normalize_word


class VocabularyService:
    def __init__(self, repository: VocabularyRepository):
        self.repo = repository

    def observe(self, word: str, lemma: str, count: int = 1):
        entry = self.repo.ensure(word, normalize_word(lemma))
        entry.seen_count += count
        now = utcnow()
        if count > 0:
            entry.first_seen_at = entry.first_seen_at or now
            entry.last_seen_at = now
        return entry

    def mark(self, word: str, status: Status, document_id: int | None = None):
        lemma = normalize_word(word)
        entry = self.repo.ensure(word.strip(), lemma)
        previous = entry.status
        entry.status = status.value
        # Reserved for a future calibrated model. A label is not a probability.
        entry.mastery_score = None
        entry.updated_at = utcnow()
        self.repo.event(word.strip(), f"MARK_{status.value}", document_id, {"previous_status": previous})
        self.repo.session.commit()
        return entry

    def lookup(self, word: str, document_id: int, source: str):
        entry = self.repo.ensure(word, normalize_word(word))
        entry.lookup_count += 1
        self.repo.event(word.strip(), "LOOKUP", document_id, {"source": source})
        self.repo.session.commit()
