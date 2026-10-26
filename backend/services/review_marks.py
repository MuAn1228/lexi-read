"""Apply explicit review choices once, including after a lost HTTP response."""
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from backend.models import LearningEvent, ReviewReceipt, Vocabulary
from backend.repositories.vocabulary import VocabularyRepository
from backend.schemas import Status
from backend.services.adaptive_review import SCHEDULE_VERSION
from backend.services.documents import require_document
from backend.services.lemmatization import normalize_word
from backend.services.vocabulary import VocabularyService


class ReviewMarkService:
    def __init__(self, repository: VocabularyRepository):
        self.repo, self.session = repository, repository.session

    def apply(self, word: str, status: Status, document_id: int | None, request_id: UUID,
              *, response_ms: int | None = None, plan_minutes: int | None = None) -> Vocabulary:
        lemma = normalize_word(word)
        key = str(request_id)
        try:
            # Serialize SQLite writers before inspecting a receipt or updating
            # mastery. Authentication may already have read the DB.
            self.session.execute(text("BEGIN IMMEDIATE"))
            receipt = self._receipt(key)
            if receipt is not None:
                result = self._replay(receipt, lemma, status, document_id, response_ms, plan_minutes)
            else:
                if document_id is not None:
                    require_document(self.session, document_id, self.repo.user_id)
                self._current(lemma)
                self.session.add(ReviewReceipt(user_id=self.repo.user_id, request_id=key,
                                               lemma=lemma, status=status.value, document_id=document_id))
                self.session.flush()
                result = VocabularyService(self.repo).mark(
                    word, status, document_id, commit=False,
                    metadata={"origin": "review", "request_id": key,
                              **({"response_ms": response_ms} if response_ms is not None else {}),
                              **({"plan_minutes": plan_minutes} if plan_minutes is not None else {})},
                )
            self.session.commit()
            return result
        except IntegrityError:
            self.session.rollback()
            # A unique receipt also protects against another writer committing
            # this key first. Only that exact retry may consume its receipt;
            # unrelated database integrity errors must still propagate.
            receipt = self._receipt(key)
            if receipt is None:
                raise
            try:
                result = self._replay(receipt, lemma, status, document_id, response_ms, plan_minutes)
                self.session.commit()
                return result
            except Exception:
                self.session.rollback()
                raise
        except Exception:
            self.session.rollback()
            raise

    def _receipt(self, request_id: str) -> ReviewReceipt | None:
        return self.session.scalar(select(ReviewReceipt).where(
            ReviewReceipt.user_id == self.repo.user_id, ReviewReceipt.request_id == request_id,
        ))

    def _replay(self, receipt: ReviewReceipt, lemma: str, status: Status, document_id: int | None,
                response_ms: int | None, plan_minutes: int | None) -> Vocabulary:
        if (receipt.lemma, receipt.status, receipt.document_id) != (lemma, status.value, document_id):
            raise HTTPException(409, "该请求编号已用于另一条复习标记。")
        # V2 keeps timing outside the receipt table to avoid a schema migration,
        # but it is still part of the idempotent request. Compare it with the
        # event written in the same transaction. Legacy pre-V2 events contain
        # neither field and remain replayable after an upgrade.
        original = self.session.scalar(select(LearningEvent).where(
            LearningEvent.user_id == self.repo.user_id,
            LearningEvent.lemma == lemma,
            LearningEvent.event_type.in_(("MARK_KNOWN", "MARK_UNKNOWN", "MARK_UNCERTAIN")),
            LearningEvent.details["origin"].as_string() == "review",
            LearningEvent.details["request_id"].as_string() == receipt.request_id,
        ).order_by(LearningEvent.id.desc()).limit(1))
        details = original.details if original is not None and isinstance(original.details, dict) else {}
        is_adaptive_request = details.get("schedule_version") == SCHEDULE_VERSION or (
            "response_ms" in details or "plan_minutes" in details)
        if is_adaptive_request and (
                details.get("response_ms") != response_ms or details.get("plan_minutes") != plan_minutes):
            raise HTTPException(409, "该请求编号的复习耗时或学习计划与首次提交不一致。")
        # A delayed retry returns current state, even after another choice has
        # changed the word. It must never replay the old mastery delta/status.
        entry = self._current(lemma)
        if entry is None:
            raise HTTPException(409, "该复习记录对应的词条已不存在，请刷新词库。")
        return entry

    def _current(self, lemma: str) -> Vocabulary | None:
        # Explicit refresh also keeps reused sessions from applying a delta to
        # cached state after another request has changed this vocabulary row.
        return self.session.scalar(select(Vocabulary).where(
            Vocabulary.user_id == self.repo.user_id, Vocabulary.lemma == lemma,
        ).execution_options(populate_existing=True))
