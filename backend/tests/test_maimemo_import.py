"""Synthetic imports: preserve learning signals, atomicity, scopes and complete backups."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from backend.config import Settings
from backend.database import get_session
from backend.main import app
from backend.models import Base, LearningEvent, ReviewSchedule, User, Vocabulary
from backend.services import maimemo
from backend.api import maimemo_routes


def record(word="models", identity="one", count=4):
    return {"voc_id": identity, "voc_spelling": word, "study_count": count,
            "add_date": "2026-01-01T00:00:00Z", "first_study_date": "2026-01-02T00:00:00Z" if count else None,
            "last_study_date": None, "next_study_date": "2026-11-01T00:00:00+08:00",
            "last_response": "FORGET", "tags": ["STICKING"]}


def snapshot(*records):
    return {"records": list(records), "remote_total": len(records), "complete": True, "warning": None}


@pytest.fixture
def database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.sqlite3'}", connect_args={"check_same_thread": False, "timeout": 10})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        session.add_all([User(id=1), User(id=2)])
        session.commit()
    yield factory
    engine.dispose()


def rows(factory, table):
    with factory() as session:
        return [dict(row) for row in session.execute(text(f'SELECT * FROM "{table}" ORDER BY 1')).mappings()]


def test_import_preserves_every_existing_vocabulary_field_and_schedule(database):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with database() as session:
        old = Vocabulary(user_id=1, word="model", lemma="model", status="KNOWN", mastery_score=.91,
                         seen_count=7, lookup_count=3, first_seen_at=now, last_seen_at=now, created_at=now, updated_at=now)
        session.add(old); session.flush()
        session.add(ReviewSchedule(vocabulary_id=old.id, user_id=1, due_at=now, known_streak=4))
        session.commit()
    before, schedule = rows(database, "vocabulary"), rows(database, "review_schedules")
    source = snapshot(record(), record("Models", "two"), record("apple", "three"),
                      record("planned", "four", 0), record("123", "bad"))
    with database() as session:
        result = maimemo.import_preview(session, source, False)
    assert result == {"added": 1, "existing": 1, "source_updated": 2, "duplicate_records": 1,
                      "invalid_words": 1, "imported_records": 3, "complete": True, "warning": None}
    after = rows(database, "vocabulary")
    assert after[0] == before[0]
    assert after[1]["status"] is None and after[1]["mastery_score"] is None
    assert after[1]["seen_count"] == after[1]["lookup_count"] == 0
    assert after[1]["first_seen_at"] is after[1]["last_seen_at"] is None
    assert rows(database, "review_schedules") == schedule
    assert rows(database, "documents") == rows(database, "word_occurrences") == []
    assert {row["event_type"] for row in rows(database, "learning_events")} == {maimemo.SOURCE_EVENT, maimemo.SYNC_EVENT}
    with database() as session:
        retry = maimemo.import_preview(session, deepcopy(source), False)
    assert retry["added"] == retry["source_updated"] == 0 and retry["existing"] == 2
    assert rows(database, "vocabulary") == after
    with database() as session:
        assert len(maimemo.source_rows(session)["model"]["records"]) == 2


def test_remote_feedback_update_never_updates_local_mastery(database):
    original = record()
    with database() as session:
        maimemo.import_preview(session, snapshot(original), False)
    before = rows(database, "vocabulary")
    original["last_response"] = "WELL_FAMILIAR"
    with database() as session:
        result = maimemo.import_preview(session, snapshot(original), False)
    assert result["source_updated"] == 1
    assert rows(database, "vocabulary") == before


def test_narrow_or_partial_sync_preserves_other_remote_word_forms(database):
    source = snapshot(record("model", "one"), record("models", "two", 0))
    with database() as session:
        maimemo.import_preview(session, source, True)
    with database() as session:
        result = maimemo.import_preview(session, source, False)
        assert result["source_updated"] == 0
    partial = snapshot(record("model", "one", 8))
    partial.update(complete=False, warning="synthetic partial")
    with database() as session:
        maimemo.import_preview(session, partial, False)
    with database() as session:
        records = maimemo.source_rows(session)["model"]["records"]
        assert len(records) == 2 and records[0]["study_count"] == 8


def test_latest_source_is_independent_of_recent_twenty_events(database):
    with database() as session:
        maimemo.import_preview(session, snapshot(record()), False)
        session.add_all([LearningEvent(user_id=1, word="model", lemma="model", event_type="LOOKUP", details={}) for _ in range(25)])
        session.add(LearningEvent(user_id=2, word="model", lemma="model", event_type="IMPORT_MAIMEMO", details={"records": []}))
        session.commit()
        repo = maimemo.VocabularyRepository(session, 1)
        assert not any(event.event_type == "IMPORT_MAIMEMO" for event in repo.word_history("model"))
        assert repo.last_maimemo_import("model").details["records"][0]["voc_id"] == "one"


def test_include_planned_and_user_isolation(database):
    source = snapshot(record(count=0))
    with database() as session, pytest.raises(Exception, match="没有可导入"):
        maimemo.import_preview(session, source, False)
    with database() as session:
        result = maimemo.import_preview(session, source, True, user_id=2)
        assert result["added"] == 1
    assert rows(database, "vocabulary")[0]["user_id"] == 2
    with database() as session:
        assert maimemo.source_rows(session, 1) == {}
        assert maimemo.status(session, True, 1)["imported_words"] == 0


@pytest.mark.parametrize("failure", ["flush", "commit"])
def test_database_failure_rolls_back_all_rows(database, monkeypatch, failure):
    with database() as session:
        if failure == "flush":
            original = maimemo.VocabularyRepository.ensure
            def failed(self, word, lemma):
                result = original(self, word, lemma)
                if word == "apple":
                    raise RuntimeError("synthetic failure")
                return result
            monkeypatch.setattr(maimemo.VocabularyRepository, "ensure", failed)
        else:
            monkeypatch.setattr(session, "commit", lambda: (_ for _ in ()).throw(RuntimeError("synthetic failure")))
        with pytest.raises(RuntimeError, match="synthetic failure"):
            maimemo.import_preview(session, snapshot(record(), record("apple", "two")), False)
    assert rows(database, "vocabulary") == rows(database, "learning_events") == []


def test_concurrent_imports_add_once_and_preserve_source_idempotency(database):
    source = snapshot(record(), record("apple", "two"))
    def run(_):
        with database() as session:
            return maimemo.import_preview(session, source, False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    assert sorted(row["added"] for row in results) == [0, 2]
    assert sorted(row["source_updated"] for row in results) == [0, 2]
    assert len(rows(database, "vocabulary")) == 2


def test_preview_is_bound_to_credential_expires_and_does_not_leak_token(monkeypatch):
    token = "synthetic-private-token"
    preview = maimemo.save_preview(token, snapshot(record(), record("future", "two", 0)))
    assert token not in str(preview)
    assert preview["studied_count"] == preview["planned_count"] == 1
    copy = maimemo.load_preview(preview["preview_id"], token)
    copy["records"].clear()
    assert maimemo.load_preview(preview["preview_id"], token)["records"]
    with pytest.raises(Exception, match="过期"):
        maimemo.load_preview(preview["preview_id"], "another")
    second = maimemo.save_preview(token, snapshot(record()))
    monkeypatch.setattr(maimemo, "monotonic", lambda: float("inf"))
    with pytest.raises(Exception, match="过期"):
        maimemo.load_preview(second["preview_id"], token)


@pytest.fixture
def client(database, monkeypatch):
    settings = Settings(_env_file=None, maimemo_api_token=SecretStr("synthetic-private-token"))
    monkeypatch.setattr("backend.main.initialize_database", lambda: None)
    monkeypatch.setattr(maimemo_routes, "get_settings", lambda: settings)
    def sessions():
        with database() as session:
            yield session
    app.dependency_overrides[get_session] = sessions
    with TestClient(app) as client:
        yield client, settings
    app.dependency_overrides.clear()


def test_routes_preview_then_import_no_remote_data_from_browser(client, database, monkeypatch):
    client, settings = client
    monkeypatch.setattr(maimemo_routes.MaimemoClient, "fetch_records", lambda self: snapshot(record()))
    assert client.get("/api/integrations/maimemo/status").json()["configured"]
    preview = client.post("/api/integrations/maimemo/preview")
    assert preview.status_code == 200 and "no-store" in preview.headers["cache-control"]
    assert rows(database, "vocabulary") == []
    payload = {"preview_id": preview.json()["preview_id"], "include_planned": False}
    result = client.post("/api/integrations/maimemo/import", json=payload)
    assert result.status_code == 200 and result.json()["added"] == 1
    assert client.post("/api/integrations/maimemo/import", json=payload).json()["added"] == 0
    history = client.get("/api/vocabulary/model/history").json()
    assert history["maimemo_source"]["details"]["records"][0]["voc_id"] == "one"
    assert history["events"] == []
    assert all(event["event_type"] != "IMPORT_MAIMEMO" for event in client.get("/api/events").json())
    assert list(client.get("/api/integrations/maimemo/sources").json()) == ["model"]
    assert client.post("/api/integrations/maimemo/import", json={**payload, "records": []}).status_code == 422
    assert client.post("/api/integrations/maimemo/import", json={**payload, "include_planned": "false"}).status_code == 422
    settings.app_mode = "public"
    for path, method in [("status", "get"), ("sources", "get"), ("preview", "post"), ("import", "post")]:
        response = getattr(client, method)(f"/api/integrations/maimemo/{path}")
        assert response.status_code == 404


def test_routes_safe_remote_failure_missing_token_and_read_lock(client, database, monkeypatch):
    client, settings = client
    def failed(self):
        raise maimemo_routes.MaimemoError("墨墨服务暂时不可用", 503)
    monkeypatch.setattr(maimemo_routes.MaimemoClient, "fetch_records", failed)
    assert client.post("/api/integrations/maimemo/preview").status_code == 503
    with maimemo_routes._reading:
        assert client.post("/api/integrations/maimemo/preview").status_code == 409
    settings.maimemo_api_token = SecretStr("")
    assert not client.get("/api/integrations/maimemo/status").json()["configured"]
    assert client.post("/api/integrations/maimemo/preview").status_code == 503
    assert rows(database, "vocabulary") == []


def test_imported_sources_round_trip_in_complete_backup(backup_app):
    from backend.tests.test_backups import archive_data, all_rows, comparable, preview, restore
    client, factory, settings = backup_app
    with factory() as session:
        maimemo.import_preview(session, snapshot(record()), False)
    before = all_rows(factory)
    archive = archive_data(client)
    with factory() as session:
        maimemo.import_preview(session, snapshot(record("apple", "two")), False)
    pre = preview(client, archive)
    assert pre.status_code == 200
    assert restore(client, pre.json()["token"]).status_code == 200
    assert comparable(all_rows(factory)) == comparable(before)


from backend.tests.test_backups import backup_app  # noqa: E402,F401
from backend.tests.test_pdf_import import pdf_app  # noqa: E402,F401
from backend.tests.test_text_import import text_app  # noqa: E402,F401
