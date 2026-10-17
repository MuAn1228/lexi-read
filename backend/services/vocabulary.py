from backend.models.entities import LearningEvent, utcnow
from backend.repositories.vocabulary import VocabularyRepository
from backend.schemas import Status
from backend.services.lemmatization import normalize_word
from backend.services.mastery import MasteryService


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

    def mark(self, word: str, status: Status, document_id: int | None = None, *,
             commit: bool = True, metadata: dict | None = None,
             recorded_events: list[LearningEvent] | None = None):
        lemma = normalize_word(word)
        entry = self.repo.ensure(word.strip(), lemma)
        previous = entry.status
        entry.status = status.value
        event_type = f"MARK_{status.value}"
        details = MasteryService.event_details(entry.mastery_score, event_type)
        entry.mastery_score = details["mastery_after"]
        entry.updated_at = utcnow()
        event = self.repo.event(word.strip(), event_type, document_id,
                               {**(metadata or {}), "previous_status": previous, **details})
        if recorded_events is not None:
            recorded_events.append(event)
        if commit:
            self.repo.session.commit()
        return entry

    def lookup(self, word: str, document_id: int, source: str):
        entry = self.repo.ensure(word, normalize_word(word))
        entry.lookup_count += 1
        details = MasteryService.event_details(entry.mastery_score, "LOOKUP")
        entry.mastery_score = details["mastery_after"]
        entry.updated_at = utcnow()
        self.repo.event(word.strip(), "LOOKUP", document_id,
                        {"source": source, "intentional": True, **details})
        self.repo.session.commit()
