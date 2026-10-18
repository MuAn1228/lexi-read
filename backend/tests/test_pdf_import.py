"""PDF page boundaries, durable retries, private files, and additive upgrades."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
import pytest
from sqlalchemy import text

from backend import migrations
from backend.models import Document, ReviewSchedule, TextImportReceipt, WordOccurrence
from backend.services import pdf_import
from backend.services.pdf_import import import_pdf
from backend.tests.test_article_library import snapshot
from backend.tests.test_migrations import legacy, rows  # noqa: F401
from backend.tests.test_text_import import count, payload, post, text_app  # noqa: F401


def pdf_bytes(pages=("Model model without punctuation", "", "Another model. Read again!"), encrypted=False):
    """A small real PDF with no extra test-only dependency."""
    writer = PdfWriter()
    for content in pages:
        page = writer.add_blank_page(width=612, height=792)
        if content:
            stream = DecodedStreamObject()
            escaped = content.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream.set_data(f"BT /F1 12 Tf 72 700 Td ({escaped}) Tj ET".encode("cp1252"))
            page[NameObject("/Contents")] = writer._add_object(stream)
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({
                NameObject("/F1"): DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})})})
    if encrypted:
        writer.encrypt("password")
    stream = BytesIO()
    writer.write(stream)
    return stream.getvalue()


@pytest.fixture
def pdf_app(text_app, monkeypatch):
    client, factory, settings = text_app
    monkeypatch.setattr(pdf_import, "get_settings", lambda: settings)
    return client, factory, settings


def upload(client, data=None, request_id=None, filename="Reading.pdf", **kwargs):
    return client.post("/api/documents/pdf", files={"file": (filename, data if data is not None else pdf_bytes(),
                                                            "application/pdf")},
                       data={"request_id": request_id or str(uuid4())}, **kwargs)


def imported(client, **kwargs):
    response = upload(client, **kwargs)
    assert response.status_code == 201, response.text
    return response.json()


def test_pdf_pages_repeated_tokens_empty_page_global_offsets_and_readonly(pdf_app, monkeypatch):
    client, factory, settings = pdf_app
    def unexpected(*args, **kwargs):
        pytest.fail("PDF extraction must not use OCR or AI")
    monkeypatch.setattr("backend.services.documents.get_ocr_service", unexpected)
    monkeypatch.setattr("backend.services.llm.LLMService.analyze_word", unexpected)
    monkeypatch.setattr("backend.services.llm.LLMService.recommend_related_words", unexpected)
    data = pdf_bytes()
    doc = imported(client, data=data, filename="School.Note.PDF")
    assert doc["source"] == "pdf" and doc["title"] == "School.Note"
    assert doc["image_url"] is None and doc["pdf_url"] == f"/api/documents/{doc['id']}/pdf"
    assert doc["page_count"] == 3 and len(doc["pages"]) == 3
    assert doc["pages"][1]["text_start"] == doc["pages"][1]["text_end"]
    assert [page["number"] for page in doc["pages"]] == [1, 2, 3]
    assert doc["pages"][2]["text_start"] == doc["pages"][1]["text_end"] + 2
    for word in doc["words"]:
        assert doc["ocr_text"][word["text_start"]:word["text_end"]] == word["word"]
        assert word["bbox"] is None and word["confidence"] is None and word["bbox_source"] == "pdf"
        assert word["line"] == doc["ocr_text"][:word["text_start"]].count("\n")
        containing = [p for p in doc["pages"] if p["text_start"] <= word["text_start"] < word["text_end"] <= p["text_end"]]
        assert len(containing) == 1
    assert doc["words"][0]["sentence"] == "Model model without punctuation"
    assert doc["words"][-1]["sentence"] == "Read again!"
    expected = Counter(w["lemma"] for w in doc["words"])
    vocabulary = client.get("/api/vocabulary").json()
    assert {w["lemma"]: w["seen_count"] for w in vocabulary} == expected
    assert all(w["status"] is None and w["mastery_score"] is None and w["lookup_count"] == 0 for w in vocabulary)
    assert expected["model"] == 3 and count(factory, ReviewSchedule) == 0
    assert client.get("/api/events").json() == []
    before = snapshot(factory)
    assert client.get(f"/api/documents/{doc['id']}").json() == doc
    assert client.get("/api/documents?source=pdf&q=another").json()[0]["page_count"] == 3
    response = client.get(doc["pdf_url"])
    assert response.content == data and response.headers["content-type"] == "application/pdf"
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "private, no-store"
    assert client.get(f"/api/documents/{doc['id']}/image").status_code == 404
    assert snapshot(factory) == before
    assert len(list((settings.storage_dir / "pdfs").glob("*.pdf"))) == 1


def test_unicode_spans_are_code_points_and_page_sentences_never_merge(pdf_app, monkeypatch):
    client, _, _ = pdf_app
    contents = iter(["🙂中文 Models", "another model"])
    monkeypatch.setattr("pypdf._page.PageObject.extract_text", lambda *args, **kwargs: next(contents))
    doc = imported(client, data=pdf_bytes(("First", "Last")))
    assert doc["ocr_text"] == "🙂中文 Models\n\nanother model"
    assert doc["words"][0]["text_start"] == 4
    assert doc["words"][0]["sentence"] == "🙂中文 Models"
    assert doc["words"][1]["sentence"] == "another model"
    assert doc["pages"][1]["text_start"] == len("🙂中文 Models\n\n")


@pytest.mark.parametrize("data,filename,status", [
    (b"not a pdf", "Reading.pdf", 422), (b"%PDF-1.7\ntruncated", "Reading.pdf", 422),
    (pdf_bytes(("",)), "Blank.pdf", 422), (pdf_bytes(("123 456!",)), "Numbers.pdf", 422),
    (pdf_bytes(encrypted=True), "Locked.pdf", 422), (pdf_bytes(), "Reading.png", 415),
    (pdf_bytes(("a",) * 101), "TooManyPages.pdf", 413),
    (b"%PDF-" + b"0" * (10 * 1024 * 1024), "Huge.pdf", 413),
], ids=["not-pdf", "broken", "blank", "numbers", "encrypted", "extension", "too-many-pages", "too-large"])
def test_invalid_pdfs_have_no_database_or_disk_side_effects(pdf_app, data, filename, status):
    client, factory, settings = pdf_app
    before = snapshot(factory)
    response = upload(client, data=data, filename=filename)
    assert response.status_code == status, response.text
    assert count(factory, Document) == count(factory, TextImportReceipt) == 0
    assert snapshot(factory) == before and not list(settings.storage_dir.rglob("*.pdf"))


@pytest.mark.parametrize("form", [{}, {"request_id": ""}, {"request_id": "invalid"}])
def test_uuid_is_required_and_validated_before_import(pdf_app, form):
    client, factory, _ = pdf_app
    result = client.post("/api/documents/pdf", files={"file": ("Reading.pdf", pdf_bytes(), "application/pdf")}, data=form)
    assert result.status_code == 422
    assert count(factory, Document) == 0


def test_character_limit_includes_page_separators_and_allows_exact_boundary(pdf_app, monkeypatch):
    client, factory, _ = pdf_app
    monkeypatch.setattr("pypdf._page.PageObject.extract_text", lambda *args, **kwargs: "model" + " " * 199_995)
    first = imported(client, data=pdf_bytes(("One",)))
    assert len(first["ocr_text"]) == 200_000
    before = snapshot(factory)
    result = upload(client, data=pdf_bytes(("One", "Two")))
    assert result.status_code == 413
    assert count(factory, Document) == 1 and snapshot(factory) == before


def test_retry_uses_receipt_before_parsing_and_preserves_status(pdf_app, monkeypatch):
    client, factory, _ = pdf_app
    key = str(uuid4())
    first = imported(client, request_id=key)
    client.put("/api/vocabulary/model", json={"status": "UNKNOWN", "document_id": first["id"]})
    before = snapshot(factory)
    def unexpected(*args, **kwargs):
        pytest.fail("A confirmed import retry must not parse PDF again")
    monkeypatch.setattr(pdf_import, "extract_pdf", unexpected)
    result = imported(client, request_id=key)
    assert result["id"] == first["id"]
    assert all(w["status"] == "UNKNOWN" for w in result["words"] if w["lemma"] == "model")
    assert snapshot(factory) == before and count(factory, Document) == 1
    assert upload(client, request_id=key, filename="Other.pdf").status_code == 409
    assert upload(client, request_id=key, data=pdf_bytes(("Different",))).status_code == 409
    assert client.post("/api/documents/text", json=payload(request_id=key)).status_code == 409
    assert snapshot(factory) == before


def test_soft_delete_blocks_pdf_and_retry_restore_keeps_file_words_pages_and_bookmark(pdf_app):
    client, factory, settings = pdf_app
    key = str(uuid4())
    doc = imported(client, request_id=key)
    bookmarked = doc["words"][-1]["id"]
    assert client.put(f"/api/documents/{doc['id']}", json={"bookmark_occurrence_id": bookmarked}).status_code == 200
    before = snapshot(factory)
    files = list(settings.storage_dir.rglob("*.pdf"))
    assert client.delete(f"/api/documents/{doc['id']}").status_code == 200
    assert client.get(doc["pdf_url"]).status_code == 404
    assert upload(client, request_id=key).status_code == 409
    assert client.get("/api/documents?source=pdf").json() == []
    assert client.get("/api/documents?source=pdf&deleted=true").json()[0]["id"] == doc["id"]
    assert client.post(f"/api/documents/{doc['id']}/restore").status_code == 200
    restored = imported(client, request_id=key)
    assert restored["words"] == doc["words"] and restored["pages"] == doc["pages"]
    assert restored["bookmark_occurrence_id"] == bookmarked
    assert client.get(doc["pdf_url"]).status_code == 200
    assert snapshot(factory) == before and list(settings.storage_dir.rglob("*.pdf")) == files


def test_public_ownership_receipt_namespaces_and_no_ocr_or_ai_quota(pdf_app):
    client, factory, settings = pdf_app
    settings.app_mode = "public"
    users = [client.post("/api/session").json() for _ in range(2)]
    auth = [{"Authorization": f"Bearer {u['token']}"} for u in users]
    key = str(uuid4())
    docs = [imported(client, request_id=key, headers=headers) for headers in auth]
    assert docs[0]["id"] != docs[1]["id"]
    assert client.get(docs[0]["pdf_url"], headers=auth[1]).status_code == 404
    assert client.get(docs[0]["pdf_url"]).status_code == 401
    assert upload(client).status_code == 401
    assert client.get(docs[0]["pdf_url"], headers=auth[0]).status_code == 200
    with factory() as session:
        paths = [Path(d.image_path) for d in session.query(Document).all()]
        assert paths[0] != paths[1] and all(p.is_file() for p in paths)
        events = session.execute(text("SELECT event_type FROM learning_events")).all()
        assert not events
    settings.public_max_documents_per_user = 1
    assert upload(client, headers=auth[0]).status_code == 409
    # Capacity does not prevent a completed receipt replay.
    assert imported(client, request_id=key, headers=auth[0])["id"] == docs[0]["id"]


def test_filename_cannot_select_storage_path_or_overwrite_another_file(pdf_app):
    client, factory, settings = pdf_app
    docs = [imported(client, filename=name) for name in ("../../secret.pdf", "C:\\outside\\secret.pdf")]
    assert all(d["title"] == "secret" for d in docs)
    with factory() as session:
        paths = [Path(d.image_path) for d in session.query(Document).all()]
    assert all(p.parent == settings.storage_dir / "pdfs" and len(p.stem) == 32 for p in paths)
    assert paths[0] != paths[1]


@pytest.mark.parametrize("failure", ["observe", "commit", "write"])
def test_failed_import_rolls_back_rows_receipt_counts_and_new_file(pdf_app, monkeypatch, failure):
    _, factory, settings = pdf_app
    before = snapshot(factory)
    def fail(*args, **kwargs):
        raise RuntimeError("simulated failure")
    with factory() as session:
        if failure == "observe":
            monkeypatch.setattr(pdf_import.VocabularyService, "observe", fail)
        elif failure == "commit":
            monkeypatch.setattr(session, "commit", fail)
        else:
            monkeypatch.setattr(Path, "open", fail)
        with pytest.raises(RuntimeError, match="simulated failure"):
            import_pdf(session, UploadFile(file=BytesIO(pdf_bytes()), filename="Read.pdf"), uuid4())
    assert snapshot(factory) == before
    assert count(factory, Document) == count(factory, TextImportReceipt) == count(factory, WordOccurrence) == 0
    assert not list(settings.storage_dir.rglob("*.pdf"))


def test_concurrent_same_uuid_import_observes_and_saves_exactly_once(pdf_app):
    _, factory, settings = pdf_app
    key, data, gate = uuid4(), pdf_bytes(), Barrier(2)
    def run(_):
        gate.wait(timeout=10)
        with factory() as session:
            return import_pdf(session, UploadFile(file=BytesIO(data), filename="Read.pdf"), key).id
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(run, range(2)))
    assert ids[0] == ids[1] and count(factory, Document) == count(factory, TextImportReceipt) == 1
    with factory() as session:
        assert session.execute(text("SELECT seen_count FROM vocabulary WHERE lemma='model'")).scalar() == 3
    assert len(list(settings.storage_dir.rglob("*.pdf"))) == 1


def test_010_online_backup_adds_pages_without_changing_any_old_column(legacy, monkeypatch):
    path, engine, connection = legacy
    versions = migrations.MIGRATIONS[:10]
    monkeypatch.setattr(migrations, "MIGRATIONS", versions[:9])
    migrations.migrate_database(engine)
    tables = [r["name"] for r in rows(path, "SELECT name FROM sqlite_master WHERE type='table'")
              if r["name"] not in {"schema_migrations", "sqlite_sequence"}]
    before = {t: rows(path, f"SELECT * FROM {t} ORDER BY 1") for t in tables}
    monkeypatch.setattr(migrations, "MIGRATIONS", versions)
    backup = migrations.migrate_database(engine)
    assert backup and "before-010_pdf_reading" in backup.name
    for table, original in before.items():
        assert rows(backup, f"SELECT * FROM {table} ORDER BY 1") == original
        expected = [{**r, "pages": "[]"} for r in original] if table == "documents" else original
        assert rows(path, f"SELECT * FROM {table} ORDER BY 1") == expected
    assert rows(path, "PRAGMA integrity_check") == [{"integrity_check": "ok"}]
    assert rows(path, "PRAGMA foreign_key_check") == []
    assert migrations.migrate_database(engine) is None
    connection.execute("UPDATE documents SET pages='[{\"number\":1,\"text_start\":0,\"text_end\":8}]'")
    connection.execute("DELETE FROM schema_migrations WHERE version=?", (versions[-1][0],))
    connection.commit()
    previous = rows(path, "SELECT * FROM documents")
    assert migrations.migrate_database(engine) is not None
    assert rows(path, "SELECT * FROM documents") == previous


def test_010_failed_upgrade_is_atomic_and_retryable(legacy, monkeypatch):
    path, engine, _ = legacy
    versions = migrations.MIGRATIONS[:10]
    monkeypatch.setattr(migrations, "MIGRATIONS", versions[:9])
    migrations.migrate_database(engine)
    before = rows(path, "SELECT * FROM documents")
    def failed(connection, tables):
        migrations._create_pdf_reading(connection, tables)
        raise RuntimeError("interrupted PDF upgrade")
    monkeypatch.setattr(migrations, "MIGRATIONS", [*versions[:9], (versions[-1][0], failed)])
    with pytest.raises(RuntimeError, match="interrupted PDF upgrade"):
        migrations.migrate_database(engine)
    assert rows(path, "SELECT * FROM documents") == before
    assert len(rows(path, "SELECT * FROM schema_migrations")) == 9
    monkeypatch.setattr(migrations, "MIGRATIONS", versions)
    assert migrations.migrate_database(engine) is not None
