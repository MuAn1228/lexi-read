"""Deterministic personalized selection; replaceable independently of generation."""
import re

from backend.models import Document, WordOccurrence
from backend.repositories.vocabulary import VocabularyRepository
from backend.schemas import Recommendation, RecommendationsOut
from backend.services.llm import Candidate, LLMService
from backend.services.vocabulary import normalize_word


class VocabularyAgent:
    def __init__(self, repository: VocabularyRepository, llm: LLMService | None = None):
        self.repo, self.llm = repository, llm or LLMService()

    def rank(self, candidates: list[Candidate], current: str, article: str,
             vocabulary: list[dict], history: list[dict]) -> tuple[Recommendation, ...]:
        knowledge = {row["lemma"]: row for row in vocabulary}
        current = normalize_word(current)
        viewed = set()
        looked_up = set()
        for event in history:
            if event["event_type"] == "VIEW_RELATED":
                viewed.update(event.get("details", {}).get("recommended_words", []))
            if event["event_type"] == "LOOKUP":
                looked_up.add(event["word"])
        seen, ranked = {current}, []
        families = ({"general", "generally", "generalize", "generalization"},)
        for candidate in candidates:
            try:
                lemma = normalize_word(candidate.word)
            except ValueError:
                continue
            if lemma in seen or any(current in family and lemma in family for family in families):
                continue
            seen.add(lemma)
            row = knowledge.get(lemma, {})
            if row.get("status") == "KNOWN" or (row.get("mastery_score") or 0) >= .85:
                continue
            # Semantic/context relevance must clear a floor; unfamiliar alone is insufficient.
            if candidate.semantic_relevance < .35 or candidate.context_relevance < .35:
                continue
            unfamiliarity = {"UNKNOWN": 1.0, "UNCERTAIN": .8, None: .65}.get(row.get("status"), .65)
            mastery = row.get("mastery_score")
            if mastery is not None:
                unfamiliarity = 1 - mastery
            occurs = bool(re.search(r"(?<![a-z])" + re.escape(lemma) + r"(?![a-z])", article.lower()))
            context_score = min(1.0, candidate.context_relevance + (.08 if occurs else 0))
            score = (.30 * candidate.semantic_relevance + .25 * context_score
                     + .25 * unfamiliarity + .20 * candidate.learning_value)
            if lemma in looked_up:
                score += .025
            if lemma in viewed:
                score -= .06
            personal_reason = {"UNKNOWN": "你已标为不认识，适合在当前语境中补齐。",
                               "UNCERTAIN": "你对这个词还不确定，可以结合本文巩固。"}.get(row.get("status"), "")
            if lemma in viewed:
                personal_reason += "此前推荐过，本次结合阅读再次回顾。"
            ranked.append(Recommendation(word=lemma, meaning=candidate.meaning,
                                         reason=candidate.reason + personal_reason,
                                         score=round(max(0, min(1, score)), 4)))
        return tuple(sorted(ranked, key=lambda item: (-item.score, item.word))[:3])

    def recommend(self, occurrence: WordOccurrence, document: Document) -> RecommendationsOut:
        vocabulary = [{"lemma": row.lemma, "status": row.status, "mastery_score": row.mastery_score,
                       "seen_count": row.seen_count, "lookup_count": row.lookup_count}
                      for row in self.repo.all()]
        history = [{"word": row.word, "event_type": row.event_type, "details": row.details,
                    "created_at": row.created_at.isoformat()} for row in self.repo.history(100)]
        generated = self.llm.recommend_related_words(occurrence.lemma, occurrence.sentence,
                                                     document.ocr_text, vocabulary, history)
        selected = self.rank(generated.candidates, occurrence.lemma, document.ocr_text, vocabulary, history)
        self.repo.event(occurrence.lemma, "VIEW_RELATED", document.id,
                        {"recommended_words": [item.word for item in selected], "source": generated.source})
        self.repo.session.commit()
        return RecommendationsOut(items=selected, source=generated.source, warning=generated.warning)
