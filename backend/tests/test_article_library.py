"""Article metadata and reversible removal preserve every learning signal."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import event, text

from backend import migrations
from backend.agents.vocabulary import VocabularyAgent
from backend.models import Document
from backend.repositories.vocabulary import VocabularyRepository
from backend.schemas import DocumentUpdateRequest
from backend.services.documents import change_document_deleted, update_document
from backend.tests.test_migrations import legacy, rows  # noqa: F401
from backend.tests.test_text_import import payload, post, text_app  # noqa: F401


LEARNING_TABLES = ("vocabulary", "learning_events", "review_receipts", "review_schedules",
                   "recommendation_feedback", "feedback_receipts", "vocabulary_relations",
                   "text_import_receipts", "word_occurrences")


def snapshot(factory):
    with factory() as session:
        return {table: [dict(row) for row in session.execute(text(f"SELECT * FROM {table} ORDER BY 1")).mappings()]
                for table in LEARNING_TABLES}


def document_url(doc):
    return f"/api/documents/{doc['id']}"


def test_metadata_normalizes_tags_and_bookmark_without_learning_writes(text_app):
    client, factory, _ = text_app
    doc = post(client, payload("Model model.\n\nAnother model!"))
    before = snapshot(factory)
    change = {"title": "  My reading note  ", "tags": ["  Study ", "study", "中文", "Straße", "STRASSE"],
              "bookmark_occurrence_id": doc["words"][1]["id"]}
    response = client.put(document_url(doc), json=change)
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["title"] == "My reading note"
    assert summary["tags"] == ["Study", "中文", "Straße"]
    assert summary["bookmark_occurrence_id"] == doc["words"][1]["id"]
    assert summary["excerpt"] == "Model model. Another model!"
    assert summary["word_count"] == len(doc["words"]) and summary["deleted_at"] is None
    assert client.get(document_url(doc)).json()["bookmark_occurrence_id"] == doc["words"][1]["id"]
    assert client.get("/api/documents").json() == [summary]
    assert snapshot(factory) == before
    response = client.put(document_url(doc), json={"bookmark_occurrence_id": None, "tags": []})
    assert response.status_code == 200
    assert response.json()["bookmark_occurrence_id"] is None and response.json()["tags"] == []
    assert snapshot(factory) == before


@pytest.mark.parametrize("patch", [
    {}, {"title": None}, {"title": " "}, {"title": 12}, {"title": "x" * 251},
    {"title": "bad\nname"}, {"title": "bad\x7fname"}, {"title": "bad\ud800name"},
    {"tags": None}, {"tags": "Study"}, {"tags": [12]}, {"tags": [""]},
    {"tags": [" "]}, {"tags": ["x" * 21]}, {"tags": [str(n) for n in range(11)]},
    {"tags": ["line\nbreak"]}, {"tags": ["bad\ud800tag"]},
    {"bookmark_occurrence_id": 0}, {"bookmark_occurrence_id": -1},
    {"bookmark_occurrence_id": True}, {"bookmark_occurrence_id": "1"}, {"bookmark_occurrence_id": 1.2},
    {"source": "demo"}, {"deleted_at": "2026-01-01"},
    {"title": "bad\u0085name"}, {"tags": ["line\u2028separator"]},
    {"bookmark_occurrence_id": 2 ** 63},
    {"bad\ud800field": "value"},
])
def test_invalid_metadata_is_rejected_atomically(text_app, patch):
    client, factory, _ = text_app
    doc = post(client)
    before = snapshot(factory)
    response = client.put(document_url(doc), content=json.dumps(patch), headers={"Content-Type": "application/json"})
    assert response.status_code == 422, response.text
    assert client.get(document_url(doc)).json() == doc
    assert snapshot(factory) == before


def test_bookmark_requires_occurrence_in_same_document(text_app):
    client, factory, _ = text_app
    first, other = post(client), post(client)
    before = snapshot(factory)
    for occurrence_id in (other["words"][0]["id"], 99999):
        response = client.put(document_url(first), json={"title": "Must roll back", "bookmark_occurrence_id": occurrence_id})
        assert response.status_code == 422
        assert client.get(document_url(first)).json() == first
    assert snapshot(factory) == before


def test_search_tags_source_order_and_summary_use_single_aggregate_query(text_app):
    client, factory, _ = text_app
    first = post(client, payload("Literal 100% and snake_case words.", "zeta 中文"))
    second = post(client, payload("An ARTICLE with Straße and ideas.", "Alpha reading"))
    client.put(document_url(first), json={"tags": ["Study", "中文"]})
    client.put(document_url(second), json={"tags": ["Another", "Straße"]})
    ids = lambda **params: [doc["id"] for doc in client.get("/api/documents", params=params).json()]
    assert ids() == [second["id"], first["id"]]
    assert ids(sort="oldest") == [first["id"], second["id"]]
    assert ids(sort="title") == [second["id"], first["id"]]
    for query in ("100%", "_", "中文", "ZETA"):
        assert ids(q=query) == [first["id"]]
    assert ids(q="article") == [second["id"]]
    assert ids(q="STRASSE") == [second["id"]]
    assert ids(q="%") == [first["id"]]
    assert ids(tag="study") == [first["id"]]
    assert ids(tag="STRASSE") == [second["id"]]
    assert ids(tag="Stu") == []
    assert ids(source="text") == [second["id"], first["id"]]
    assert ids(source="upload") == []
    assert ids(deleted=True) == []
    for params in ({"q": "x" * 201}, {"tag": "x" * 21}, {"source": "bad"}, {"sort": "bad"}):
        assert client.get("/api/documents", params=params).status_code == 422
    statements = []
    engine = factory.kw["bind"]

    def record(_connection, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        summaries = client.get("/api/documents").json()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert len(statements) == 1
    assert "count(word_occurrences.id)" in statements[0]
    assert all("words" not in summary and summary["word_count"] > 0 for summary in summaries)


def test_delete_restore_preserves_learning_receipts_and_import_retry_semantics(text_app):
    client, factory, _ = text_app
    body = payload("Generalization in machine learning helps a model learn from data.")
    doc = post(client, body)
    occurrence = doc["words"][0]
    assert client.post("/api/words/analyze", json={"occurrence_id": occurrence["id"]}).status_code == 200
    recommendation = client.post("/api/words/recommend", json={"occurrence_id": occurrence["id"]}).json()
    offered = recommendation["items"][0]["word"]
    feedback = {"word": offered, "action": "UNKNOWN", "request_id": str(uuid4())}
    feedback_url = f"/api/recommendations/{recommendation['batch_id']}/feedback"
    assert client.post(feedback_url, json=feedback).status_code == 200
    mark = {"status": "UNKNOWN", "document_id": doc["id"], "origin": "review", "request_id": str(uuid4())}
    assert client.put("/api/vocabulary/generalization", json=mark).status_code == 200
    before = snapshot(factory)
    response = client.delete(document_url(doc))
    assert response.status_code == 200
    removed = response.json()
    assert removed["deleted_at"].endswith("+00:00")
    assert client.delete(document_url(doc)).json() == removed
    assert client.get("/api/documents").json() == []
    assert client.get("/api/documents?deleted=true").json() == [removed]
    assert client.get("/api/dashboard").json()["summary"]["document_count"] == 0
    assert client.post("/api/documents/text", json=body).status_code == 409
    assert client.put("/api/vocabulary/generalization", json=mark).status_code == 200
    assert client.post(feedback_url, json=feedback).status_code == 200
    assert client.post(feedback_url, json={**feedback, "action": "KNOWN", "request_id": str(uuid4())}).status_code == 404
    assert snapshot(factory) == before
    response = client.post(document_url(doc) + "/restore")
    assert response.status_code == 200 and response.json()["deleted_at"] is None
    assert client.post(document_url(doc) + "/restore").json() == response.json()
    assert client.get(document_url(doc)).json()["ocr_text"] == body["text"]
    assert client.post("/api/documents/text", json=body).json()["id"] == doc["id"]
    assert snapshot(factory) == before


def test_deleted_articles_reject_every_reader_and_agent_path_but_keep_review_word(text_app, monkeypatch):
    client, factory, _ = text_app
    doc = post(client)
    occurrence = doc["words"][0]
    client.put("/api/vocabulary/model", json={"status": "UNKNOWN", "document_id": doc["id"]})
    assert client.get("/api/review").json()["items"][0]["occurrence"]["document_id"] == doc["id"]
    client.delete(document_url(doc))
    before = snapshot(factory)

    def forbidden(*args, **kwargs):
        pytest.fail("Deleted articles must not reach the model")

    monkeypatch.setattr("backend.services.llm.LLMService.analyze_word", forbidden)
    monkeypatch.setattr("backend.services.llm.LLMService.recommend_related_words", forbidden)
    assert client.get(document_url(doc)).status_code == 404
    assert client.get(document_url(doc) + "/image").status_code == 404
    assert client.put(document_url(doc), json={"title": "Cannot edit"}).status_code == 404
    for endpoint in ("analyze", "recommend"):
        assert client.post(f"/api/words/{endpoint}", json={"occurrence_id": occurrence["id"]}).status_code == 404
    for review_fields in ({}, {"origin": "review"}, {"origin": "review", "request_id": str(uuid4())}):
        assert client.put("/api/vocabulary/model", json={"status": "KNOWN", "document_id": doc["id"], **review_fields}).status_code == 404
    queue = client.get("/api/review").json()
    assert queue["total"] == 1 and queue["items"][0]["occurrence"] is None
    with factory() as session:
        deleted = session.get(Document, doc["id"])
        with pytest.raises(HTTPException) as exc:
            VocabularyAgent(VocabularyRepository(session)).build_context(deleted.words[0], deleted)
        assert exc.value.status_code == 404
    assert snapshot(factory) == before
    assert client.put("/api/vocabulary/model", json={"status": "KNOWN"}).status_code == 200


def test_review_context_uses_latest_active_article_and_rename(text_app):
    client, _, _ = text_app
    first = post(client)
    second = post(client)
    client.put("/api/vocabulary/model", json={"status": "UNKNOWN"})
    assert client.get("/api/review").json()["items"][0]["occurrence"]["document_id"] == second["id"]
    client.delete(document_url(second))
    client.put(document_url(first), json={"title": "Renamed source"})
    context = client.get("/api/review").json()["items"][0]["occurrence"]
    assert context["document_id"] == first["id"] and context["document_title"] == "Renamed source"
    client.post(document_url(second) + "/restore")
    assert client.get("/api/review").json()["items"][0]["occurrence"]["document_id"] == second["id"]


def test_demo_never_reuses_deleted_article_and_ids_remain_unique(text_app):
    client, _, _ = text_app
    first = client.post("/api/documents/demo").json()
    client.delete(document_url(first))
    second = client.post("/api/documents/demo").json()
    assert second["id"] != first["id"]
    assert {word["id"] for word in second["words"]}.isdisjoint(word["id"] for word in first["words"])
    assert client.post("/api/documents/demo").json()["id"] == second["id"]


def test_restore_active_capacity_is_atomic_and_idempotent(text_app):
    client, factory, settings = text_app
    first, second = post(client), post(client)
    settings.app_mode = "public"
    settings.public_max_documents_per_user = 1
    with factory() as session:
        change_document_deleted(session, first["id"], 1, restore=False)
    before = snapshot(factory)
    with factory() as session:
        with pytest.raises(HTTPException) as exc:
            change_document_deleted(session, first["id"], 1, restore=True)
        assert exc.value.status_code == 409
        assert session.get(Document, first["id"]).deleted_at is not None
        session.rollback()
        assert change_document_deleted(session, second["id"], 1, restore=True).deleted_at is None
    assert snapshot(factory) == before
    with factory() as session:
        change_document_deleted(session, second["id"], 1, restore=False)
        assert change_document_deleted(session, first["id"], 1, restore=True).deleted_at is None


def test_public_users_cannot_manage_or_filter_another_users_trash(text_app):
    client, factory, settings = text_app
    settings.app_mode = "public"
    users = [client.post("/api/session").json() for _ in range(2)]
    headers = [{"Authorization": f"Bearer {user['token']}"} for user in users]
    first = post(client, headers=headers[0])
    other = post(client, headers=headers[1])
    assert client.delete(document_url(first), headers=headers[0]).status_code == 200
    before = snapshot(factory)
    for auth, forbidden in ((headers[0], other), (headers[1], first)):
        url = document_url(forbidden)
        assert client.get(url, headers=auth).status_code == 404
        assert client.get(url + "/image", headers=auth).status_code == 404
        assert client.put(url, json={"title": "Forbidden"}, headers=auth).status_code == 404
        assert client.delete(url, headers=auth).status_code == 404
        assert client.post(url + "/restore", headers=auth).status_code == 404
    assert [doc["id"] for doc in client.get("/api/documents?deleted=true", headers=headers[0]).json()] == [first["id"]]
    assert client.get("/api/documents?deleted=true", headers=headers[1]).json() == []
    assert client.get("/api/documents").status_code == 401
    assert client.delete(document_url(first)).status_code == 401
    assert snapshot(factory) == before


@pytest.mark.parametrize("action", ["update", "trash", "restore"])
def test_failed_metadata_commit_rolls_back_document_and_learning(text_app, monkeypatch, action):
    client, factory, _ = text_app
    doc = post(client)
    if action == "restore":
        client.delete(document_url(doc))
    with factory() as session:
        original = dict(session.execute(text("SELECT * FROM documents")).mappings().one())
        session.rollback()
        before = snapshot(factory)

        def failed_commit():
            raise RuntimeError("simulated database failure")

        monkeypatch.setattr(session, "commit", failed_commit)
        with pytest.raises(RuntimeError, match="simulated database failure"):
            if action == "update":
                update_document(session, doc["id"], DocumentUpdateRequest(title="Must roll back", tags=["Tag"]), 1)
            else:
                change_document_deleted(session, doc["id"], 1, restore=action == "restore")
        assert dict(session.execute(text("SELECT * FROM documents")).mappings().one()) == original
    assert snapshot(factory) == before


def test_009_migration_backup_preserves_original_columns_and_all_learning(legacy, monkeypatch):
    path, engine, connection = legacy
    versions = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, "MIGRATIONS", versions[:8])
    migrations.migrate_database(engine)
    existing = {row["name"] for row in rows(path, "SELECT name FROM sqlite_master WHERE type='table'")}
    tables = [table for table in ("documents", "users", *LEARNING_TABLES) if table in existing]
    before = {table: rows(path, f"SELECT * FROM {table} ORDER BY 1") for table in tables}
    monkeypatch.setattr(migrations, "MIGRATIONS", versions)
    backup = migrations.migrate_database(engine)
    assert backup and "before-009_article_library" in backup.name
    for table, contents in before.items():
        assert rows(backup, f"SELECT * FROM {table} ORDER BY 1") == contents
        expected = ([{**row, "tags": "[]", "bookmark_occurrence_id": None, "deleted_at": None} for row in contents]
                    if table == "documents" else contents)
        assert rows(path, f"SELECT * FROM {table} ORDER BY 1") == expected
    assert rows(path, "PRAGMA integrity_check") == [{"integrity_check": "ok"}]
    assert rows(path, "PRAGMA foreign_key_check") == []
    assert migrations.migrate_database(engine) is None
    connection.execute("UPDATE documents SET tags='[\"Legacy\"]', bookmark_occurrence_id=1, deleted_at='2026-01-01'")
    connection.execute("DELETE FROM schema_migrations WHERE version=?", (versions[-1][0],))
    connection.commit()
    metadata = rows(path, "SELECT * FROM documents")
    assert migrations.migrate_database(engine) is not None
    assert rows(path, "SELECT * FROM documents") == metadata


def test_009_schema_failure_rolls_back_and_retries(legacy, monkeypatch):
    path, engine, _ = legacy
    versions = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, "MIGRATIONS", versions[:8])
    migrations.migrate_database(engine)
    before = rows(path, "SELECT * FROM documents")

    def failed_upgrade(connection, tables):
        migrations._create_article_library(connection, tables)
        raise RuntimeError("library upgrade interrupted")

    monkeypatch.setattr(migrations, "MIGRATIONS", [*versions[:8], (versions[-1][0], failed_upgrade)])
    with pytest.raises(RuntimeError, match="library upgrade interrupted"):
        migrations.migrate_database(engine)
    assert rows(path, "SELECT * FROM documents") == before
    assert len(rows(path, "SELECT * FROM schema_migrations")) == 8
    monkeypatch.setattr(migrations, "MIGRATIONS", versions)
    assert migrations.migrate_database(engine) is not None
    assert migrations.migrate_database(engine) is None


@pytest.mark.parametrize("endpoint,method", [("analyze", "analyze_word"), ("recommend", "recommend_related_words")])
def test_slow_ai_result_cannot_write_learning_after_other_tab_trashes_article(text_app, monkeypatch, endpoint, method):
    from backend.services.llm import LLMService

    client, factory, _ = text_app
    doc = post(client, payload("Generalization in machine learning is useful."))
    original = getattr(LLMService, method)
    before = snapshot(factory)

    def respond_after_delete(self, *args, **kwargs):
        with factory() as session:
            change_document_deleted(session, doc["id"], 1, restore=False)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(LLMService, method, respond_after_delete)
    result = client.post(f"/api/words/{endpoint}", json={"occurrence_id": doc["words"][0]["id"]})
    assert result.status_code == 404, result.text
    assert snapshot(factory) == before


def test_concurrent_restores_cannot_exceed_active_capacity(text_app):
    client, factory, settings = text_app
    docs = [post(client), post(client)]
    for doc in docs:
        client.delete(document_url(doc))
    settings.app_mode = "public"
    settings.public_max_documents_per_user = 1
    gate = Barrier(2)
    before = snapshot(factory)

    def worker(doc):
        gate.wait(timeout=10)
        with factory() as session:
            try:
                return change_document_deleted(session, doc["id"], 1, restore=True).id
            except HTTPException as exc:
                assert exc.status_code == 409
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        restored = list(pool.map(worker, docs))
    assert sum(value is not None for value in restored) == 1
    assert snapshot(factory) == before
