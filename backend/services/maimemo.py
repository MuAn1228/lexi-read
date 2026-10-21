"""Local, additive Maimemo import. Remote observations never become self-assessments."""
from collections import defaultdict
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from threading import Lock
from time import monotonic
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from backend.models import LearningEvent, Vocabulary
from backend.models.entities import utcnow
from backend.repositories.vocabulary import VocabularyRepository
from backend.services.lemmatization import normalize_word


SOURCE_EVENT = "IMPORT_MAIMEMO"
SYNC_EVENT = "MAIMEMO_SYNC"
PREVIEW_TTL = 300
_previews: dict[str, tuple[float, str, dict]] = {}
_preview_lock = Lock()


def credential_scope(token: str) -> str:
    return sha256(token.encode()).hexdigest()


def studied(record: dict) -> bool:
    return record["study_count"] > 0 or bool(record.get("first_study_date"))


def group_records(records: list[dict], include_planned: bool) -> tuple[dict, int]:
    grouped, invalid = defaultdict(list), 0
    for record in records:
        if not include_planned and not studied(record):
            continue
        try:
            lemma = normalize_word(record["voc_spelling"])
        except ValueError:
            invalid += 1
            continue
        grouped[lemma].append(record)
    # Stable ordering makes a repeated preview/import a no-op for source events.
    return {lemma: sorted(rows, key=lambda row: row["voc_id"])
            for lemma, rows in grouped.items()}, invalid


def save_preview(token: str, snapshot: dict) -> dict:
    grouped, invalid = group_records(snapshot["records"], True)
    learned = sum(studied(record) for record in snapshot["records"])
    preview_id = uuid4().hex
    with _preview_lock:
        for key, (expiry, _, _) in list(_previews.items()):
            if expiry <= monotonic():
                _previews.pop(key)
        while len(_previews) >= 4:
            _previews.pop(next(iter(_previews)))
        _previews[preview_id] = (monotonic() + PREVIEW_TTL, credential_scope(token), deepcopy(snapshot))
    return {
        "preview_id": preview_id, "expires_at": (utcnow() + timedelta(seconds=PREVIEW_TTL)).isoformat(),
        "remote_total": snapshot["remote_total"], "fetched_count": len(snapshot["records"]),
        "studied_count": learned, "planned_count": len(snapshot["records"]) - learned,
        "valid_words": len(grouped), "invalid_words": invalid,
        "complete": snapshot["complete"], "warning": snapshot["warning"],
        "sample_words": [rows[0]["voc_spelling"] for rows in list(grouped.values())[:12]],
    }


def load_preview(preview_id: str, token: str) -> dict:
    with _preview_lock:
        value = _previews.get(preview_id)
        if not value or value[0] <= monotonic() or value[1] != credential_scope(token):
            _previews.pop(preview_id, None)
            raise HTTPException(410, "墨墨预览已过期或配置已改变，请重新读取。")
        return deepcopy(value[2])


def source_rows(session: Session, user_id: int = 1) -> dict:
    latest = select(func.max(LearningEvent.id).label("id")).where(
        LearningEvent.user_id == user_id, LearningEvent.event_type == SOURCE_EVENT,
    ).group_by(LearningEvent.lemma).subquery()
    rows = session.scalars(select(LearningEvent).join(latest, LearningEvent.id == latest.c.id))
    return {row.lemma: {"records": row.details["records"], "synced_at": row.created_at.isoformat()}
            for row in rows if isinstance(row.details.get("records"), list)}


def status(session: Session, configured: bool, user_id: int = 1) -> dict:
    imported = session.scalar(select(func.count(func.distinct(LearningEvent.lemma))).where(
        LearningEvent.user_id == user_id, LearningEvent.event_type == SOURCE_EVENT,
    ))
    synced = session.scalar(select(func.max(LearningEvent.created_at)).where(
        LearningEvent.user_id == user_id, LearningEvent.event_type == SYNC_EVENT,
    ))
    return {"configured": configured, "imported_words": imported or 0,
            "last_synced_at": synced.isoformat() if synced else None}


def import_preview(session: Session, snapshot: dict, include_planned: bool, user_id: int = 1) -> dict:
    """One SQLite transaction; retry preserves vocabulary and avoids duplicate observations."""
    grouped, invalid = group_records(snapshot["records"], include_planned)
    if not grouped:
        raise HTTPException(422, "本次没有可导入的有效英文词汇，请检查预览范围。")
    imported_records = sum(len(rows) for rows in grouped.values())
    result = {"added": 0, "existing": 0, "source_updated": 0,
              "duplicate_records": imported_records - len(grouped), "invalid_words": invalid,
              "imported_records": imported_records, "complete": snapshot["complete"],
              "warning": snapshot["warning"]}
    # This service owns the transaction. Dependency authentication does not read
    # the local database; remote fetching/normalization already finished.
    if session.in_transaction():
        raise RuntimeError("Maimemo import requires a fresh transaction")
    try:
        session.execute(text("BEGIN IMMEDIATE"))
        existing = {row.lemma for row in session.scalars(select(Vocabulary).where(Vocabulary.user_id == user_id))}
        sources = source_rows(session, user_id)
        repository = VocabularyRepository(session, user_id)
        now = utcnow()
        for lemma, rows in grouped.items():
            if lemma in existing:
                result["existing"] += 1
            else:
                repository.ensure(rows[0]["voc_spelling"].strip(), lemma)
                result["added"] += 1
            previous_records = sources.get(lemma, {}).get("records", [])
            merged = {row["voc_id"]: row for row in previous_records}
            merged.update({row["voc_id"]: row for row in rows})
            merged_rows = sorted(merged.values(), key=lambda row: row["voc_id"])
            if previous_records != merged_rows:
                session.add(LearningEvent(user_id=user_id, word=rows[0]["voc_spelling"].strip(), lemma=lemma,
                    event_type=SOURCE_EVENT, details={"origin": "maimemo", "version": 1, "records": merged_rows},
                    created_at=now))
                result["source_updated"] += 1
        session.add(LearningEvent(user_id=user_id, word="Maimemo", lemma="maimemo", event_type=SYNC_EVENT,
            details={"origin": "maimemo", "version": 1, "include_planned": include_planned,
                     "remote_total": snapshot["remote_total"], "fetched_count": len(snapshot["records"]), **result},
            created_at=now))
        session.commit()
    except Exception:
        session.rollback()
        raise
    return result
