"""Image corrections preserve immutable pixels and every existing learning signal."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
from PIL import Image
import pytest
from sqlalchemy import text

from backend.models import Document, TextImportReceipt
from backend.ocr.service import ENGLISH_WORD, OCRResult, OCRWord
from backend.schemas import ProofreadRequest
from backend.services import article_questions, backups, proofreading
from backend.services.proofreading import read_proofread, save_proofread
from backend.services.review_context import review_target_range
from backend.tests.test_backups import (all_rows, archive_data, backup_app, preview, restore)  # noqa: F401
from backend.tests.test_pdf_import import pdf_app  # noqa: F401
from backend.tests.test_proofreading import draft, learning, url
from backend.tests.test_public_auth import guest, public_app  # noqa: F401
from backend.tests.test_public_limits import counts
from backend.tests.test_text_import import post, text_app  # noqa: F401


def imported_image(client, monkeypatch, *, source="upload", headers=None):
    if source == "demo":
        response = client.post("/api/documents/demo", headers=headers)
    else:
        output = BytesIO()
        Image.new("RGB", (420, 140), "white").save(output, "PNG")
        content = "Model model learns.\nAnother model reads."
        words = [OCRWord(match.group(), [10 + n * 25, 10, 34 + n * 25, 28],
                         content[:match.start()].count("\n"), .87, "ocr")
                 for n, match in enumerate(ENGLISH_WORD.finditer(content))]
        service = SimpleNamespace(recognize=lambda path: OCRResult(content, 420, 140, words))
        monkeypatch.setattr("backend.services.documents.get_ocr_service", lambda: service)
        response = client.post("/api/documents/upload", headers=headers,
                               files={"file": ("Reading.png", output.getvalue(), "image/png")})
    assert response.status_code in {200, 201}, response.text
    return response.json()


def corrected(client, doc, content="🙂 中文 Models model model.\r\nWe can’t fail; discovery matters!"):
    response = client.put(url(doc), json=draft(client, doc, [content]))
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("source", ["upload", "demo"])
def test_image_virtual_page_noop_and_exact_corrections_preserve_learning_and_pixels(text_app, monkeypatch, source):
    client, factory, settings = text_app
    doc = imported_image(client, monkeypatch, source=source)
    assert all(word["bbox"] is not None and word["text_start"] is None for word in doc["words"])
    assert client.put("/api/vocabulary/model", json={"status": "UNKNOWN", "document_id": doc["id"]}).status_code == 200
    assert client.put(f"/api/documents/{doc['id']}", json={"tags": ["Reading"], "title": "My image",
        "bookmark_occurrence_id": doc["words"][0]["id"]}).status_code == 200
    doc = client.get(f"/api/documents/{doc['id']}").json()
    original_image = client.get(doc["image_url"]).content
    before = learning(factory)
    rows_before = all_rows(factory)
    current = client.get(url(doc))
    assert current.status_code == 200, current.text
    assert current.json()["source"] == "image"
    assert current.json()["pages"] == [{"number": 1, "text": doc["ocr_text"]}]
    assert current.headers["cache-control"] == "private, no-store"
    assert all_rows(factory) == rows_before
    noop = draft(client, doc)
    assert client.put(url(doc), json=noop).json() == doc
    assert client.get(url(doc)).json()["revision"] == noop["base_revision"]

    def unexpected(*args, **kwargs):
        pytest.fail("An image correction must not invoke OCR, LLM, or observation")
    monkeypatch.setattr("backend.services.documents.get_ocr_service", unexpected)
    monkeypatch.setattr("backend.services.llm.LLMService._complete", unexpected)
    monkeypatch.setattr("backend.services.vocabulary.VocabularyService.observe", unexpected)
    result = corrected(client, doc)
    assert result["ocr_text"] == "🙂 中文 Models model model.\nWe can’t fail; discovery matters!"
    assert result["pages"] == [] and result["page_count"] == 1 and result["pdf_url"] is None
    assert result["source"] == source and result["title"] == "My image" and result["tags"] == ["Reading"]
    assert result["created_at"] == doc["created_at"]
    assert (result["width"], result["height"], result["image_url"]) == (doc["width"], doc["height"], doc["image_url"])
    assert result["bookmark_occurrence_id"] is None  # Legacy OCR has no trustworthy text span.
    assert min(word["id"] for word in result["words"]) > max(word["id"] for word in doc["words"])
    for word in result["words"]:
        assert result["ocr_text"][word["text_start"]:word["text_end"]] == word["word"]
        assert word["line"] == result["ocr_text"][:word["text_start"]].count("\n")
        assert word["bbox_source"] == "corrected_text" and word["bbox"] is None and word["confidence"] is None
    repeated = [word for word in result["words"] if word["word"] == "model"]
    targets = [review_target_range(SimpleNamespace(**result), SimpleNamespace(**word)) for word in repeated]
    assert len(repeated) == 2 and all(targets) and targets[0] != targets[1]
    assert all(word["status"] == "UNKNOWN" for word in result["words"] if word["lemma"] == "model")
    assert next(word for word in result["words"] if word["lemma"] == "discovery")["status"] is None
    assert client.get(doc["image_url"]).content == original_image
    assert learning(factory) == before
    with factory() as session:
        stored = session.get(Document, doc["id"])
        assert all(word.bbox == [0, 0, 0, 0] and word.confidence == 0 for word in stored.words)
        assert Path(stored.image_path).read_bytes() == original_image
    assert len(list((settings.storage_dir / "images").glob("*.png"))) == 1
    assert client.get(f"/api/documents/{doc['id']}/pdf/pages/1/image").status_code == 422


@pytest.mark.parametrize("kind", ["empty", "extra", "wrong-number", "noenglish", "control", "surrogate", "huge", "stale"])
def test_invalid_image_edit_is_atomic(text_app, monkeypatch, kind):
    client, factory, _ = text_app
    doc = imported_image(client, monkeypatch)
    body = draft(client, doc)
    if kind == "empty": body["pages"] = []
    elif kind == "extra": body["pages"].append({"number": 2, "text": "Extra page."})
    elif kind == "wrong-number": body["pages"][0]["number"] = 2
    elif kind == "noenglish": body["pages"][0]["text"] = "中文 \n123"
    elif kind == "control": body["pages"][0]["text"] += "\x7f"
    elif kind == "surrogate": body["pages"][0]["text"] += "\ud800"
    elif kind == "huge": body["pages"][0]["text"] = "a" * 200_001
    elif kind == "stale": body["base_revision"] = "0" * 64
    before = all_rows(factory)
    image = client.get(doc["image_url"]).content
    response = client.put(url(doc), content=json.dumps(body), headers={"content-type": "application/json"})
    assert response.status_code == (409 if kind == "stale" else 422), response.text
    assert all_rows(factory) == before and client.get(doc["image_url"]).content == image


def test_image_idempotency_late_retry_aba_and_bookmark_remap(text_app, monkeypatch):
    client, factory, _ = text_app
    doc = imported_image(client, monkeypatch)
    original_revision = client.get(url(doc)).json()["revision"]
    body = draft(client, doc, ["Model model survives."])
    first = client.put(url(doc), json=body).json()
    before = all_rows(factory)
    assert client.put(url(doc), json=body).json() == first and all_rows(factory) == before
    changed_request = {**body, "pages": [{"number": 1, "text": "Other."}]}
    assert client.put(url(doc), json=changed_request).status_code == 409
    bookmark = first["words"][1]["id"]
    assert client.put(f"/api/documents/{doc['id']}", json={"bookmark_occurrence_id": bookmark}).status_code == 200
    second = corrected(client, doc, "Inserted Model model survives.")
    assert next(word for word in second["words"] if word["id"] == second["bookmark_occurrence_id"])["word"] == "model"
    before = all_rows(factory)
    assert client.put(url(doc), json=body).json() == second and all_rows(factory) == before
    assert client.put(url(doc), json={**body, "request_id": str(uuid4())}).status_code == 409
    again = corrected(client, doc, doc["ocr_text"])
    assert again["ocr_text"] == doc["ocr_text"]
    assert client.get(url(doc)).json()["revision"] != original_revision
    assert all(word["bbox"] is None for word in again["words"])  # Text equality cannot resurrect OCR boxes.


@pytest.mark.parametrize("same_request", [True, False])
def test_image_concurrent_retry_and_conflict_have_one_write(text_app, monkeypatch, same_request):
    client, factory, _ = text_app
    doc = imported_image(client, monkeypatch)
    body = draft(client, doc, ["Model learns differently."])
    gate = Barrier(2)
    def worker(index):
        value = {**body}
        if index and not same_request:
            value["request_id"] = str(uuid4())
        gate.wait(timeout=10)
        with factory() as session:
            try:
                return save_proofread(session, doc["id"], ProofreadRequest.model_validate(value), 1).ocr_text
            except HTTPException as exc:
                assert exc.status_code == 409
                return None
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(worker, range(2)))
    assert sum(result is not None for result in results) == (2 if same_request else 1)
    with factory() as session:
        assert session.query(TextImportReceipt).count() == 1


@pytest.mark.parametrize("source", ["upload", "demo"])
def test_corrected_image_and_receipt_survive_complete_backup(backup_app, monkeypatch, source):
    client, factory, _ = backup_app
    doc = imported_image(client, monkeypatch, source=source)
    image = client.get(doc["image_url"]).content
    body = draft(client, doc, ["A corrected model learns with curiosity."])
    saved = client.put(url(doc), json=body).json()
    before = learning(factory)
    pre = preview(client, archive_data(client))
    assert pre.status_code == 200, pre.text
    assert restore(client, pre.json()["token"]).status_code == 200
    assert client.get(f"/api/documents/{doc['id']}").json() == saved
    assert client.get(doc["image_url"]).content == image
    after = all_rows(factory)
    assert client.put(url(doc), json=body).json() == saved and all_rows(factory) == after
    assert learning(factory) == before


@pytest.mark.parametrize("kind", ["null", "mismatch", "wrong-source"])
def test_backup_rejects_malformed_corrected_image_spans(backup_app, monkeypatch, kind):
    client, factory, settings = backup_app
    doc = imported_image(client, monkeypatch)
    corrected(client, doc)
    # Exercise the same validator used by export and verified ZIP imports.
    with factory() as session:
        if kind == "wrong-source":
            session.execute(text("UPDATE documents SET source='text' WHERE id=:id"), {"id": doc["id"]})
        else:
            session.execute(text("UPDATE word_occurrences SET text_start=:value WHERE document_id=:id"),
                            {"value": None if kind == "null" else 0, "id": doc["id"]})
        session.commit()
    before = all_rows(factory)
    with backups.read_database(settings.storage_dir / "text.sqlite3") as connection, pytest.raises(HTTPException) as bad:
        backups.validate_database(connection)
    assert bad.value.status_code == 422 and all_rows(factory) == before


def test_corrected_image_review_sentence_and_qa_use_fresh_exact_text(text_app, monkeypatch):
    client, factory, settings = text_app
    doc = imported_image(client, monkeypatch)
    client.put("/api/vocabulary/model", json={"status": "UNKNOWN", "document_id": doc["id"]})
    before = learning(factory)
    saved = corrected(client, doc, "🙂 Model model model.\nCuriosity improves reading.")
    for path in ("/api/words/analyze", "/api/words/recommend", "/api/sentences/analyze"):
        assert client.post(path, json={"occurrence_id": doc["words"][0]["id"]}).status_code == 404
    queue = client.get("/api/review").json()
    context = next(item["occurrence"] for item in queue["items"] if item["vocabulary"]["lemma"] == "model")
    assert context["id"] in {word["id"] for word in saved["words"]}
    assert context["sentence"] == "🙂 Model model model."
    assert context["sentence"][context["target_start"]:context["target_end"]] == "model"
    assert context["target_start"] == len("🙂 Model model ")  # Last repeated occurrence, not a guessed first match.
    settings.llm_provider, settings.llm_api_key, settings.llm_model = "openai_compatible", "test-key", "test-model"
    monkeypatch.setattr(article_questions, "get_settings", lambda: settings)
    monkeypatch.setattr("backend.services.sentences.get_settings", lambda: settings)
    calls = []
    def complete(self, instruction, data, schema):
        calls.append(data)
        if "article_chunks" in data:
            chunk = data["article_chunks"][0]
            return schema.model_validate({"answer": "按校对后的文章回答。", "answerable": True,
                "citations": [{"chunk_id": chunk["id"], "quote": chunk["text"]}]})
        return schema.model_validate({"sentence": data["current_sentence"], "translation": "译文", "structure": "结构",
            "clauses": [{"text": data["current_sentence"], "role": "主句", "explanation": "说明"}], "key_points": ["语法"]})
    monkeypatch.setattr("backend.services.llm.LLMService._complete", complete)
    question_url = f"/api/documents/{doc['id']}/questions"
    answer = client.post(question_url, json={"question": "文章说什么？"}).json()
    quote = answer["citations"][0]
    assert saved["ocr_text"][quote["text_start"]:quote["text_end"]] == quote["quote"] and quote["page_number"] is None
    assert client.post(question_url, json={"question": "文章说什么？"}).json()["cached"]
    sentence = client.post("/api/sentences/analyze", json={"occurrence_id": saved["words"][1]["id"]})
    assert sentence.status_code == 200 and sentence.json()["sentence"] == "🙂 Model model model."
    latest = corrected(client, doc, "A model reads a completely new sentence.")
    answer = client.post(question_url, json={"question": "文章说什么？"}).json()
    assert not answer["cached"] and answer["citations"][0]["quote"] == latest["ocr_text"]
    assert learning(factory) == before


def test_image_public_ownership_private_files_and_deleted_retry(public_app, monkeypatch):
    client, factory, _settings, _ = public_app
    _, one = guest(client)
    _, two = guest(client)
    doc = imported_image(client, monkeypatch, headers=one)
    response = client.get(url(doc), headers=one)
    assert response.status_code == 200 and response.headers["cache-control"] == "private, no-store"
    body = {"request_id": str(uuid4()), "base_revision": response.json()["revision"],
            "pages": [{"number": 1, "text": "A corrected model learns."}]}
    assert client.put(url(doc), headers=one, json=body).status_code == 200
    assert client.get(doc["image_url"], headers=one).headers["cache-control"] == "private, no-store"
    before = learning(factory)
    quota = counts(factory)
    for headers, status in (({}, 401), (two, 404)):
        assert client.get(url(doc), headers=headers).status_code == status
        assert client.put(url(doc), headers=headers, json=body).status_code == status
        assert client.get(doc["image_url"], headers=headers).status_code == status
    assert client.put(url(doc), headers=one, json=body).status_code == 200
    assert learning(factory) == before and counts(factory) == quota  # No extra OCR/LLM charges for corrections.
    assert client.delete(f"/api/documents/{doc['id']}", headers=one).status_code == 200
    rows_before = all_rows(factory)
    assert client.get(url(doc), headers=one).status_code == 404
    assert client.put(url(doc), headers=one, json=body).status_code == 404
    assert client.get(doc["image_url"], headers=one).status_code == 404
    assert all_rows(factory) == rows_before


def test_plain_text_and_web_sources_stay_outside_workbench(text_app):
    client, factory, _ = text_app
    doc = post(client)
    body = {"request_id": str(uuid4()), "base_revision": "0" * 64,
            "pages": [{"number": 1, "text": "Correct words."}]}
    for source in ("text", "web"):
        with factory() as session:
            session.get(Document, doc["id"]).source = source
            session.commit()
        before = all_rows(factory)
        assert client.get(url(doc)).status_code == 422
        assert client.put(url(doc), json=body).status_code == 422
        assert all_rows(factory) == before
