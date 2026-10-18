"""Real SQLite transactions and API behavior using synthetic, offline articles."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from threading import Barrier, Event
from uuid import uuid4
from zipfile import ZipFile

from fastapi import HTTPException
import pytest
from sqlalchemy import func, select, text

from backend import migrations
from backend.models import Document, LearningEvent, ReviewSchedule, TextImportReceipt, Vocabulary, WordOccurrence
from backend.schemas import UrlImportRequest
from backend.services import url_import
from backend.services.web_fetch import normalize_url
from backend.tests.test_backups import archive_data, preview, restore, backup_app  # noqa: F401
from backend.tests.test_pdf_import import pdf_app  # noqa: F401
from backend.tests.test_text_import import text_app, post, payload  # noqa: F401


CONTENT = "🙂中文 Models are useful in science. A model helps readers understand a difficult question.\n\nThe models can learn from reliable examples and offer a clear explanation."
ARTICLE = f"<html><head><title>Example reading</title></head><body><article><h1>Example reading</h1><p>{CONTENT.split(chr(10))[0]}</p><p>{CONTENT.split(chr(10))[-1]}</p></article></body></html>"


@pytest.fixture
def url_app(text_app, monkeypatch):
    calls = []

    def fetch(url):
        calls.append(url)
        return ARTICLE, "https://news.example.com/article"

    monkeypatch.setattr(url_import, "fetch_page", fetch)
    return *text_app, calls


def body(url="https://example.com/start", request_id=None):
    return {"url": url, "request_id": request_id or str(uuid4())}


def import_post(client, payload=None):
    response = client.post("/api/documents/url", json=payload or body())
    assert response.status_code == 201, response.text
    return response.json()


def count(factory, model):
    with factory() as session:
        return session.scalar(select(func.count()).select_from(model))


def test_import_exact_unicode_words_no_ocr_ai_or_learning_signals(url_app, monkeypatch):
    client, factory, settings, calls = url_app

    def unexpected(*_args, **_kwargs):
        pytest.fail("Web import must not call OCR or LLM")

    monkeypatch.setattr("backend.services.documents.get_ocr_service", unexpected)
    monkeypatch.setattr("backend.services.llm.LLMService.analyze_word", unexpected)
    monkeypatch.setattr("backend.services.llm.LLMService.recommend_related_words", unexpected)
    doc = import_post(client)
    assert doc["source"] == "web" and doc["source_url"] == "https://news.example.com/article"
    assert doc["image_url"] is None and doc["pdf_url"] is None and doc["pages"] == []
    assert doc["width"] == doc["height"] == 0 and doc["title"] == "Example reading"
    for word in doc["words"]:
        assert doc["ocr_text"][word["text_start"]:word["text_end"]] == word["word"]
        assert word["bbox_source"] == "web" and word["bbox"] is word["confidence"] is None
        assert word["status"] is None and word["word"] in word["sentence"]
    assert "🙂中文 Models" in doc["ocr_text"]
    first_model = next(word for word in doc["words"] if word["word"] == "Models")
    assert first_model["text_start"] == doc["ocr_text"].index("Models")
    counts = Counter(word["lemma"] for word in doc["words"])
    vocabulary = client.get("/api/vocabulary").json()
    assert {entry["lemma"]: entry["seen_count"] for entry in vocabulary} == counts
    assert all(entry["lookup_count"] == 0 and entry["status"] is None and entry["mastery_score"] is None for entry in vocabulary)
    assert count(factory, LearningEvent) == count(factory, ReviewSchedule) == 0
    assert count(factory, TextImportReceipt) == count(factory, Document) == 1
    summary = client.get("/api/documents?source=web").json()
    assert summary[0]["source_url"] == doc["source_url"] and summary[0]["word_count"] == len(doc["words"])
    assert client.get(f"/api/documents/{doc['id']}/image").status_code == 404
    assert client.get(f"/api/documents/{doc['id']}/pdf").status_code == 404
    assert len(calls) == 1 and not list(settings.storage_dir.rglob("*.png"))


def test_lost_response_retry_uses_receipt_not_remote_content_and_current_status(url_app, monkeypatch):
    client, factory, _, calls = url_app
    request = body("HTTPS://EXAMPLE.COM:443/start#one")
    first = import_post(client, request)
    client.put("/api/vocabulary/model", json={"status": "UNKNOWN", "document_id": first["id"]})
    before = client.get("/api/vocabulary").json()
    events = client.get("/api/events").json()
    monkeypatch.setattr(url_import, "fetch_page", lambda *_: pytest.fail("retry refetched changed webpage"))
    retry = import_post(client, {**request, "url": "https://example.com/start#two"})
    assert retry["id"] == first["id"] and count(factory, TextImportReceipt) == 1
    assert all(word["status"] == "UNKNOWN" for word in retry["words"] if word["lemma"] == "model")
    assert client.get("/api/vocabulary").json() == before and client.get("/api/events").json() == events


def test_conflicting_uuid_and_text_uuid_do_not_fetch(url_app):
    client, factory, _, calls = url_app
    request = body()
    import_post(client, request)
    response = client.post("/api/documents/url", json={**request, "url": "https://other.example.com/story"})
    assert response.status_code == 409 and len(calls) == 1
    text_request = payload(request_id=str(uuid4()))
    post(client, text_request)
    assert client.post("/api/documents/url", json=body(request_id=text_request["request_id"])).status_code == 409
    assert len(calls) == 1 and count(factory, Document) == 2


def test_retry_trash_requires_restore_and_owner_isolation(url_app):
    client, factory, _, calls = url_app
    request = body()
    first = import_post(client, request)
    client.delete(f"/api/documents/{first['id']}")
    assert client.post("/api/documents/url", json=request).status_code == 409 and len(calls) == 1
    client.post(f"/api/documents/{first['id']}/restore")
    assert import_post(client, request)["id"] == first["id"] and len(calls) == 1
    with factory() as session:
        second = url_import.import_url(session, UrlImportRequest(**request), user_id=2)
        assert second.user_id == 2 and second.id != first["id"]
    assert len(calls) == 2 and len(client.get("/api/documents?source=web").json()) == 1


@pytest.mark.parametrize("field,value", [("request_id", "invalid"), ("url", ""), ("url", 123), ("url", "x" * 2049)])
def test_invalid_request_schema_never_fetches(url_app, field, value):
    client, _, _, calls = url_app
    assert client.post("/api/documents/url", json={**body(), field: value}).status_code == 422
    assert calls == []


def test_errors_leave_no_rows_and_same_request_can_retry(url_app, monkeypatch):
    client, factory, _, calls = url_app
    original = url_import.fetch_page
    monkeypatch.setattr(url_import, "fetch_page", lambda *_: (_ for _ in ()).throw(HTTPException(504, "网页读取超时")))
    request = body()
    assert client.post("/api/documents/url", json=request).status_code == 504
    assert count(factory, Document) == count(factory, Vocabulary) == count(factory, TextImportReceipt) == 0
    monkeypatch.setattr(url_import, "fetch_page", original)
    assert import_post(client, request)["id"] > 0 and len(calls) == 1


def test_extraction_rejection_and_observation_failure_rollback_everything(url_app, monkeypatch):
    client, factory, _, calls = url_app
    original = url_import.fetch_page
    monkeypatch.setattr(url_import, "fetch_page", lambda *_: ("<html><p>登录后继续阅读。</p></html>", "https://example.com/"))
    assert client.post("/api/documents/url", json=body()).status_code == 422
    assert count(factory, Document) == count(factory, Vocabulary) == count(factory, TextImportReceipt) == 0
    monkeypatch.setattr(url_import, "fetch_page", original)

    def fail(*_args, **_kwargs):
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(url_import.VocabularyService, "observe", fail)
    with factory() as session, pytest.raises(RuntimeError):
        url_import.import_url(session, UrlImportRequest(**body()))
    assert count(factory, Document) == count(factory, WordOccurrence) == count(factory, Vocabulary) == count(factory, TextImportReceipt) == 0


def test_concurrent_same_request_fetches_and_observes_once(url_app, monkeypatch):
    _, factory, _, _ = url_app
    barrier, started, release = Barrier(2), Event(), Event()
    calls = []

    def fetch(url):
        calls.append(url)
        started.set()
        assert release.wait(5)
        return ARTICLE, "https://news.example.com/article"

    monkeypatch.setattr(url_import, "fetch_page", fetch)
    request = UrlImportRequest(**body())

    def execute():
        barrier.wait(timeout=5)
        with factory() as session:
            return url_import.import_url(session, request).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(execute) for _ in range(2)]
        assert started.wait(5)
        # Unrelated SQLite writes must remain possible while the fetch waits.
        with factory() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            session.commit()
        release.set()
        identifiers = [result.result(timeout=10) for result in results]
    assert identifiers[0] == identifiers[1] and len(calls) == 1
    assert count(factory, Document) == count(factory, TextImportReceipt) == 1
    with factory() as session:
        assert session.scalar(select(func.sum(Vocabulary.seen_count))) == count(factory, WordOccurrence)


def test_public_capacity_is_checked_before_fetch(url_app):
    client, factory, settings, calls = url_app
    settings.app_mode = "public"
    settings.public_max_documents_per_user = 1
    with factory() as session:
        url_import.import_url(session, UrlImportRequest(**body()), user_id=1)
    with factory() as session, pytest.raises(HTTPException) as error:
        url_import.import_url(session, UrlImportRequest(**body()), user_id=1)
    assert error.value.status_code == 409 and len(calls) == 1


def test_complete_backup_restores_web_text_source_link_offsets_and_bookmark(backup_app, monkeypatch):
    client, factory, _ = backup_app
    monkeypatch.setattr(url_import, "fetch_page", lambda *_: (ARTICLE, "https://news.example.com/article"))
    document = import_post(client)
    word = document["words"][-1]
    client.put(f"/api/documents/{document['id']}", json={"tags": ["Web"], "bookmark_occurrence_id": word["id"]})
    client.delete(f"/api/documents/{document['id']}")
    data = archive_data(client)
    with ZipFile(BytesIO(data)) as archive:
        assert set(archive.namelist()) == {"manifest.json", "data.sqlite3"}
        assert json.loads(archive.read("manifest.json"))["documents"] == {str(document["id"]): None}
    pre = preview(client, data)
    assert pre.status_code == 200 and pre.json()["file_count"] == 0
    assert restore(client, pre.json()["token"]).status_code == 200
    client.post(f"/api/documents/{document['id']}/restore")
    restored = client.get(f"/api/documents/{document['id']}").json()
    assert restored["source_url"] == document["source_url"] and restored["source"] == "web"
    assert restored["ocr_text"] == document["ocr_text"] and restored["words"] == document["words"]
    assert restored["tags"] == ["Web"] and restored["bookmark_occurrence_id"] == word["id"]


def test_012_migration_preserves_preexisting_values_and_initializes_null_url(text_app, tmp_path):
    client, factory, _ = text_app
    document = post(client)
    engine = factory.kw["bind"]
    migrations.migrate_database(engine)
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM schema_migrations WHERE version='012_web_import'"))
        connection.execute(text("ALTER TABLE documents DROP COLUMN source_url"))
        before = list(connection.execute(text("SELECT id,title,ocr_text,source,pages,tags FROM documents")))
    backup = migrations.migrate_database(engine)
    assert backup is not None and backup.is_file()
    with engine.connect() as connection:
        assert list(connection.execute(text("SELECT id,title,ocr_text,source,pages,tags FROM documents"))) == before
        assert connection.execute(text("SELECT source_url FROM documents")).scalar() is None
        assert connection.execute(text("PRAGMA integrity_check")).scalar() == "ok"
        assert list(connection.execute(text("PRAGMA foreign_key_check"))) == []
    assert migrations.migrate_database(engine) is None
