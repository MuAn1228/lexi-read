"""Real SQLite/API checks for plain-text reading and safe import retries."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import sqlite3
from threading import Barrier
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from backend import migrations
from backend.config import Settings
from backend.database import get_session
from backend.main import app
from backend.models import (Base, Document, LearningEvent, ReviewSchedule,
                            TextImportReceipt, User, Vocabulary, WordOccurrence)
from backend.schemas import TextImportRequest
from backend.services.text_import import import_text
from backend.services.vocabulary import VocabularyService
from backend.tests.test_migrations import legacy, rows  # noqa: F401


@pytest.fixture
def text_app(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'text.sqlite3'}",
                           connect_args={"check_same_thread": False, "timeout": 15})

    @event.listens_for(engine, "connect")
    def configure(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add_all([User(id=1), User(id=2)])
        session.commit()
    settings = Settings(data_dir=tmp_path, llm_provider="offline", _env_file=None)
    for module in ("backend.auth", "backend.api.routes", "backend.services.documents",
                   "backend.services.llm", "backend.public_limits"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    monkeypatch.setattr("backend.main.settings", settings)
    monkeypatch.setattr("backend.main.initialize_database", lambda: None)

    def session_override():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    try:
        with TestClient(app) as client:
            yield client, factory, settings
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def payload(text="Model models.\nA new model!", title="Reading note", request_id=None):
    return {"text": text, "title": title, "request_id": request_id or str(uuid4())}


def post(client, body=None, **kwargs):
    response = client.post("/api/documents/text", json=body or payload(), **kwargs)
    assert response.status_code == 201, response.text
    return response.json()


def count(factory, model):
    with factory() as session:
        return session.scalar(select(func.count()).select_from(model))


def test_exact_unicode_spans_surfaces_lines_context_and_no_synthetic_assessments(text_app, monkeypatch):
    client, factory, settings = text_app

    def unexpected(*args, **kwargs):
        pytest.fail("Text import must not use OCR or LLM")

    monkeypatch.setattr("backend.services.documents.get_ocr_service", unexpected)
    monkeypatch.setattr("backend.services.llm.LLMService.analyze_word", unexpected)
    monkeypatch.setattr("backend.services.llm.LLMService.recommend_related_words", unexpected)
    content = "🙂中文  Models  model, can't self-aware!\r\n\r\nThe models aren’t ready.\rA model?"
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    doc = post(client, payload(content, "  My article  "))
    assert doc["title"] == "My article"
    assert doc["source"] == "text" and doc["image_url"] is None
    assert (doc["width"], doc["height"]) == (0, 0)
    assert doc["ocr_text"] == normalized
    assert [word["word"] for word in doc["words"]] == [
        "Models", "model", "can't", "self-aware", "The", "models", "aren’t", "ready", "A", "model"]
    for word in doc["words"]:
        assert normalized[word["text_start"]:word["text_end"]] == word["word"]
        assert word["line"] == normalized[:word["text_start"]].count("\n")
        assert word["word"] in word["sentence"]
        assert word["bbox"] is None and word["confidence"] is None and word["bbox_source"] == "text"
        assert word["status"] is None
    assert doc["words"][0]["text_start"] == 5  # One emoji occupies one Python code point.
    assert doc["words"][0]["sentence"] == "🙂中文 Models model, can't self-aware!"
    assert doc["words"][-1]["sentence"] == "A model?"
    expected = Counter(word["lemma"] for word in doc["words"])
    entries = client.get("/api/vocabulary").json()
    assert {word["lemma"]: word["seen_count"] for word in entries} == expected
    assert all(word["lookup_count"] == 0 and word["status"] is None and word["mastery_score"] is None
               for word in entries)
    assert expected["model"] == 4
    assert client.get("/api/events").json() == []
    assert count(factory, ReviewSchedule) == 0
    assert count(factory, TextImportReceipt) == 1
    assert client.get(f"/api/documents/{doc['id']}/image").status_code == 404
    assert client.get("/api/documents").json()[0]["word_count"] == len(doc["words"])
    before = client.get("/api/vocabulary").json()
    assert client.get(f"/api/documents/{doc['id']}").json() == doc
    assert client.get("/api/vocabulary").json() == before
    assert not list(settings.storage_dir.rglob("*.png"))


def test_retry_after_lost_response_is_exactly_once_and_returns_current_status(text_app):
    client, factory, _ = text_app
    body = payload("Model models.\r\nA model!", title="  Article  ")
    first = post(client, body)
    client.put("/api/vocabulary/model", json={"status": "UNKNOWN", "document_id": first["id"]})
    before_vocab = client.get("/api/vocabulary").json()
    before_events = client.get("/api/events").json()
    retried = post(client, {**body, "text": body["text"].replace("\r\n", "\n"), "title": "Article"})
    assert retried["id"] == first["id"]
    assert all(word["status"] == "UNKNOWN" for word in retried["words"] if word["lemma"] == "model")
    assert count(factory, Document) == count(factory, TextImportReceipt) == 1
    assert client.get("/api/vocabulary").json() == before_vocab
    assert client.get("/api/events").json() == before_events
    for changed in ({**body, "text": "Another article."}, {**body, "title": "Different"}):
        assert client.post("/api/documents/text", json=changed).status_code == 409
    assert client.get("/api/vocabulary").json() == before_vocab


def test_new_request_id_intentionally_imports_another_copy(text_app):
    client, factory, _ = text_app
    first, second = post(client), post(client)
    assert first["id"] != second["id"]
    assert count(factory, Document) == count(factory, TextImportReceipt) == 2
    assert next(word for word in client.get("/api/vocabulary").json() if word["lemma"] == "model")["seen_count"] == 6


@pytest.mark.parametrize("body", [
    {"text": "", "request_id": str(uuid4())},
    {"text": "  \n 中文123!? 🙂", "request_id": str(uuid4())},
    {"text": "x" * 151, "request_id": str(uuid4())},
    {"text": "x" * 20_001, "request_id": str(uuid4())},
    {"text": "Model", "title": "t" * 251, "request_id": str(uuid4())},
    {"text": "Model", "title": "one\ntwo", "request_id": str(uuid4())},
    {"text": "Model", "title": "bad\0title", "request_id": str(uuid4())},
    {"text": "Model\0x", "request_id": str(uuid4())},
    {"text": "Model", "request_id": "invalid"},
    {"text": "Model"},
    {"text": 123, "request_id": str(uuid4())},
    {"text": "Model", "title": 123, "request_id": str(uuid4())},
])
def test_invalid_import_returns_422_without_partial_data(text_app, body):
    client, factory, _ = text_app
    response = client.post("/api/documents/text", json=body)
    assert response.status_code == 422, response.text
    for model in (Document, WordOccurrence, Vocabulary, LearningEvent, ReviewSchedule, TextImportReceipt):
        assert count(factory, model) == 0


def test_complete_long_token_is_skipped_without_truncating_into_a_fake_word(text_app):
    client, _, _ = text_app
    doc = post(client, payload("x" * 151 + " can't model " + "y" * 200))
    assert [word["word"] for word in doc["words"]] == ["can't", "model"]
    max_length = post(client, payload("x" * 150))
    assert [word["word"] for word in max_length["words"]] == ["x" * 150]


def test_20000_code_points_accepted_and_title_is_optional(text_app):
    client, _, _ = text_app
    content = "\r\n" + "🙂" * 19_991 + " Model. "  # 20,000 after newline normalization.
    assert len(content.replace("\r\n", "\n")) == 20_000
    doc = post(client, {"text": content, "request_id": str(uuid4())})
    assert len(doc["ocr_text"]) == 20_000
    assert len(doc["title"]) == 80
    assert doc["words"][0]["word"] == "Model"
    blank = post(client, payload("\n\n  First line  \nSecond line", title="   "))
    assert blank["title"] == "First line"


def test_existing_status_lookup_and_schedule_survive_observation(text_app):
    client, factory, _ = text_app
    client.put("/api/vocabulary/model", json={"status": "KNOWN"})
    with factory() as session:
        schedule = session.scalar(select(ReviewSchedule))
        before = (schedule.due_at, schedule.known_streak)
    doc = post(client)
    entry = next(word for word in client.get("/api/vocabulary").json() if word["lemma"] == "model")
    assert entry["status"] == "KNOWN" and entry["mastery_score"] == .65
    assert entry["seen_count"] == 3 and entry["lookup_count"] == 0
    assert all(word["status"] == "KNOWN" for word in doc["words"] if word["lemma"] == "model")
    with factory() as session:
        schedule = session.scalar(select(ReviewSchedule))
        assert (schedule.due_at, schedule.known_streak) == before
    assert len(client.get("/api/events").json()) == 1


def test_explicit_text_lookup_uses_selected_occurrence_and_review_jumps_to_source(text_app):
    client, _, _ = text_app
    doc = post(client, payload("Generalization is useful.\nGeneralization helps a machine learning model."))
    selected = next(word for word in reversed(doc["words"]) if word["lemma"] == "generalization")
    analysis = client.post("/api/words/analyze", json={"occurrence_id": selected["id"]})
    assert analysis.status_code == 200 and analysis.json()["source"] == "offline"
    client.put("/api/vocabulary/generalization", json={"status": "UNKNOWN", "document_id": doc["id"]})
    review = client.get("/api/review").json()
    assert review["total"] == 1
    item = review["items"][0]
    assert item["occurrence"]["document_id"] == doc["id"]
    assert item["occurrence"]["document_title"] == "Reading note"
    assert item["occurrence"]["id"] in [word["id"] for word in doc["words"]]
    assert next(word for word in client.get("/api/vocabulary").json()
                if word["lemma"] == "generalization")["lookup_count"] == 1


def test_import_failure_rolls_back_document_occurrences_receipt_and_exposure(text_app, monkeypatch):
    client, factory, _ = text_app
    original = VocabularyService.observe

    def fail_after_first_observation(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.repo.session.flush()
        raise RuntimeError("observation interrupted")

    body = payload()
    with monkeypatch.context() as patch:
        patch.setattr(VocabularyService, "observe", fail_after_first_observation)
        with factory() as session, pytest.raises(RuntimeError, match="observation interrupted"):
            import_text(session, TextImportRequest(**body))
    for model in (Document, WordOccurrence, Vocabulary, TextImportReceipt):
        assert count(factory, model) == 0
    assert post(client, body)["word_count"] == 5


def test_commit_failure_rolls_back_even_after_receipt_and_counts_have_flushed(text_app, monkeypatch):
    client, factory, _ = text_app
    body = payload()
    with factory() as session:
        def fail_commit():
            session.flush()
            assert session.scalar(select(func.count()).select_from(TextImportReceipt)) == 1
            raise RuntimeError("commit interrupted")

        monkeypatch.setattr(session, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="commit interrupted"):
            import_text(session, TextImportRequest(**body))
    for model in (Document, WordOccurrence, Vocabulary, TextImportReceipt):
        assert count(factory, model) == 0
    assert post(client, body)["word_count"] == 5


def test_public_guests_are_isolated_and_no_expensive_budget_is_consumed(text_app):
    client, factory, settings = text_app
    settings.app_mode = "public"
    settings.public_max_documents_per_user = 1
    headers = []
    for _ in range(2):
        response = client.post("/api/session")
        assert response.status_code == 201
        headers.append({"Authorization": "Bearer " + response.json()["token"]})
    body = payload()
    assert client.post("/api/documents/text", json=body).status_code == 401
    first = post(client, body, headers=headers[0])
    # The UUID namespace belongs to its visitor, not to all users.
    second = post(client, body, headers=headers[1])
    assert first["id"] != second["id"]
    with factory() as session:
        buckets = session.connection().exec_driver_sql("SELECT bucket FROM public_usage").scalars().all()
        assert all(bucket.startswith("signup:") for bucket in buckets)
    for own, other, auth in ((first, second, headers[0]), (second, first, headers[1])):
        assert [doc["id"] for doc in client.get("/api/documents", headers=auth).json()] == [own["id"]]
        assert client.get(f"/api/documents/{other['id']}", headers=auth).status_code == 404
        assert client.get(f"/api/documents/{other['id']}/image", headers=auth).status_code == 404
        assert client.post("/api/words/analyze", json={"occurrence_id": other["words"][0]["id"]}, headers=auth).status_code == 404
        assert post(client, body, headers=auth)["id"] == own["id"]  # Retry even at capacity.
        assert client.post("/api/documents/text", json=payload(), headers=auth).status_code == 409
    assert count(factory, Document) == count(factory, TextImportReceipt) == 2
    # The rejected explicit lookup above does reserve LLM budget; text imports
    # themselves must leave all OCR usage absent.
    with factory() as session:
        names = session.connection().exec_driver_sql("SELECT name FROM sqlite_master WHERE name='public_usage'").fetchall()
        if names:
            buckets = session.connection().exec_driver_sql("SELECT bucket FROM public_usage").scalars().all()
            assert not any("ocr" in bucket for bucket in buckets)


@pytest.mark.parametrize("same_request", [True, False])
def test_concurrent_imports_are_atomic_and_retry_never_double_observes(text_app, same_request):
    _, factory, _ = text_app
    shared = payload()
    bodies = [shared, shared if same_request else payload()]
    gate = Barrier(2)

    def worker(body):
        gate.wait(timeout=10)
        with factory() as session:
            return import_text(session, TextImportRequest(**body)).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(worker, bodies))
    imports = 1 if same_request else 2
    assert len(set(ids)) == imports
    assert count(factory, Document) == count(factory, TextImportReceipt) == imports
    with factory() as session:
        assert session.scalar(select(Vocabulary).where(Vocabulary.lemma == "model")).seen_count == 3 * imports


def test_concurrent_capacity_checks_allow_only_one_import(text_app):
    _, factory, settings = text_app
    settings.app_mode = "public"
    settings.public_max_documents_per_user = 1
    gate = Barrier(2)

    def worker(_):
        gate.wait(timeout=10)
        with factory() as session:
            try:
                return import_text(session, TextImportRequest(**payload()), user_id=2).id
            except HTTPException as exc:
                assert exc.status_code == 409
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(worker, range(2)))
    assert sum(value is not None for value in ids) == 1
    assert count(factory, Document) == count(factory, TextImportReceipt) == 1


def test_concurrent_different_payloads_with_same_uuid_commit_only_one(text_app):
    _, factory, _ = text_app
    original = payload()
    bodies = [original, {**original, "text": "Another article."}]
    gate = Barrier(2)

    def worker(body):
        gate.wait(timeout=10)
        with factory() as session:
            try:
                return import_text(session, TextImportRequest(**body)).id
            except HTTPException as exc:
                assert exc.status_code == 409
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(worker, bodies))
    assert sum(value is not None for value in ids) == 1
    assert count(factory, Document) == count(factory, TextImportReceipt) == 1


def test_008_upgrade_preserves_all_installed_data_and_backs_up_wal(legacy, monkeypatch):
    path, engine, connection = legacy
    versions = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, "MIGRATIONS", versions[:7])
    migrations.migrate_database(engine)
    tables = ("users", "documents", "word_occurrences", "vocabulary", "learning_events",
              "review_schedules", "recommendation_feedback", "feedback_receipts", "vocabulary_relations")
    before = {table: rows(path, f"SELECT * FROM {table}") for table in tables}
    monkeypatch.setattr(migrations, "MIGRATIONS", versions)
    backup = migrations.migrate_database(engine)
    assert backup and "before-008_plain_text_import" in backup.name
    for table, contents in before.items():
        assert rows(backup, f"SELECT * FROM {table}") == contents
        current = rows(path, f"SELECT * FROM {table}")
        if table == "word_occurrences":
            assert current == [{**row, "text_start": None, "text_end": None} for row in contents]
        else:
            assert current == contents
    assert rows(path, "SELECT * FROM text_import_receipts") == []
    assert rows(path, "PRAGMA integrity_check") == [{"integrity_check": "ok"}]
    assert rows(path, "PRAGMA foreign_key_check") == []
    assert migrations.migrate_database(engine) is None
    assert len(list((path.parent / "backups").glob("*.sqlite3"))) == 2
    # Partial installation with DDL already in place keeps existing offsets.
    connection.execute("UPDATE word_occurrences SET text_start=3,text_end=14")
    connection.execute("INSERT INTO text_import_receipts VALUES(1,1,'request','hash',1,'2026-10-17 00:00:00')")
    connection.execute("DELETE FROM schema_migrations WHERE version=?", (versions[-1][0],))
    connection.commit()
    receipts = rows(path, "SELECT * FROM text_import_receipts")
    assert migrations.migrate_database(engine) is not None
    assert rows(path, "SELECT text_start,text_end FROM word_occurrences") == [{"text_start": 3, "text_end": 14}]
    assert rows(path, "SELECT * FROM text_import_receipts") == receipts


def test_008_migration_rolls_back_ddl_and_retries_safely(legacy, monkeypatch):
    path, engine, _ = legacy
    versions = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, "MIGRATIONS", versions[:7])
    migrations.migrate_database(engine)
    before = rows(path, "SELECT * FROM word_occurrences")

    def fail(connection, tables):
        migrations._create_text_imports(connection, tables)
        raise RuntimeError("text import schema interrupted")

    monkeypatch.setattr(migrations, "MIGRATIONS", [*versions[:7], (versions[-1][0], fail)])
    with pytest.raises(RuntimeError, match="schema interrupted"):
        migrations.migrate_database(engine)
    assert rows(path, "SELECT * FROM word_occurrences") == before
    assert rows(path, "SELECT name FROM sqlite_master WHERE name='text_import_receipts'") == []
    assert len(rows(path, "SELECT * FROM schema_migrations")) == 7
    monkeypatch.setattr(migrations, "MIGRATIONS", versions)
    assert migrations.migrate_database(engine) is not None
    assert migrations.migrate_database(engine) is None


def test_receipt_migrated_schema_matches_fresh_install_and_enforces_constraints(legacy, tmp_path):
    path, engine, connection = legacy
    migrations.migrate_database(engine)
    fresh = tmp_path / "fresh-text.sqlite3"
    fresh_engine = create_engine(f"sqlite:///{fresh}")
    Base.metadata.create_all(fresh_engine)
    fresh_engine.dispose()
    for pragma in ("table_info", "foreign_key_list", "index_list"):
        def comparable(db):
            return sorted(tuple(sorted((key, value) for key, value in row.items() if key not in {"seq", "id"}))
                          for row in rows(db, f"PRAGMA {pragma}(text_import_receipts)"))
        assert comparable(path) == comparable(fresh)
    insert = "INSERT INTO text_import_receipts VALUES(?, ?, ?, ?, ?, '2026-10-17 00:00:00')"
    connection.execute(insert, (1, 1, "request", "hash", 1))
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        connection.execute(insert, (2, 1, "request", "hash", 1))
    for user, document in [(999, 1), (1, 999)]:
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute(insert, (2, user, "other", "hash", document))
    connection.rollback()
