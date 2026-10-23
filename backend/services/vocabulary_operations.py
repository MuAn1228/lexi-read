"""Atomic, version-checked bulk marks with conservative, auditable undo."""
from datetime import datetime, timedelta
import hashlib
import json
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from backend.models import LearningEvent, ReviewSchedule, Vocabulary
from backend.models.entities import utcnow
from backend.models.vocabulary_operations import VocabularyOperation
from backend.repositories.vocabulary import VocabularyRepository
from backend.schemas import Status
from backend.services.scheduling import utc
from backend.services.vocabulary import VocabularyService


def _timestamp(value: datetime | None) -> str | None:
    return utc(value).isoformat() if value is not None else None


class VocabularyBatchService:
    def __init__(self, repository: VocabularyRepository):
        self.repo, self.session = repository, repository.session

    def apply(self, request_id: UUID, status: Status, items: list[tuple[int, datetime]]) -> dict:
        # Canonicalize both order and timezone: an HTTP retry may legitimately
        # serialize UTC as either Z, +00:00, or SQLite's naive UTC value.
        if not 1 <= len(items) <= 200 or len({item[0] for item in items}) != len(items):
            raise HTTPException(422, "请选择 1 到 200 个不重复的词条。")
        key = str(request_id)
        canonical = {"status": status.value, "items": sorted(
            [(entry_id, _timestamp(version)) for entry_id, version in items])}
        digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        try:
            self.session.execute(text("BEGIN IMMEDIATE"))
            receipt = self._receipt(key)
            if receipt is not None:
                self._require_payload(receipt, digest)
            else:
                entries = self._entries([entry_id for entry_id, _ in items])
                by_id = {entry.id: entry for entry in entries}
                for entry_id, version in items:
                    if _timestamp(by_id[entry_id].updated_at) != _timestamp(version):
                        raise HTTPException(409, "所选词条已发生变化，请刷新词库后重新选择。")
                snapshots = []
                now = self._next_version(entries)
                for entry_id, _ in items:
                    entry = by_id[entry_id]
                    before = self._state(entry)
                    changed = entry.status != status.value
                    events: list[LearningEvent] = []
                    if changed:
                        VocabularyService(self.repo).mark(
                            entry.lemma, status, commit=False, now=now,
                            metadata={"origin": "vocabulary_bulk", "operation_id": key},
                            recorded_events=events,
                        )
                        self.session.flush()
                    snapshots.append({"id": entry.id, "lemma": entry.lemma, "changed": changed,
                                      "mark_event_id": events[0].id if events else None,
                                      "before": before, "after": self._state(entry)})
                receipt = VocabularyOperation(user_id=self.repo.user_id, request_id=key,
                                               payload_hash=digest, snapshots=snapshots, undone=False,
                                               created_at=now)
                self.session.add(receipt)
                self.session.flush()
            result = self._result(receipt)
            self.session.commit()
            return result
        except IntegrityError:
            self.session.rollback()
            # The writer reservation serializes SQLite. Keep a receipt fallback
            # as an extra guard if another database writer wins the unique key.
            receipt = self._receipt(key)
            if receipt is None:
                raise
            try:
                self._require_payload(receipt, digest)
                result = self._result(receipt)
                self.session.commit()
                return result
            except Exception:
                self.session.rollback()
                raise
        except Exception:
            self.session.rollback()
            raise

    def undo(self, operation_id: UUID) -> dict:
        try:
            self.session.execute(text("BEGIN IMMEDIATE"))
            receipt = self._receipt(str(operation_id))
            if receipt is None:
                raise HTTPException(404, "未找到这次词库操作。")
            if not receipt.undone:
                entries = self._entries([item["id"] for item in receipt.snapshots])
                by_id = {entry.id: entry for entry in entries}
                # Preflight the entire batch before changing even one row.
                # The full guard also detects LOOKUP, new article encounters,
                # later assessments and independent schedule changes.
                for item in receipt.snapshots:
                    entry = by_id[item["id"]]
                    if entry.lemma != item["lemma"] or self._state(entry) != item["after"]:
                        raise HTTPException(409, "部分词条在此后已有新操作，为保留学习记录，本次批量调整无法撤销。")
                now = max(self._next_version(entries), utc(receipt.created_at) + timedelta(microseconds=1))
                for item in receipt.snapshots:
                    if not item["changed"]:
                        continue
                    entry, before = by_id[item["id"]], item["before"]
                    entry.status, entry.mastery_score = before["status"], before["mastery_score"]
                    # Do not roll updated_at backwards: a stale pre-edit tab
                    # must not pass an optimistic version check after undo.
                    entry.updated_at = now
                    self._restore_schedule(entry, before["schedule"])
                    original = self.session.get(LearningEvent, item["mark_event_id"])
                    if original is None or original.user_id != self.repo.user_id:
                        raise HTTPException(409, "原始学习记录已变化，本次调整无法撤销。")
                    original.details = {**original.details, "original_event_type": original.event_type,
                                        "reverted_by_operation": receipt.request_id, "reverted_at": _timestamp(now)}
                    original.event_type = "VOCAB_MARK_REVERTED"
                    event = self.repo.event(entry.lemma, "VOCABULARY_UNDO", details={
                        "origin": "vocabulary_bulk", "operation_id": receipt.request_id,
                        "reverted_event_id": original.id, "previous_status": item["after"]["status"],
                        "restored_status": entry.status, "mastery_before": item["after"]["mastery_score"],
                        "mastery_after": entry.mastery_score,
                    })
                    event.created_at = now
                receipt.undone, receipt.undone_at = True, now
                self.session.flush()
            result = self._result(receipt)
            self.session.commit()
            return result
        except Exception:
            self.session.rollback()
            raise

    def _receipt(self, key: str) -> VocabularyOperation | None:
        return self.session.scalar(select(VocabularyOperation).where(
            VocabularyOperation.user_id == self.repo.user_id, VocabularyOperation.request_id == key,
        ).execution_options(populate_existing=True))

    @staticmethod
    def _next_version(entries: list[Vocabulary]) -> datetime:
        # Preserve optimistic versions even with a corrected/backwards clock,
        # or two operations within a frozen microsecond during an undo/retry.
        return max([utcnow(), *(utc(entry.updated_at) + timedelta(microseconds=1) for entry in entries)])

    @staticmethod
    def _require_payload(receipt: VocabularyOperation, digest: str) -> None:
        if receipt.payload_hash != digest:
            raise HTTPException(409, "该请求编号已用于另一项词库调整。")

    def _entries(self, ids: list[int]) -> list[Vocabulary]:
        entries = list(self.session.scalars(select(Vocabulary).where(
            Vocabulary.user_id == self.repo.user_id, Vocabulary.id.in_(ids),
        ).execution_options(populate_existing=True)))
        if len(entries) != len(ids):
            # Avoid revealing whether a forged ID exists for another user.
            raise HTTPException(409, "部分词条不可用，请刷新词库后重新选择。")
        return entries

    def _schedule(self, entry: Vocabulary) -> ReviewSchedule | None:
        return self.session.scalar(select(ReviewSchedule).where(
            ReviewSchedule.vocabulary_id == entry.id, ReviewSchedule.user_id == self.repo.user_id,
        ).execution_options(populate_existing=True))

    def _state(self, entry: Vocabulary) -> dict:
        schedule = self._schedule(entry)
        latest_event_id = self.session.scalar(select(func.max(LearningEvent.id)).where(
            LearningEvent.user_id == self.repo.user_id, LearningEvent.lemma == entry.lemma,
        ))
        return {"status": entry.status, "mastery_score": entry.mastery_score,
                "updated_at": _timestamp(entry.updated_at),
                "seen_count": entry.seen_count, "lookup_count": entry.lookup_count,
                "first_seen_at": _timestamp(entry.first_seen_at), "last_seen_at": _timestamp(entry.last_seen_at),
                "last_event_id": latest_event_id,
                "schedule": {"due_at": _timestamp(schedule.due_at), "known_streak": schedule.known_streak}
                if schedule else None}

    def _restore_schedule(self, entry: Vocabulary, before: dict | None) -> None:
        schedule = self._schedule(entry)
        if before is None:
            if schedule is not None:
                self.session.delete(schedule)
            return
        if schedule is None:
            schedule = ReviewSchedule(vocabulary_id=entry.id, user_id=self.repo.user_id)
            self.session.add(schedule)
        schedule.due_at = datetime.fromisoformat(before["due_at"])
        schedule.known_streak = before["known_streak"]

    def _result(self, receipt: VocabularyOperation) -> dict:
        entries = self._entries([item["id"] for item in receipt.snapshots])
        by_id = {entry.id: entry for entry in entries}
        return {"operation_id": receipt.request_id,
                "items": [by_id[item["id"]] for item in receipt.snapshots],
                "changed_count": sum(item["changed"] for item in receipt.snapshots),
                "undone": receipt.undone}
