"""Deterministic personalized selection; replaceable independently of generation."""
import re
from contextlib import contextmanager
from fastapi import HTTPException
from sqlalchemy import text

from backend.models import Document, WordOccurrence
from backend.repositories.vocabulary import VocabularyRepository
from backend.schemas import EventOut, Recommendation, RecommendationsOut, Relation, VocabularyOut, VocabularyRecommendationsOut
from backend.services.llm import Candidate, GeneratedCandidates, LLMService, learner_data
from backend.public_limits import require_llm_budget
from backend.services.word_expansion_cache import candidates as expansion_candidates
from backend.services.vocabulary import normalize_word
from backend.services.documents import require_document, require_current_occurrence


class VocabularyAgent:
    def __init__(self, repository: VocabularyRepository, llm: LLMService | None = None):
        self.repo, self.llm = repository, llm or LLMService()

    def rank(self, candidates: list[Candidate], current: str, article: str,
             vocabulary: list[dict], history: list[dict],
             relation: Relation | None = None) -> tuple[Recommendation, ...]:
        knowledge = {row["lemma"]: row for row in vocabulary}
        current = normalize_word(current)
        viewed = set()
        looked_up = set()
        utility = {}
        for event in history:
            if (event["event_type"] in {"RECOMMENDATION_USEFUL", "RECOMMENDATION_NOT_USEFUL"}
                    and event.get("details", {}).get("source_lemma") == current):
                utility.setdefault(event.get("lemma", event["word"]), event["event_type"] == "RECOMMENDATION_USEFUL")
            if event["event_type"] in {"VIEW_RELATED", "RECOMMENDATION_GENERATED"}:
                viewed.update(event.get("details", {}).get("recommended_words", []))
                if event["event_type"] == "VIEW_RELATED":
                    target = event.get("details", {}).get("target_lemma")
                    if target:
                        viewed.add(target)
            if event["event_type"] == "LOOKUP":
                try:
                    looked_up.add(normalize_word(event.get("lemma") or event["word"]))
                except ValueError:
                    continue
        seen, ranked = {current}, []
        families = () if relation == Relation.WORD_FAMILY else (
            {"general", "generally", "generalize", "generalization"},)
        for candidate in candidates:
            if relation is not None and (candidate.relation != relation or not all(
                re.search(r"[\u3400-\u9fff]", value) for value in (candidate.meaning, candidate.reason)
            )):
                continue
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
            if lemma in utility:
                score += .08 if utility[lemma] else -.12
            personal_reason = ({"UNKNOWN": "你已标为不认识，适合在当前语境中补齐。",
                                "UNCERTAIN": "你对这个词还不确定，可以结合本文巩固。"} if article.strip() else
                               {"UNKNOWN": "你已标为不认识，可以结合词义与用法补齐。",
                                "UNCERTAIN": "你对这个词还不确定，可以比较词义与用法巩固。"}).get(row.get("status"), "")
            if lemma in viewed:
                personal_reason += "此前推荐过，本次结合阅读再次回顾。" if article.strip() else "此前推荐过，可以再次回顾。"
            if lemma in utility:
                personal_reason += ("你认为这组关联有帮助，已提高优先级。" if utility[lemma]
                                    else "你曾认为这组关联没有帮助，已降低优先级。")
            priority = round(max(0, min(1, score)), 4)
            ranked.append(Recommendation(word=lemma, meaning=candidate.meaning,
                                         reason=candidate.reason + personal_reason,
                                         relation=candidate.relation, priority=priority, score=priority))
        return tuple(sorted(ranked, key=lambda item: (-item.score, item.word))[:3])

    def build_context(self, occurrence: WordOccurrence, document: Document,
                      vocabulary: list[dict] | None = None, history: list[dict] | None = None) -> dict:
        require_document(self.repo.session, document.id, self.repo.user_id)
        if vocabulary is None:
            vocabulary = [VocabularyOut.model_validate(row).model_dump(mode="json") for row in self.repo.all()]
        if history is None:
            history = [EventOut.model_validate(row).model_dump(mode="json") for row in self.repo.history(100)]
        context = learner_data(occurrence.word, occurrence.lemma, occurrence.sentence, document.ocr_text,
                               vocabulary, history)
        last_mark = self.repo.last_mark(occurrence.lemma)
        context["current_word_history"] = {
            "vocabulary": next((row for row in vocabulary if row["lemma"] == occurrence.lemma), None),
            "last_mark": EventOut.model_validate(last_mark).model_dump(mode="json") if last_mark else None,
            "events": [EventOut.model_validate(row).model_dump(mode="json")
                       for row in self.repo.word_history(occurrence.lemma)],
        }
        return context

    def recommend(self, occurrence: WordOccurrence, document: Document,
                  relation: Relation | None = None) -> RecommendationsOut:
        vocabulary = [VocabularyOut.model_validate(row).model_dump(mode="json") for row in self.repo.all()]
        history = [EventOut.model_validate(row).model_dump(mode="json") for row in self.repo.history(100)]
        context = self.build_context(occurrence, document, vocabulary, history)
        return self._recommend(occurrence.word, occurrence.sentence, document.ocr_text,
                               vocabulary, history, context, relation, occurrence=occurrence, document=document)

    def recommend_vocabulary(self, word: str, relation: Relation, *, refresh: bool = False
                             ) -> VocabularyRecommendationsOut:
        lemma = normalize_word(word)
        entry = self.repo.get(lemma)
        if entry is None:
            raise HTTPException(404, "词库中未找到这个单词。")

        def generate():
            # The public budget protects actual provider attempts. Reusing facts
            # from disk or another concurrent request spends no extra AI quota.
            with contextmanager(require_llm_budget)(user_id=self.repo.user_id, session=self.repo.session):
                return self.llm.expand_vocabulary(entry.word, lemma, relation)

        generated, cached = expansion_candidates(self.llm.settings, self.repo.user_id, entry.word, lemma,
                                                  relation, generate, refresh=refresh)
        # Imported/saved words need not have an article occurrence. Do not
        # invent a sentence or associate the request with an unrelated document.
        result = self._recommend(entry.word, "", "", [], [], {}, relation, generated=generated)
        return VocabularyRecommendationsOut(**result.model_dump(), cached=cached)

    def _recommend(self, word: str, sentence: str, article: str, vocabulary: list[dict],
                   history: list[dict], context: dict, relation: Relation | None,
                   *, occurrence: WordOccurrence | None = None,
                   document: Document | None = None,
                   generated: GeneratedCandidates | None = None) -> RecommendationsOut:
        options = {"learner_context": context}
        if relation is not None:
            options["relation"] = relation
        if generated is None:
            generated = self.llm.recommend_related_words(word, sentence, article, vocabulary, history, **options)
        lemma = normalize_word(word)
        try:
            self.repo.session.execute(text("BEGIN IMMEDIATE"))
            if occurrence is not None and document is not None:
                require_current_occurrence(self.repo.session, occurrence.id, document.id, self.repo.user_id)
            elif self.repo.get(lemma) is None:
                raise HTTPException(404, "词库中未找到这个单词。")
            # A word can be marked known in another tab while the model runs.
            # Rank against committed knowledge before issuing the snapshot.
            self.repo.session.expire_all()
            current_vocabulary = [{"lemma": row.lemma, "status": row.status, "mastery_score": row.mastery_score}
                                  for row in self.repo.all()]
            utility_history = [EventOut.model_validate(row).model_dump(mode="json")
                               for row in self.repo.utility_history(lemma)]
            if occurrence is None:
                history = [EventOut.model_validate(row).model_dump(mode="json")
                           for row in self.repo.history(100)]
            selected = self.rank(generated.candidates, lemma, article, current_vocabulary,
                                 utility_history + history, relation)
            details = {"recommended_words": [item.word for item in selected], "source": generated.source,
                       "items": [item.model_dump(mode="json") for item in selected]}
            if relation is not None:
                details["requested_relation"] = relation.value
            batch = self.repo.event(word, "RECOMMENDATION_GENERATED", document.id if document else None, details)
            self.repo.session.commit()
        except Exception:
            self.repo.session.rollback()
            raise
        return RecommendationsOut(batch_id=batch.id, items=selected, source=generated.source, warning=generated.warning)
