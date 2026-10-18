"""Atomic, retry-safe feedback against a server-issued recommendation snapshot."""
from fastapi import HTTPException
from sqlalchemy import select, text

from backend.models import FeedbackReceipt, LearningEvent, RecommendationFeedback
from backend.models.entities import utcnow
from backend.repositories.vocabulary import VocabularyRepository
from backend.schemas import FeedbackOut, FeedbackRequest, Status
from backend.services.lemmatization import normalize_word
from backend.services.relations import RelationService
from backend.services.vocabulary import VocabularyService
from backend.services.documents import require_document


class FeedbackService:
    def __init__(self, repository: VocabularyRepository):
        self.repo, self.session = repository, repository.session

    def apply(self, batch_id: int, request: FeedbackRequest) -> FeedbackOut:
        try:
            word = normalize_word(request.word)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        # Reserve SQLite's single writer before any read. Concurrent requests
        # then see committed receipts/state instead of racing a read-then-write.
        try:
            self.session.execute(text("BEGIN IMMEDIATE"))
            result = self._apply(batch_id, word, request)
            self.session.commit()
            return result
        except Exception:
            self.session.rollback()
            raise

    def _apply(self, batch_id: int, word: str, request: FeedbackRequest) -> FeedbackOut:
        uid = self.repo.user_id
        batch = self.session.scalar(select(LearningEvent).where(
            LearningEvent.id == batch_id, LearningEvent.user_id == uid,
            LearningEvent.event_type == "RECOMMENDATION_GENERATED"))
        if batch is None:
            raise HTTPException(404, "未找到该推荐批次。")
        offered = next((item for item in batch.details.get("items", []) if item.get("word") == word), None)
        if offered is None:
            raise HTTPException(422, "该词不在这次推荐中，请重新生成推荐。")
        receipt = self.session.scalar(select(FeedbackReceipt).where(
            FeedbackReceipt.user_id == uid, FeedbackReceipt.request_id == str(request.request_id)))
        if receipt and (receipt.batch_id, receipt.word, receipt.action) != (batch_id, word, request.action):
            raise HTTPException(409, "该请求编号已用于另一条反馈。")
        state = self.session.scalar(select(RecommendationFeedback).where(
            RecommendationFeedback.user_id == uid, RecommendationFeedback.batch_id == batch_id,
            RecommendationFeedback.word == word))
        if receipt:
            # Return current state: a delayed retry must never replay an earlier
            # choice over a newer one or apply its mastery delta twice.
            return self._out(state)
        if batch.document_id is not None:
            require_document(self.session, batch.document_id, uid)
        if state is None:
            state = RecommendationFeedback(user_id=uid, batch_id=batch_id, word=word,
                                           shown=False, viewed=False, added_unknown=False)
            self.session.add(state)
        metadata = {"batch_id": batch_id, "source_word": batch.word, "source_lemma": batch.lemma,
                    "target_lemma": word, "relation": offered["relation"],
                    "priority": offered["priority"], "source": "recommendation_feedback"}
        changed = False
        recorded_events = []
        if not state.shown:
            state.shown = True
            recorded_events.append(self.repo.event(word, "RECOMMENDATION_SHOWN", batch.document_id, metadata))
            changed = True
        if request.action == "VIEWED" and not state.viewed:
            state.viewed = True
            recorded_events.append(self.repo.event(word, "VIEW_RELATED", batch.document_id, metadata))
            changed = True
        elif request.action in {"KNOWN", "UNKNOWN"}:
            entry = self.repo.get(word)
            if state.status != request.action or entry is None or entry.status != request.action:
                state.added_unknown = state.added_unknown or (
                    request.action == "UNKNOWN" and (entry is None or entry.status != "UNKNOWN"))
                VocabularyService(self.repo).mark(word, Status(request.action), batch.document_id,
                                                   commit=False, metadata=metadata, recorded_events=recorded_events)
                state.status = request.action
                changed = True
        elif request.action in {"USEFUL", "NOT_USEFUL"}:
            useful = request.action == "USEFUL"
            if state.useful != useful:
                state.useful = useful
                recorded_events.append(self.repo.event(word, "RECOMMENDATION_" + request.action,
                                                      batch.document_id, metadata))
                changed = True
        if changed:
            state.updated_at = utcnow()
        self.session.add(FeedbackReceipt(user_id=uid, request_id=str(request.request_id),
                                        batch_id=batch_id, word=word, action=request.action))
        self.session.flush()
        relations = RelationService(self.session, uid)
        for event in recorded_events:
            relations.apply_event(event)
        return self._out(state)

    def _out(self, state: RecommendationFeedback) -> FeedbackOut:
        return FeedbackOut(batch_id=state.batch_id, word=state.word, shown=state.shown,
                           viewed=state.viewed, status=state.status, useful=state.useful,
                           vocabulary=self.repo.get(state.word))
