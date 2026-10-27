"""Independent, durable learning and review of stable offline dictionary senses."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.config import Settings
from backend.database import get_session
from backend.main import app
from backend.models import (Base, LearningEvent, ReviewSchedule, SenseReviewReceipt,
                            User, Vocabulary, VocabularySense)
from backend.services import offline_dictionary
from backend.services.sense_review import SenseReviewService


NOW = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
FIRST = {
    "sense_key": "a" * 64, "source": "ecdict", "source_revision": "1" * 64,
    "headword": "bank", "source_ordinal": 0, "part_of_speech": "n.",
    "meaning": "n. 银行；金融机构",
}
SECOND = {
    "sense_key": "b" * 64, "source": "ecdict", "source_revision": "1" * 64,
    "headword": "bank", "source_ordinal": 1, "part_of_speech": "n.",
    "meaning": "n. 岸；河岸",
}


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    event.listen(engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        db.add_all([User(id=1), User(id=2)])
        db.commit()
        yield db
    engine.dispose()


@pytest.fixture
def catalogs(monkeypatch):
    state = {"bank": [FIRST.copy(), SECOND.copy()]}

    def catalog(word, lemma=None):
        return [item.copy() for item in state.get(word.strip().lower(), [])]

    monkeypatch.setattr("backend.services.sense_learning.sense_catalog", catalog)
    monkeypatch.setattr("backend.services.sense_review.sense_catalog", catalog)
    return state


@pytest.fixture
def client(session, catalogs, monkeypatch):
    settings = Settings(app_mode="local", llm_provider="offline", _env_file=None)
    monkeypatch.setattr("backend.auth.get_settings", lambda: settings)
    monkeypatch.setattr("backend.main.initialize_database", lambda: None)
    app.dependency_overrides[get_session] = lambda: session
    try:
        with TestClient(app) as connection:
            yield connection
    finally:
        app.dependency_overrides.clear()


def mark(client, key=FIRST["sense_key"], status="UNKNOWN", **extra):
    return client.put(f"/api/vocabulary/bank/senses/{key}", json={"status": status, **extra})


def test_catalog_is_read_only_then_two_senses_are_independent_and_word_state_is_untouched(client, session):
    before = client.get("/api/vocabulary/bank/senses")
    assert before.status_code == 200
    assert before.json()["vocabulary"] is None
    assert [(item["sense_key"], item["id"], item["status"]) for item in before.json()["senses"]] == [
        (FIRST["sense_key"], None, None), (SECOND["sense_key"], None, None),
    ]
    assert session.scalar(select(func.count(Vocabulary.id))) == 0

    first = mark(client, status="KNOWN")
    second = mark(client, SECOND["sense_key"], "UNKNOWN")
    assert first.status_code == second.status_code == 200
    assert first.json()["mastery_score"] == .65
    entry = session.scalar(select(Vocabulary).where(Vocabulary.lemma == "bank"))
    states = list(session.scalars(select(VocabularySense).order_by(VocabularySense.source_ordinal)))
    assert entry.status is None and entry.mastery_score is None
    assert [(item.status, item.mastery_score) for item in states] == [("KNOWN", .65), ("UNKNOWN", .3)]
    assert list(session.scalars(select(ReviewSchedule))) == []
    assert [event.event_type for event in session.scalars(select(LearningEvent).order_by(LearningEvent.id))] == [
        "SENSE_MARK_KNOWN", "SENSE_MARK_UNKNOWN",
    ]


def test_catalog_never_flushes_unrelated_pending_state(client, session):
    pending = Vocabulary(user_id=1, word="pending", lemma="pending", status=None,
                         mastery_score=None, seen_count=0, lookup_count=0)
    session.add(pending)
    response = client.get("/api/vocabulary/bank/senses")
    assert response.status_code == 200
    assert pending.id is None
    session.rollback()


def test_dictionary_failure_keeps_snapshots_visible_and_blocks_only_new_state(
        client, session, monkeypatch):
    assert mark(client).status_code == 200

    def unavailable(*_):
        raise offline_dictionary.OfflineDictionaryUnavailable("离线词典暂时不可用。")

    monkeypatch.setattr("backend.services.sense_learning.sense_catalog", unavailable)
    catalog = client.get("/api/vocabulary/bank/senses")
    assert catalog.status_code == 200
    assert [(item["sense_key"], item["available"]) for item in catalog.json()["senses"]] == [
        (FIRST["sense_key"], False),
    ]
    # Existing durable snapshots remain markable without reopening the asset.
    assert mark(client, status="UNCERTAIN").status_code == 200

    session.execute(text("DELETE FROM sense_review_receipts"))
    session.execute(text("DELETE FROM vocabulary_senses"))
    session.execute(text("DELETE FROM vocabulary"))
    session.commit()
    rejected = mark(client)
    assert rejected.status_code == 503
    assert session.scalar(select(func.count(Vocabulary.id))) == 0


def test_forged_key_rolls_back_auto_collection_and_orphan_snapshot_remains_markable(client, session, catalogs):
    rejected = mark(client, "f" * 64)
    assert rejected.status_code == 404
    assert session.scalar(select(func.count(Vocabulary.id))) == 0
    assert mark(client).status_code == 200

    catalogs["bank"] = [SECOND.copy()]
    result = client.get("/api/vocabulary/bank/senses").json()
    orphan = next(item for item in result["senses"] if item["sense_key"] == FIRST["sense_key"])
    assert orphan["available"] is False and orphan["meaning"] == FIRST["meaning"]
    changed = mark(client, status="UNCERTAIN")
    assert changed.status_code == 200
    assert changed.json()["available"] is False and changed.json()["status"] == "UNCERTAIN"


def test_review_request_is_exactly_idempotent_and_conflicts_do_not_mutate(client, session):
    assert mark(client).status_code == 200
    key = str(uuid4())
    body = {"status": "KNOWN", "origin": "review", "request_id": key,
            "response_ms": 2300, "plan_minutes": 20}
    first = client.put(f"/api/vocabulary/bank/senses/{FIRST['sense_key']}", json=body)
    assert first.status_code == 200 and first.json()["known_streak"] == 1
    counts = (
        session.scalar(select(func.count(LearningEvent.id))),
        session.scalar(select(func.count(SenseReviewReceipt.id))),
    )
    repeated = client.put(f"/api/vocabulary/bank/senses/{FIRST['sense_key']}", json=body)
    assert repeated.status_code == 200 and repeated.json() == first.json()
    assert counts == (
        session.scalar(select(func.count(LearningEvent.id))),
        session.scalar(select(func.count(SenseReviewReceipt.id))),
    )
    conflict = client.put(f"/api/vocabulary/bank/senses/{FIRST['sense_key']}",
                          json={**body, "status": "UNCERTAIN"})
    assert conflict.status_code == 409
    assert session.scalar(select(VocabularySense.status)) == "KNOWN"


@pytest.mark.parametrize("payload", [
    {"status": "KNOWN", "origin": "review"},
    {"status": "KNOWN", "request_id": "a8ca1f0c-c7ed-4668-a442-81dbd93d83b3"},
    {"status": "KNOWN", "response_ms": 1000},
    {"status": "KNOWN", "origin": "review", "request_id": "a8ca1f0c-c7ed-4668-a442-81dbd93d83b3",
     "plan_minutes": 15},
])
def test_invalid_review_contract_has_no_writes(client, session, payload):
    response = client.put(f"/api/vocabulary/bank/senses/{FIRST['sense_key']}", json=payload)
    assert response.status_code == 422
    assert session.scalar(select(func.count(Vocabulary.id))) == 0


def test_new_api_rejects_unknown_fields_and_duplicate_restore_ids(client, session):
    response = mark(client, unexpected="client definition")
    assert response.status_code == 422
    assert session.scalar(select(func.count(Vocabulary.id))) == 0
    duplicate = client.get("/api/sense-review?sense_ids=1&sense_ids=1")
    assert duplicate.status_code == 422


def test_due_queue_is_read_only_user_scoped_and_supports_sense_id_recovery(session, catalogs):
    word = Vocabulary(user_id=1, word="bank", lemma="bank", status=None, mastery_score=None,
                      seen_count=8, lookup_count=0, created_at=NOW, updated_at=NOW)
    other_word = Vocabulary(user_id=2, word="bank", lemma="bank", status=None, mastery_score=None,
                            seen_count=0, lookup_count=0, created_at=NOW, updated_at=NOW)
    session.add_all([word, other_word])
    session.flush()
    one = VocabularySense(user_id=1, vocabulary_id=word.id, **FIRST, status="UNKNOWN", mastery_score=.3,
                          due_at=NOW - timedelta(minutes=1), known_streak=0,
                          created_at=NOW, updated_at=NOW)
    two = VocabularySense(user_id=1, vocabulary_id=word.id, **SECOND, status="UNCERTAIN", mastery_score=.42,
                          due_at=NOW - timedelta(minutes=2), known_streak=1,
                          created_at=NOW, updated_at=NOW)
    foreign = VocabularySense(user_id=2, vocabulary_id=other_word.id, **FIRST, status="UNKNOWN", mastery_score=.3,
                              due_at=NOW - timedelta(days=1), known_streak=0,
                              created_at=NOW, updated_at=NOW)
    session.add_all([one, two, foreign])
    session.commit()
    before = session.scalar(select(func.count(LearningEvent.id)))

    result = SenseReviewService(session, 1, now=NOW).read(
        limit=10, sense_ids=[one.id], plan_minutes=20,
    )
    assert result.total == 2 and [item.sense.id for item in result.items] == [one.id]
    assert result.items[0].vocabulary.id == word.id and result.items[0].sense.meaning == FIRST["meaning"]
    assert result.plan is not None and result.plan.due_now == 2
    assert session.scalar(select(func.count(LearningEvent.id))) == before
    assert SenseReviewService(session, 2, now=NOW).read().total == 1


def test_offline_catalog_key_is_content_stable_not_array_position(monkeypatch):
    entry = {"headword": "bank", "phonetic": "", "chinese": ["n. 银行", "n. 岸"],
             "english": [], "forms": [], "source": "ecdict", "source_url": "fixture"}
    monkeypatch.setattr(offline_dictionary, "lookup", lambda word, lemma=None: entry)
    monkeypatch.setattr(offline_dictionary, "_manifest", lambda: {"database_sha256": "3" * 64})
    first = offline_dictionary.sense_catalog("BANK")
    entry["chinese"].reverse()
    second = offline_dictionary.sense_catalog("bank")
    assert {item["meaning"]: item["sense_key"] for item in first} == {
        item["meaning"]: item["sense_key"] for item in second
    }
    assert first[0]["part_of_speech"] == "n."
