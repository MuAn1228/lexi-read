import re

from backend.models.entities import utcnow
from backend.repositories.vocabulary import VocabularyRepository
from backend.schemas import Status

# Deliberately conservative: derivations (generalize/generalization) are distinct.
# This seam can later be replaced by a contextual morphological model.
INFLECTIONS = {
    "models": "model", "datasets": "dataset", "samples": "sample", "features": "feature",
    "predictions": "prediction", "distributions": "distribution", "hypotheses": "hypothesis",
    "algorithms": "algorithm", "parameters": "parameter", "observations": "observation",
    "examples": "example", "errors": "error", "results": "result", "methods": "method",
    "studies": "study", "was": "be", "were": "be", "is": "be", "are": "be",
    "has": "have", "had": "have",
}


def normalize_word(word: str) -> str:
    normalized = re.sub(r"\s+", " ", word.strip().lower().replace("’", "'").replace("–", "-"))
    if not re.fullmatch(r"[a-z]+(?:[' -][a-z]+)*", normalized) or len(normalized) > 150:
        raise ValueError("请输入有效英文单词或短语（最多 150 个字符）。")
    return INFLECTIONS.get(normalized, normalized)


class VocabularyService:
    def __init__(self, repository: VocabularyRepository):
        self.repo = repository

    def observe(self, word: str, lemma: str, count: int = 1):
        entry = self.repo.ensure(word, lemma)
        entry.seen_count += count
        entry.last_seen_at = utcnow()
        return entry

    def mark(self, word: str, status: Status, document_id: int | None = None):
        lemma = normalize_word(word)
        entry = self.repo.ensure(lemma, lemma)
        previous = entry.status
        entry.status = status.value
        # Reserved for a future calibrated model. A label is not a probability.
        entry.mastery_score = None
        entry.updated_at = utcnow()
        self.repo.event(lemma, f"MARK_{status.value}", document_id, {"previous_status": previous})
        self.repo.session.commit()
        return entry

    def lookup(self, word: str, document_id: int, source: str):
        entry = self.repo.ensure(word, normalize_word(word))
        entry.lookup_count += 1
        self.repo.event(entry.lemma, "LOOKUP", document_id, {"source": source})
        self.repo.session.commit()
