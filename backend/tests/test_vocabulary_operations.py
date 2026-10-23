"""Bulk edits use only temporary databases and never call a model or OCR."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from backend.api.vocabulary_operations import router
from backend.api.routes import router as reading_router
from backend.auth import token_digest
from backend.config import Settings
from backend.database import get_session
from backend.models import Base, GuestCredential, LearningEvent, ReviewSchedule, User, Vocabulary
from backend.models.vocabulary_operations import VocabularyOperation
from backend.repositories.vocabulary import VocabularyRepository
from backend.schemas import Status
from backend.services.scheduling import utc
from backend.services.vocabulary import VocabularyService
from backend.services.vocabulary_operations import VocabularyBatchService


NOW = datetime(2026, 10, 23, 4, tzinfo=timezone.utc)


@pytest.fixture
def bulk_app(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'bulk.sqlite3'}",
                           connect_args={"check_same_thread": False, "timeout": 10})

    @event.listens_for(engine, "connect")
    def configuration(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add_all([User(id=1), User(id=2), User(id=3)])
        db.flush()
        db.add_all([
            Vocabulary(id=1, user_id=1, word="models", lemma="model", status="UNKNOWN",
                       mastery_score=.3, seen_count=2, lookup_count=1, created_at=NOW, updated_at=NOW,
                       first_seen_at=NOW, last_seen_at=NOW),
            Vocabulary(id=2, user_id=1, word="quiet", lemma="quiet", status=None, mastery_score=None,
                       seen_count=1, lookup_count=0, created_at=NOW, updated_at=NOW),
            Vocabulary(id=3, user_id=1, word="read", lemma="read", status="KNOWN", mastery_score=.8,
                       seen_count=3, lookup_count=0, created_at=NOW, updated_at=NOW),
            Vocabulary(id=4, user_id=2, word="model", lemma="model", status="UNKNOWN", mastery_score=.5,
                       seen_count=5, lookup_count=0, created_at=NOW, updated_at=NOW),
            Vocabulary(id=5, user_id=3, word="model", lemma="model", status="UNCERTAIN", mastery_score=.2,
                       seen_count=3, lookup_count=0, created_at=NOW, updated_at=NOW),
        ])
        db.add_all([GuestCredential(token_hash=token_digest("a" * 43), user_id=2),
                    GuestCredential(token_hash=token_digest("b" * 43), user_id=3)])
        db.commit()

    settings = Settings(data_dir=tmp_path, app_mode="local", llm_provider="offline", _env_file=None)
    monkeypatch.setattr("backend.auth.get_settings", lambda: settings)
    monkeypatch.setattr("backend.services.offline_dictionary.summary", lambda *args: None)
    monkeypatch.setattr("backend.services.offline_dictionary.lookup", lambda *args: None)
    monkeypatch.setattr("backend.services.dictionary.cached_summary", lambda *args: None)
    app = FastAPI()
    app.include_router(router)
    app.include_router(reading_router)

    def override_session():
        with factory() as db:
            yield db

    app.dependency_overrides[get_session] = override_session
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client, factory, settings
    finally:
        engine.dispose()


def payload(ids=(1, 2), status="KNOWN", *, key=None, version=NOW):
    return {"request_id": str(key or uuid4()), "status": status,
            "items": [{"id": entry_id, "updated_at": version.isoformat()} for entry_id in ids]}


def snapshot(factory):
    with factory() as db:
        return {
            "vocabulary": [(e.id, e.status, e.mastery_score, e.seen_count, e.lookup_count,
                            utc(e.updated_at), utc(e.first_seen_at) if e.first_seen_at else None,
                            utc(e.last_seen_at) if e.last_seen_at else None)
                           for e in db.scalars(select(Vocabulary).order_by(Vocabulary.id))],
            "schedules": [(e.vocabulary_id, utc(e.due_at), e.known_streak)
                          for e in db.scalars(select(ReviewSchedule).order_by(ReviewSchedule.vocabulary_id))],
            "events": [(e.id, e.event_type, e.details) for e in db.scalars(select(LearningEvent).order_by(LearningEvent.id))],
            "operations": [(e.request_id, e.payload_hash, e.snapshots, e.undone, e.undone_at)
                           for e in db.scalars(select(VocabularyOperation).order_by(VocabularyOperation.id))],
        }


def seed_schedule(factory, entry_id=1, *, streak=4):
    with factory() as db:
        db.add(ReviewSchedule(vocabulary_id=entry_id, user_id=1,
                              due_at=NOW + timedelta(days=17), known_streak=streak))
        db.commit()


def test_bulk_mark_updates_score_and_schedule_as_one_transaction(bulk_app):
    client, factory, _ = bulk_app
    seed_schedule(factory)
    body = payload()
    response = client.post("/api/vocabulary/batch", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["operation_id"] == body["request_id"] and not result["undone"]
    assert result["changed_count"] == 2
    assert [item["id"] for item in result["items"]] == [1, 2]
    assert all(item["status"] == "KNOWN" for item in result["items"])
    assert response.headers["cache-control"] == "private, no-store"
    with factory() as db:
        entries = list(db.scalars(select(Vocabulary).where(Vocabulary.id.in_([1, 2])).order_by(Vocabulary.id)))
        assert entries[0].mastery_score > .3 and entries[1].mastery_score is not None
        for entry in entries:
            schedule = db.get(ReviewSchedule, entry.id)
            assert schedule.known_streak == 0
            assert utc(schedule.due_at) == utc(entry.updated_at) + timedelta(days=3)
        events = list(db.scalars(select(LearningEvent)))
        assert len(events) == 2 and all(e.event_type == "MARK_KNOWN" for e in events)
        assert all(e.details["origin"] == "vocabulary_bulk" for e in events)


def test_same_status_is_noop_even_when_score_or_schedule_is_missing(bulk_app):
    client, factory, _ = bulk_app
    body = payload((3,))
    before = snapshot(factory)
    response = client.post("/api/vocabulary/batch", json=body)
    assert response.status_code == 200 and response.json()["changed_count"] == 0
    current = snapshot(factory)
    assert current["vocabulary"] == before["vocabulary"]
    assert current["events"] == current["schedules"] == []
    assert len(current["operations"]) == 1
    assert client.post(f"/api/vocabulary/batch/{body['request_id']}/undo").json()["undone"] is True
    current = snapshot(factory)
    assert current["vocabulary"] == before["vocabulary"]
    assert current["events"] == current["schedules"] == []


def test_undo_restores_null_score_existing_and_absent_schedule_and_retains_audit(bulk_app):
    client, factory, _ = bulk_app
    seed_schedule(factory)
    before = snapshot(factory)
    body = payload()
    assert client.post("/api/vocabulary/batch", json=body).status_code == 200
    response = client.post(f"/api/vocabulary/batch/{body['request_id']}/undo")
    assert response.status_code == 200, response.text
    assert response.json()["undone"] is True and response.json()["changed_count"] == 2
    after = snapshot(factory)
    assert after["schedules"] == before["schedules"]
    # The only vocabulary field that is not restored is its monotonic version.
    for old, new in zip(before["vocabulary"], after["vocabulary"]):
        assert old[:5] == new[:5] and old[6:] == new[6:]
        if old[0] in (1, 2):
            assert new[5] > old[5]
    assert [event[1] for event in after["events"]] == [
        "VOCAB_MARK_REVERTED", "VOCAB_MARK_REVERTED", "VOCABULARY_UNDO", "VOCABULARY_UNDO"]
    assert all(e[2]["original_event_type"] == "MARK_KNOWN" for e in after["events"][:2])
    with factory() as db:
        assert VocabularyRepository(db).last_mark("model") is None
        assert db.get(Vocabulary, 2).status is None and db.get(Vocabulary, 2).mastery_score is None
        assert db.scalar(select(VocabularyOperation)).undone_at is not None


def test_undo_exposes_the_previous_real_last_mark(bulk_app):
    client, factory, _ = bulk_app
    with factory() as db:
        db.add(LearningEvent(user_id=1, word="model", lemma="model", event_type="MARK_UNKNOWN",
                             created_at=NOW, details={}))
        db.commit()
    body = payload((1,))
    assert client.post("/api/vocabulary/batch", json=body).status_code == 200
    assert client.post(f"/api/vocabulary/batch/{body['request_id']}/undo").status_code == 200
    with factory() as db:
        assert VocabularyRepository(db).last_mark("model").event_type == "MARK_UNKNOWN"


def test_reading_vocabulary_and_word_history_does_not_block_undo(bulk_app):
    client, factory, _ = bulk_app
    body = payload()
    assert client.post("/api/vocabulary/batch", json=body).status_code == 200
    before_reads = snapshot(factory)
    for url in ("/api/vocabulary", "/api/vocabulary/models/history", "/api/events?limit=1000"):
        response = client.get(url)
        assert response.status_code == 200, response.text
    assert snapshot(factory) == before_reads
    response = client.post(f"/api/vocabulary/batch/{body['request_id']}/undo")
    assert response.status_code == 200 and response.json()["undone"] is True


@pytest.mark.parametrize("status", ["UNKNOWN", "UNCERTAIN"])
def test_unfamiliar_bulk_choices_and_undo_restore_preexisting_mastery_and_streak(bulk_app, status):
    client, factory, _ = bulk_app
    seed_schedule(factory, 3)
    before = snapshot(factory)
    body = payload((2, 3), status=status)
    result = client.post("/api/vocabulary/batch", json=body)
    assert result.status_code == 200, result.text
    with factory() as db:
        for entry_id in (2, 3):
            entry, schedule = db.get(Vocabulary, entry_id), db.get(ReviewSchedule, entry_id)
            assert entry.status == status and entry.mastery_score is not None
            assert utc(schedule.due_at) == utc(entry.updated_at) and schedule.known_streak == 0
    assert client.post(f"/api/vocabulary/batch/{body['request_id']}/undo").status_code == 200
    after = snapshot(factory)
    assert after["schedules"] == before["schedules"]
    assert [row[:5] for row in after["vocabulary"]] == [row[:5] for row in before["vocabulary"]]


def test_apply_retry_after_later_mark_returns_current_state_without_replaying(bulk_app):
    client, factory, _ = bulk_app
    body = payload()
    assert client.post("/api/vocabulary/batch", json=body).status_code == 200
    with factory() as db:
        VocabularyService(VocabularyRepository(db)).mark("model", Status.UNCERTAIN)
    before_retry = snapshot(factory)
    response = client.post("/api/vocabulary/batch", json=body)
    assert response.status_code == 200 and response.json()["items"][0]["status"] == "UNCERTAIN"
    assert response.json()["undone"] is False
    assert snapshot(factory) == before_retry
    assert client.post(f"/api/vocabulary/batch/{body['request_id']}/undo").status_code == 409
    assert snapshot(factory) == before_retry


def test_a_stale_tab_cannot_reapply_after_apply_and_undo_restore_identical_status(bulk_app):
    client, factory, _ = bulk_app
    body = payload()
    assert client.post("/api/vocabulary/batch", json=body).status_code == 200
    assert client.post(f"/api/vocabulary/batch/{body['request_id']}/undo").status_code == 200
    before_stale = snapshot(factory)
    assert client.post("/api/vocabulary/batch", json=payload()).status_code == 409
    assert snapshot(factory) == before_stale


def test_lost_response_retries_and_undo_retries_never_replay_changes(bulk_app):
    client, factory, _ = bulk_app
    body = payload()
    first = client.post("/api/vocabulary/batch", json=body)
    assert first.status_code == 200
    before_retry = snapshot(factory)
    # Reordering and UTC timezone spelling do not change request identity.
    retry = {**body, "items": [{**item, "updated_at": item["updated_at"].replace("+00:00", "Z")}
                              for item in reversed(body["items"])]}
    assert client.post("/api/vocabulary/batch", json=retry).status_code == 200
    assert snapshot(factory) == before_retry
    url = f"/api/vocabulary/batch/{body['request_id']}/undo"
    assert client.post(url).status_code == 200
    undone = snapshot(factory)
    assert client.post(url).status_code == 200
    assert snapshot(factory) == undone
    with factory() as db:
        VocabularyService(VocabularyRepository(db)).mark("model", Status.UNCERTAIN)
    progressed = snapshot(factory)
    result = client.post("/api/vocabulary/batch", json=body).json()
    assert result["undone"] is True and result["items"][0]["status"] == "UNCERTAIN"
    assert client.post(url).json()["items"][0]["status"] == "UNCERTAIN"
    assert snapshot(factory) == progressed


@pytest.mark.parametrize("changed", ["status", "ids", "version"])
def test_reusing_request_uuid_for_other_payload_is_conflict(bulk_app, changed):
    client, factory, _ = bulk_app
    body = payload()
    assert client.post("/api/vocabulary/batch", json=body).status_code == 200
    before = snapshot(factory)
    modified = payload((1,), key=body["request_id"]) if changed == "ids" else {**body}
    if changed == "status":
        modified["status"] = "UNKNOWN"
    if changed == "version":
        modified["items"] = [{**item, "updated_at": (NOW + timedelta(seconds=1)).isoformat()}
                             for item in body["items"]]
    assert client.post("/api/vocabulary/batch", json=modified).status_code == 409
    assert snapshot(factory) == before


@pytest.mark.parametrize("ids", [(1, 999999), (1, 4)])
def test_missing_or_foreign_id_rejects_entire_batch_without_leaking_data(bulk_app, ids):
    client, factory, _ = bulk_app
    before = snapshot(factory)
    result = client.post("/api/vocabulary/batch", json=payload(ids))
    assert result.status_code == 409
    assert snapshot(factory) == before


def test_stale_version_rejects_whole_batch_even_if_other_entries_are_current(bulk_app):
    client, factory, _ = bulk_app
    body = payload()
    with factory() as db:
        VocabularyService(VocabularyRepository(db)).observe("quiet", "quiet")
        db.commit()
    before = snapshot(factory)
    assert client.post("/api/vocabulary/batch", json=body).status_code == 409
    assert snapshot(factory) == before


def test_naive_utc_and_offset_versions_compare_as_same_instant(bulk_app):
    client, _, _ = bulk_app
    body = payload()
    body["items"][0]["updated_at"] = NOW.replace(tzinfo=None).isoformat()
    body["items"][1]["updated_at"] = NOW.astimezone(timezone(timedelta(hours=8))).isoformat()
    assert client.post("/api/vocabulary/batch", json=body).status_code == 200


@pytest.mark.parametrize("mutation", ["lookup", "mark", "observation", "event", "schedule"])
def test_any_later_activity_prevents_entire_undo_and_preserves_new_learning(bulk_app, mutation):
    client, factory, _ = bulk_app
    body = payload()
    assert client.post("/api/vocabulary/batch", json=body).status_code == 200
    with factory() as db:
        repo = VocabularyRepository(db)
        service = VocabularyService(repo)
        if mutation == "lookup":
            service.lookup("quiet", None, "offline")
        elif mutation == "mark":
            service.mark("quiet", Status.KNOWN)
        elif mutation == "observation":
            service.observe("quiet", "quiet", 3)
        elif mutation == "event":
            repo.event("quiet", "RECOMMENDATION_VIEWED")
        elif mutation == "schedule":
            schedule = db.get(ReviewSchedule, 2)
            schedule.due_at += timedelta(seconds=1)
        db.commit()
    before_undo = snapshot(factory)
    response = client.post(f"/api/vocabulary/batch/{body['request_id']}/undo")
    assert response.status_code == 409, response.text
    assert snapshot(factory) == before_undo


def test_mid_batch_write_failure_rolls_back_marks_schedule_and_receipt(bulk_app, monkeypatch):
    client, factory, _ = bulk_app
    seed_schedule(factory)
    before = snapshot(factory)
    original = VocabularyService.mark
    calls = 0

    def fail_on_second(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        result = original(self, *args, **kwargs)
        self.repo.session.flush()
        if calls == 2:
            raise RuntimeError("injected second mark failure")
        return result

    monkeypatch.setattr(VocabularyService, "mark", fail_on_second)
    body = payload()
    assert client.post("/api/vocabulary/batch", json=body).status_code == 500
    assert snapshot(factory) == before
    monkeypatch.setattr(VocabularyService, "mark", original)
    assert client.post("/api/vocabulary/batch", json=body).status_code == 200


def test_mid_undo_failure_rolls_back_status_schedule_audit_and_receipt(bulk_app, monkeypatch):
    client, factory, _ = bulk_app
    seed_schedule(factory)
    body = payload()
    assert client.post("/api/vocabulary/batch", json=body).status_code == 200
    before = snapshot(factory)
    original = VocabularyBatchService._restore_schedule
    calls = 0

    def fail_on_second(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        original(self, *args, **kwargs)
        self.session.flush()
        if calls == 2:
            raise RuntimeError("injected second restore failure")

    monkeypatch.setattr(VocabularyBatchService, "_restore_schedule", fail_on_second)
    url = f"/api/vocabulary/batch/{body['request_id']}/undo"
    assert client.post(url).status_code == 500
    assert snapshot(factory) == before
    monkeypatch.setattr(VocabularyBatchService, "_restore_schedule", original)
    assert client.post(url).status_code == 200


def test_public_user_cannot_apply_or_undo_another_users_operation(bulk_app, monkeypatch):
    client, factory, _ = bulk_app
    settings = Settings(app_mode="public", cors_origins="https://reader.example", _env_file=None)
    monkeypatch.setattr("backend.auth.get_settings", lambda: settings)
    user_a = {"Authorization": "Bearer " + "a" * 43}
    user_b = {"Authorization": "Bearer " + "b" * 43}
    body = payload((4,))
    assert client.post("/api/vocabulary/batch", json=body).status_code == 401
    assert client.post("/api/vocabulary/batch", json=body, headers=user_a).status_code == 200
    before = snapshot(factory)
    undo = f"/api/vocabulary/batch/{body['request_id']}/undo"
    assert client.post(undo, headers=user_b).status_code == 404
    assert client.post("/api/vocabulary/batch", json=payload((4, 5)), headers=user_b).status_code == 409
    assert snapshot(factory) == before
    # UUIDs are namespaced by owner, not global across unrelated visitors.
    assert client.post("/api/vocabulary/batch", json=payload((5,), key=body["request_id"]), headers=user_b).status_code == 200
    assert client.post(undo, headers=user_a).json()["items"][0]["status"] == "UNKNOWN"


@pytest.mark.parametrize("bad", [
    {"items": []}, {"items": [{"id": 1, "updated_at": NOW.isoformat()}] * 2},
    {"items": [{"id": entry_id, "updated_at": NOW.isoformat()} for entry_id in range(1, 202)]},
    {"items": [{"id": 0, "updated_at": NOW.isoformat()}]},
    {"items": [{"id": True, "updated_at": NOW.isoformat()}]},
    {"items": [{"id": "1", "updated_at": NOW.isoformat()}]},
    {"items": [{"id": 1, "updated_at": "not-a-date"}]},
    {"items": [{"id": 1, "updated_at": 1792728000}]},
    {"items": [{"id": 1}]}, {"request_id": "invalid-uuid"},
    {"status": "SKIP"}, {"unexpected": True},
])
def test_invalid_payload_has_no_writes(bulk_app, bad):
    client, factory, _ = bulk_app
    before = snapshot(factory)
    response = client.post("/api/vocabulary/batch", json={**payload(), **bad})
    assert response.status_code == 422, response.text
    assert snapshot(factory) == before


def test_invalid_or_unknown_undo_request_has_no_writes(bulk_app):
    client, factory, _ = bulk_app
    before = snapshot(factory)
    assert client.post("/api/vocabulary/batch/not-a-uuid/undo").status_code == 422
    assert client.post(f"/api/vocabulary/batch/{uuid4()}/undo").status_code == 404
    assert snapshot(factory) == before


def test_two_simultaneous_identical_requests_apply_once(bulk_app):
    _, factory, _ = bulk_app
    barrier, key = Barrier(2), uuid4()

    def worker():
        with factory() as db:
            barrier.wait(timeout=5)
            return VocabularyBatchService(VocabularyRepository(db)).apply(
                key, Status.KNOWN, [(1, NOW), (2, NOW)])

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: worker(), range(2)))
    assert all(result["operation_id"] == str(key) and result["changed_count"] == 2 for result in results)
    state = snapshot(factory)
    assert len(state["operations"]) == 1 and len(state["events"]) == 2 and len(state["schedules"]) == 2


def test_two_concurrent_different_batches_with_stale_versions_have_one_winner(bulk_app):
    _, factory, _ = bulk_app
    barrier = Barrier(2)

    def worker(status):
        with factory() as db:
            barrier.wait(timeout=5)
            try:
                return VocabularyBatchService(VocabularyRepository(db)).apply(uuid4(), status, [(1, NOW)])
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(worker, [Status.KNOWN, Status.UNCERTAIN]))
    assert sum(result == 409 for result in results) == 1
    state = snapshot(factory)
    assert len(state["operations"]) == 1 and len(state["events"]) == 1


def test_two_simultaneous_undo_requests_restore_once(bulk_app):
    client, factory, _ = bulk_app
    body = payload()
    assert client.post("/api/vocabulary/batch", json=body).status_code == 200
    barrier = Barrier(2)

    def worker():
        with factory() as db:
            barrier.wait(timeout=5)
            return VocabularyBatchService(VocabularyRepository(db)).undo(UUID(body["request_id"]))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: worker(), range(2)))
    assert all(result["undone"] for result in results)
    state = snapshot(factory)
    assert len(state["events"]) == 4 and state["schedules"] == []
