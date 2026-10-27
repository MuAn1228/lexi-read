"""Explicit learning state for stable, offline dictionary senses."""
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models import LearningEvent, SenseReviewReceipt, VocabularySense
from backend.models.entities import utcnow
from backend.repositories.senses import SenseRepository
from backend.repositories.vocabulary import VocabularyRepository
from backend.schemas import (SenseMarkRequest, Status, VocabularySenseOut,
                             VocabularySensesOut)
from backend.services.adaptive_review import ReviewProfile, SCHEDULE_VERSION, memory_state
from backend.services.documents import require_document
from backend.services.lemmatization import normalize_word
from backend.services.mastery import MasteryService
from backend.services.offline_dictionary import OfflineDictionaryUnavailable, sense_catalog
from backend.services.scheduling import KNOWN_DAYS, next_intervals, utc


SENSE_REVIEW_EVENTS = ("SENSE_MARK_KNOWN", "SENSE_MARK_UNKNOWN", "SENSE_MARK_UNCERTAIN")


def sense_output(source: VocabularySense | dict, *, available: bool) -> VocabularySenseOut:
    if isinstance(source, dict):
        return VocabularySenseOut(**source, available=available)
    return VocabularySenseOut(
        id=source.id, sense_key=source.sense_key, source=source.source,
        source_revision=source.source_revision, headword=source.headword,
        source_ordinal=source.source_ordinal, part_of_speech=source.part_of_speech,
        meaning=source.meaning, available=available, status=source.status,
        mastery_score=source.mastery_score, due_at=source.due_at,
        known_streak=source.known_streak, created_at=source.created_at,
        updated_at=source.updated_at,
    )


def read_sense_profiles(session: Session, user_id: int, sense_ids: list[int], *, per_sense: int = 8
                        ) -> dict[int, ReviewProfile]:
    """Read bounded review-only histories without mixing sibling senses or whole words."""
    if not sense_ids:
        return {}
    unique = list(dict.fromkeys(sense_ids))
    rows = session.execute(select(LearningEvent.event_type, LearningEvent.details).where(
        LearningEvent.user_id == user_id,
        LearningEvent.event_type.in_(SENSE_REVIEW_EVENTS),
        LearningEvent.details["origin"].as_string() == "review",
        LearningEvent.details["sense_id"].as_integer().in_(unique),
    ).order_by(LearningEvent.created_at.desc(), LearningEvent.id.desc()).limit(
        min(1600, max(len(unique) * per_sense * 3, 50)),
    )).all()
    grouped: dict[int, list[tuple[str, int | None]]] = {sense_id: [] for sense_id in unique}
    for event_type, details in rows:
        if not isinstance(details, dict) or type(details.get("sense_id")) is not int:
            continue
        sense_id = details["sense_id"]
        values = grouped.get(sense_id)
        if values is None or len(values) >= per_sense:
            continue
        response = details.get("response_ms")
        values.append((event_type.removeprefix("SENSE_"),
                       response if type(response) is int and 500 <= response <= 600_000 else None))
    return {sense_id: ReviewProfile(tuple(event for event, _ in values),
                                    tuple(response for _, response in values))
            for sense_id, values in grouped.items()}


class SenseLearningService:
    def __init__(self, session: Session, user_id: int = 1):
        self.session, self.user_id = session, user_id
        self.vocabulary = VocabularyRepository(session, user_id)
        self.senses = SenseRepository(session, user_id)

    def catalog(self, word: str) -> VocabularySensesOut:
        # A GET must not flush unrelated pending state. When the immutable
        # dictionary is temporarily unavailable, durable snapshots stay visible.
        with self.session.no_autoflush:
            lemma = normalize_word(word)
            entry = self.vocabulary.get(lemma)
            try:
                current = sense_catalog(word, lemma)
            except OfflineDictionaryUnavailable:
                current = []
            tracked = self.senses.for_vocabulary(entry.id) if entry is not None else []
            by_key = {item.sense_key: item for item in tracked}
            result: list[VocabularySenseOut] = []
            current_keys: set[str] = set()
            for item in current:
                current_keys.add(item["sense_key"])
                saved = by_key.get(item["sense_key"])
                result.append(sense_output(saved if saved is not None else item, available=True))
            result.extend(sense_output(item, available=False) for item in tracked
                          if item.sense_key not in current_keys)
            return VocabularySensesOut(word=word.strip(), lemma=lemma, vocabulary=entry, senses=result)

    def mark(self, word: str, sense_key: str, payload: SenseMarkRequest,
             *, now: datetime | None = None) -> VocabularySenseOut:
        try:
            self.session.execute(text("BEGIN IMMEDIATE"))
            result = self._mark(word, sense_key, payload, now=now)
            self.session.commit()
            return result
        except IntegrityError:
            self.session.rollback()
            # BEGIN IMMEDIATE normally serializes contenders. The unique receipt
            # remains the final guard on other SQLite-compatible configurations.
            if payload.request_id is None:
                raise
            return self._retry_after_integrity(word, sense_key, payload)
        except Exception:
            self.session.rollback()
            raise

    def _mark(self, word: str, sense_key: str, payload: SenseMarkRequest,
              *, now: datetime | None) -> VocabularySenseOut:
        lemma = normalize_word(word)
        if payload.document_id is not None:
            require_document(self.session, payload.document_id, self.user_id)
        vocabulary = self.vocabulary.ensure(word.strip(), lemma)
        sense = self.senses.by_key(vocabulary.id, sense_key)
        created = sense is None
        if sense is None:
            try:
                definition = next((item for item in sense_catalog(word, lemma)
                                   if item["sense_key"] == sense_key), None)
            except OfflineDictionaryUnavailable as exc:
                raise HTTPException(503, str(exc)) from exc
            if definition is None:
                raise HTTPException(404, "该释义项不存在或词典来源已更新，请刷新释义后重试。")
            sense = VocabularySense(
                user_id=self.user_id, vocabulary_id=vocabulary.id, **definition,
                status=payload.status.value, mastery_score=MasteryService.INITIAL_SCORE,
                due_at=utc(now or utcnow()), known_streak=0,
            )
            self.session.add(sense)
            self.session.flush()

        if payload.request_id is not None:
            receipt = self.senses.receipt(str(payload.request_id))
            if receipt is not None:
                self._validate_retry(receipt, sense, payload)
                return sense_output(sense, available=self._available(word, lemma, sense.sense_key))

        timestamp = utc(now or utcnow())
        previous = None if created else sense.status
        mastery = MasteryService.event_details(
            None if created else sense.mastery_score, f"MARK_{payload.status.value}",
        )
        profile = (read_sense_profiles(self.session, self.user_id, [sense.id]).get(sense.id, ReviewProfile())
                   if payload.origin == "review" else ReviewProfile())
        if payload.origin == "review":
            intervals = next_intervals(sense.known_streak, mastery["mastery_before"], profile)
            seconds = intervals[payload.status]
            streak = min(sense.known_streak + 1, len(KNOWN_DAYS)) if payload.status == Status.KNOWN else 0
            if payload.status == Status.UNKNOWN:
                reason = "暂时遗忘，10 分钟后快速重试"
            elif payload.status == Status.UNCERTAIN:
                reason = f"仍有些模糊，{seconds // 86400} 天后再次确认"
            elif profile.failure_ratio >= .2:
                reason = "近期有遗忘，已缩短巩固间隔"
            else:
                reason = "根据这个释义项的记忆阶段安排下次复习"
            schedule = {
                "schedule_version": SCHEDULE_VERSION,
                "review_interval_seconds": seconds,
                "adaptive_reason": reason,
                "memory_state": memory_state(payload.status.value, streak, profile),
            }
        else:
            seconds = 3 * 86400 if payload.status == Status.KNOWN else 0
            streak = 0
            schedule = {}

        sense.status = payload.status.value
        sense.mastery_score = mastery["mastery_after"]
        sense.known_streak = streak
        sense.due_at = timestamp + timedelta(seconds=seconds)
        sense.updated_at = timestamp
        details = {
            "origin": "review" if payload.origin == "review" else "sense",
            "sense_id": sense.id,
            "sense_key": sense.sense_key,
            "sense_source": sense.source,
            "sense_meaning": sense.meaning,
            "previous_status": previous,
            **mastery,
            **schedule,
            "due_at": sense.due_at.isoformat(),
        }
        if payload.request_id is not None:
            key = str(payload.request_id)
            details["request_id"] = key
            self.session.add(SenseReviewReceipt(
                user_id=self.user_id, request_id=key, sense_id=sense.id,
                status=payload.status.value, document_id=payload.document_id,
                response_ms=payload.response_ms, plan_minutes=payload.plan_minutes,
                created_at=timestamp,
            ))
        if payload.response_ms is not None:
            details["response_ms"] = payload.response_ms
        if payload.plan_minutes is not None:
            details["plan_minutes"] = payload.plan_minutes
        self.session.add(LearningEvent(
            user_id=self.user_id, word=word.strip(), lemma=lemma,
            event_type=f"SENSE_MARK_{payload.status.value}", document_id=payload.document_id,
            details=details, created_at=timestamp,
        ))
        self.session.flush()
        return sense_output(sense, available=self._available(word, lemma, sense.sense_key))

    def _available(self, word: str, lemma: str, sense_key: str) -> bool:
        try:
            return any(item["sense_key"] == sense_key for item in sense_catalog(word, lemma))
        except OfflineDictionaryUnavailable:
            return False

    @staticmethod
    def _validate_retry(receipt: SenseReviewReceipt, sense: VocabularySense,
                        payload: SenseMarkRequest) -> None:
        actual = (receipt.sense_id, receipt.status, receipt.document_id,
                  receipt.response_ms, receipt.plan_minutes)
        requested = (sense.id, payload.status.value, payload.document_id,
                     payload.response_ms, payload.plan_minutes)
        if actual != requested:
            raise HTTPException(409, "该请求编号已用于另一条义项复习标记。")

    def _retry_after_integrity(self, word: str, sense_key: str,
                               payload: SenseMarkRequest) -> VocabularySenseOut:
        lemma = normalize_word(word)
        vocabulary = self.vocabulary.get(lemma)
        sense = self.senses.by_key(vocabulary.id, sense_key) if vocabulary is not None else None
        receipt = self.senses.receipt(str(payload.request_id))
        if sense is None or receipt is None:
            raise HTTPException(409, "义项复习请求发生冲突，请刷新后重试。")
        self._validate_retry(receipt, sense, payload)
        return sense_output(sense, available=self._available(word, lemma, sense.sense_key))
