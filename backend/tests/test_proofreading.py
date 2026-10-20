"""Real SQLite corrections, retry/conflict safety and bounded native PDF previews."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from threading import Barrier, BoundedSemaphore
from uuid import uuid4

from fastapi import HTTPException
from PIL import Image
import pytest
from sqlalchemy import text

from backend import public_limits
from backend.models import Document, TextImportReceipt, User, Vocabulary
from backend.schemas import ProofreadRequest
from backend.services import proofreading
from backend.services.proofreading import read_proofread, save_proofread
from backend.services.documents import document_out
from backend.tests.test_article_library import snapshot
from backend.tests.test_backups import (all_rows, archive_data, backup_app, preview, restore)  # noqa: F401
from backend.tests.test_pdf_import import imported, pdf_app, pdf_bytes  # noqa: F401
from backend.tests.test_text_import import post, text_app  # noqa: F401
from backend.tests.test_public_auth import guest, public_app  # noqa: F401
from backend.tests.test_public_limits import counts


def url(doc):
    return f"/api/documents/{doc['id']}/proofread"


def draft(client, doc, contents=None):
    current = client.get(url(doc))
    assert current.status_code == 200, current.text
    body = {"request_id": str(uuid4()), "base_revision": current.json()["revision"],
            "pages": current.json()["pages"]}
    if contents is not None:
        body["pages"] = [{"number": index, "text": value} for index, value in enumerate(contents, 1)]
    return body


def learning(factory):
    return {key: value for key, value in snapshot(factory).items()
            if key not in {"word_occurrences", "text_import_receipts"}}


def test_correct_unicode_blank_pages_exact_spans_without_observations(pdf_app, monkeypatch):
    client, factory, settings = pdf_app
    original = pdf_bytes()
    doc = imported(client, data=original)
    assert client.put("/api/vocabulary/model", json={"status": "UNKNOWN", "document_id": doc["id"]}).status_code == 200
    assert client.put(f"/api/documents/{doc['id']}", json={"tags": ["Reading"], "title": "My PDF"}).status_code == 200
    before = learning(factory)
    def unexpected(*args, **kwargs):
        pytest.fail("Proofreading/preview must never invoke OCR or AI")
    monkeypatch.setattr("backend.services.pdf_ocr.get_ocr_service", unexpected)
    monkeypatch.setattr("backend.services.llm.LLMService._complete", unexpected)
    body = draft(client, doc, ["🙂中文 Models can’t fail.\r\nA corrected discovery!", "", "Another model."])
    result = client.put(url(doc), json=body)
    assert result.status_code == 200, result.text
    changed = result.json()
    assert changed["title"] == "My PDF" and changed["tags"] == ["Reading"]
    assert changed["page_count"] == 3 and changed["pages"][1]["text_start"] == changed["pages"][1]["text_end"]
    assert changed["ocr_text"] == "🙂中文 Models can’t fail.\nA corrected discovery!\n\n\n\nAnother model."
    assert min(w["id"] for w in changed["words"]) > max(w["id"] for w in doc["words"])
    for word in changed["words"]:
        assert changed["ocr_text"][word["text_start"]:word["text_end"]] == word["word"]
        assert word["line"] == changed["ocr_text"][:word["text_start"]].count("\n")
        assert word["word"] in word["sentence"]
        assert word["bbox"] is None and word["confidence"] is None and word["bbox_source"] == "pdf"
        assert sum(p["text_start"] <= word["text_start"] < word["text_end"] <= p["text_end"] for p in changed["pages"]) == 1
    assert next(w for w in changed["words"] if w["lemma"] == "model")["status"] == "UNKNOWN"
    assert next(w for w in changed["words"] if w["lemma"] == "discovery")["status"] is None
    assert learning(factory) == before
    assert client.get(doc["pdf_url"]).content == original
    assert len(list((settings.storage_dir / "pdfs").glob("*.pdf"))) == 1
    assert client.get(url(doc)).json()["revision"] != body["base_revision"]
    assert result.headers["cache-control"] == "private, no-store"
    assert client.get(url(doc)).headers["cache-control"] == "private, no-store"


def test_noop_durable_retry_changed_payload_late_retry_and_aba(pdf_app):
    client, factory, _ = pdf_app
    doc = imported(client)
    body = draft(client, doc)
    original_revision = body["base_revision"]
    result = client.put(url(doc), json=body)
    assert result.status_code == 200 and result.json() == doc
    after = all_rows(factory)
    assert client.put(url(doc), json=body).json() == doc
    assert all_rows(factory) == after
    first = draft(client, doc, ["Changed words.", "", "Another reading."])
    changed = client.put(url(doc), json=first)
    assert changed.status_code == 200, changed.text
    second = draft(client, doc, [page["text"] for page in body["pages"]])
    restored = client.put(url(doc), json=second).json()
    assert restored["ocr_text"] == doc["ocr_text"]
    assert client.get(url(doc)).json()["revision"] != original_revision
    after = all_rows(factory)
    assert client.put(url(doc), json=first).json() == restored  # Does not reapply first.
    assert client.put(url(doc), json=body).json() == restored
    assert all_rows(factory) == after
    modified = {**first, "pages": [{"number": 1, "text": "Other."}, *first["pages"][1:]]}
    assert client.put(url(doc), json=modified).status_code == 409
    stale = {**body, "request_id": str(uuid4())}
    assert client.put(url(doc), json=stale).status_code == 409
    assert all_rows(factory) == after


@pytest.mark.parametrize("kind", ["empty", "missing", "extra", "duplicate", "order", "noenglish", "control", "surrogate", "huge", "invalidrevision"])
def test_invalid_corrections_do_not_change_any_rows_or_file(pdf_app, kind):
    client, factory, _ = pdf_app
    doc = imported(client)
    body = draft(client, doc)
    if kind == "empty": body["pages"] = []
    elif kind == "missing": body["pages"] = body["pages"][:-1]
    elif kind == "extra": body["pages"].append({"number": 4, "text": "Extra page"})
    elif kind == "duplicate": body["pages"][1]["number"] = 1
    elif kind == "order": body["pages"].reverse()
    elif kind == "noenglish": body["pages"] = [{"number": n, "text": "中文"} for n in range(1, 4)]
    elif kind == "control": body["pages"][0]["text"] += "\x7f"
    elif kind == "surrogate": body["pages"][0]["text"] += "\ud800"
    elif kind == "huge": body["pages"][0]["text"] = "a" * 200_000
    elif kind == "invalidrevision": body["base_revision"] = "invalid"
    before = all_rows(factory)
    import json
    response = client.put(url(doc), content=json.dumps(body), headers={"content-type": "application/json"})
    assert response.status_code == 422, response.text
    assert all_rows(factory) == before
    assert client.get(f"/api/documents/{doc['id']}").json() == doc


@pytest.mark.parametrize("position,correction,kept", [(0, "Model model corrected punctuation", True),
    (1, "Inserted Model model without punctuation", True), (0, "Changed model without punctuation", False)])
def test_bookmark_moves_only_to_the_same_verbatim_token(pdf_app, position, correction, kept):
    client, _, _ = pdf_app
    doc = imported(client)
    assert client.put(f"/api/documents/{doc['id']}", json={"bookmark_occurrence_id": doc["words"][position]["id"]}).status_code == 200
    body = draft(client, doc)
    body["pages"][0]["text"] = correction
    result = client.put(url(doc), json=body)
    assert result.status_code == 200, result.text
    bookmark = result.json()["bookmark_occurrence_id"]
    if kept:
        mapped = next(w for w in result.json()["words"] if w["id"] == bookmark)
        assert mapped["word"] == doc["words"][position]["word"]
    else:
        assert bookmark is None


def test_changed_page_boundaries_even_when_joined_text_is_identical(pdf_app):
    client, _, _ = pdf_app
    doc = imported(client)
    initial = draft(client, doc, ["First words\n\nSecond words", "", "Last words"])
    original = client.put(url(doc), json=initial).json()
    body = draft(client, doc, ["First words", "Second words\n\n", "Last words"])
    assert "\n\n".join(page["text"] for page in body["pages"]) == original["ocr_text"]
    changed = client.put(url(doc), json=body)
    assert changed.status_code == 200, changed.text
    updated = changed.json()
    assert updated["ocr_text"] == original["ocr_text"] and updated["pages"] != original["pages"]
    assert client.get(url(doc)).json()["revision"] != body["base_revision"]
    assert min(word["id"] for word in updated["words"]) > max(word["id"] for word in original["words"])
    second = next(word for word in updated["words"] if word["word"] == "Second")
    assert updated["pages"][1]["text_start"] <= second["text_start"] < second["text_end"] <= updated["pages"][1]["text_end"]


@pytest.mark.parametrize("retry", [False, True])
def test_save_response_is_consistent_if_another_edit_commits_before_serialization(pdf_app, monkeypatch, retry):
    client, factory, _ = pdf_app
    doc = imported(client)
    body = ProofreadRequest.model_validate(draft(client, doc, ["🙂 Correct words.", "", "A page."]))
    if retry:
        expected = client.put(url(doc), json=body.model_dump(mode="json")).json()
    else:
        expected = None
    with factory() as session:
        commit = session.commit

        def commit_then_edit_again():
            commit()
            with factory() as other:
                current = read_proofread(other, doc["id"], 1)
                later = ProofreadRequest.model_validate({"request_id": str(uuid4()), "base_revision": current.revision,
                    "pages": [{"number": 1, "text": "A considerably longer second correction moves all words."},
                              {"number": 2, "text": "New middle page."}, {"number": 3, "text": "End."}]})
                save_proofread(other, doc["id"], later, 1)

        monkeypatch.setattr(session, "commit", commit_then_edit_again)
        saved = document_out(save_proofread(session, doc["id"], body, 1), session, 1).model_dump(mode="json")
    assert saved["ocr_text"] == "🙂 Correct words.\n\n\n\nA page."
    for word in saved["words"]:
        assert saved["ocr_text"][word["text_start"]:word["text_end"]] == word["word"]
        assert sum(page["text_start"] <= word["text_start"] < word["text_end"] <= page["text_end"]
                   for page in saved["pages"]) == 1
    if expected is not None:
        assert saved == expected
    latest = client.get(f"/api/documents/{doc['id']}").json()
    assert latest["ocr_text"] != saved["ocr_text"]
    assert {word["id"] for word in latest["words"]}.isdisjoint(word["id"] for word in saved["words"])


def test_read_revision_and_page_text_share_a_snapshot_during_concurrent_edit(pdf_app, monkeypatch):
    client, factory, _ = pdf_app
    doc = imported(client)
    original = client.get(url(doc)).json()
    body = ProofreadRequest.model_validate(draft(client, doc, ["Correct words.", "", "A page."]))
    require = proofreading.require_pdf
    with factory() as reader:
        def require_then_edit(session, document_id, user_id):
            found = require(session, document_id, user_id)
            if session is reader:
                with factory() as other:
                    save_proofread(other, doc["id"], body, 1)
            return found

        monkeypatch.setattr(proofreading, "require_pdf", require_then_edit)
        result = read_proofread(reader, doc["id"], 1).model_dump(mode="json")
    assert result == original
    assert client.get(url(doc)).json()["revision"] != original["revision"]


@pytest.mark.parametrize("flushed", [False, True])
def test_read_snapshot_preserves_caller_pending_state_and_uncommitted_work(pdf_app, flushed):
    client, factory, _ = pdf_app
    doc = imported(client)
    original = client.get(url(doc)).json()
    before = all_rows(factory)
    with factory() as session:
        entry = session.query(Vocabulary).filter_by(lemma="model", user_id=1).one()
        entry.lookup_count += 1
        added = User(id=3)
        session.add(added)
        if flushed:
            session.flush()
        assert read_proofread(session, doc["id"], 1).model_dump(mode="json") == original
        assert entry.lookup_count == 1
        if not flushed:
            assert entry in session.dirty and added in session.new
        else:
            assert session.connection().connection.driver_connection.in_transaction
            assert session.get(User, 3) is added
        assert all_rows(factory) == before
        session.rollback()
    assert all_rows(factory) == before


def test_original_page_preview_including_blank_is_readonly_and_bounded(pdf_app, monkeypatch):
    client, factory, _ = pdf_app
    doc = imported(client)
    before = all_rows(factory)
    monkeypatch.setattr("backend.services.pdf_ocr.get_ocr_service", lambda: pytest.fail("No OCR preview"))
    for number in (1, 2, 3):
        result = client.get(f"/api/documents/{doc['id']}/pdf/pages/{number}/image")
        assert result.status_code == 200, result.text
        assert result.headers["content-type"] == "image/png"
        assert result.headers["cache-control"] == "private, no-store"
        with Image.open(BytesIO(result.content)) as image:
            assert max(image.size) <= 2200 and image.width * image.height <= 4_000_000
            if number == 2:
                assert image.getextrema() == ((255, 255),) * 3
    assert all_rows(factory) == before
    assert client.get(f"/api/documents/{doc['id']}/pdf/pages/0/image").status_code == 404
    assert client.get(f"/api/documents/{doc['id']}/pdf/pages/4/image").status_code == 404


def test_preview_source_image_guard_runs_before_renderer(pdf_app, monkeypatch):
    from backend.tests.test_pdf_scan import image_metadata_pdf
    client, factory, settings = pdf_app
    doc = imported(client)
    with factory() as session:
        path = Path(session.get(Document, doc["id"]).image_path)
    path.write_bytes(image_metadata_pdf(header="Readable original words " * 20))
    # Metadata source has one page; use the equivalent original page count.
    with factory() as session:
        document = session.get(Document, doc["id"])
        document.pages = document.pages[:1]
        session.commit()
    monkeypatch.setattr(proofreading, "open_renderer", lambda *args: pytest.fail("guard must run first"))
    result = client.get(f"/api/documents/{doc['id']}/pdf/pages/1/image")
    assert result.status_code == 413, result.text


def test_delete_source_missing_and_wrong_source_are_checked_on_retry(pdf_app):
    client, factory, _ = pdf_app
    doc = imported(client)
    body = draft(client, doc, ["New words.", "", "Another page."])
    assert client.put(url(doc), json=body).status_code == 200
    assert client.delete(f"/api/documents/{doc['id']}").status_code == 200
    before = all_rows(factory)
    assert client.get(url(doc)).status_code == 404
    assert client.put(url(doc), json=body).status_code == 404
    assert client.get(f"/api/documents/{doc['id']}/pdf/pages/1/image").status_code == 404
    assert all_rows(factory) == before
    text_doc = post(client)
    assert client.get(url(text_doc)).status_code == 422
    assert client.put(url(text_doc), json=body).status_code == 422
    assert client.get(f"/api/documents/{text_doc['id']}/pdf/pages/1/image").status_code == 422


def test_commit_failure_rolls_back_content_occurrences_bookmark_and_receipt(pdf_app, monkeypatch):
    client, factory, _ = pdf_app
    doc = imported(client)
    body = ProofreadRequest.model_validate(draft(client, doc, ["Correct words.", "", "A page."]))
    before = all_rows(factory)
    with factory() as session:
        monkeypatch.setattr(session, "commit", lambda: (_ for _ in ()).throw(RuntimeError("disk full")))
        with pytest.raises(RuntimeError, match="disk full"):
            save_proofread(session, doc["id"], body, 1)
    assert all_rows(factory) == before


@pytest.mark.parametrize("same_request", [True, False])
def test_concurrent_retries_and_conflicts_are_serialized(pdf_app, same_request):
    client, factory, _ = pdf_app
    doc = imported(client)
    body = draft(client, doc, ["Correct words.", "", "A page."])
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
        assert len(list(session.query(TextImportReceipt))) == 2  # Initial import + one save.


def test_corrected_new_words_and_receipt_survive_backup_restore(backup_app):
    client, factory, _ = backup_app
    doc = imported(client)
    body = draft(client, doc, ["An unprecedented discovery.", "", "Different context."])
    corrected = client.put(url(doc), json=body)
    assert corrected.status_code == 200, corrected.text
    saved = corrected.json()
    before = learning(factory)
    archive = archive_data(client)
    pre = preview(client, archive)
    assert pre.status_code == 200, pre.text
    result = restore(client, pre.json()["token"])
    assert result.status_code == 200, result.text
    assert client.get(f"/api/documents/{doc['id']}").json() == saved
    after = all_rows(factory)
    assert client.put(url(doc), json=body).json() == saved
    assert all_rows(factory) == after
    assert learning(factory) == before


@pytest.mark.parametrize("endpoint,method", [("analyze", "analyze_word"), ("recommend", "recommend_related_words")])
def test_slow_ai_cannot_write_learning_after_correction(pdf_app, monkeypatch, endpoint, method):
    from backend.services.llm import LLMService
    client, factory, _ = pdf_app
    doc = imported(client)
    body = ProofreadRequest.model_validate(draft(client, doc, ["Correct words.", "", "Another reading."]))
    original = getattr(LLMService, method)
    before = learning(factory)
    def after_edit(self, *args, **kwargs):
        with factory() as session:
            save_proofread(session, doc["id"], body, 1)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(LLMService, method, after_edit)
    response = client.post(f"/api/words/{endpoint}", json={"occurrence_id": doc["words"][0]["id"]})
    assert response.status_code == 409, response.text
    assert learning(factory) == before
    assert client.post(f"/api/words/{endpoint}", json={"occurrence_id": doc["words"][0]["id"]}).status_code == 404


def test_preview_capacity_is_released_after_validation_failure(pdf_app, monkeypatch):
    client, _, _ = pdf_app
    slots = BoundedSemaphore(1)
    monkeypatch.setattr(public_limits, "_pdf_preview_slots", slots)
    assert slots.acquire(False)
    assert client.get("/api/documents/999/pdf/pages/1/image").status_code == 429
    slots.release()
    assert client.get("/api/documents/999/pdf/pages/1/image").status_code == 404
    assert slots.acquire(False)
    slots.release()


@pytest.mark.parametrize("invalid_result", [False, True])
def test_sentence_response_after_correction_is_not_returned_or_cached(pdf_app, monkeypatch, invalid_result):
    from backend.services import sentences
    client, factory, settings = pdf_app
    doc = imported(client)
    body = ProofreadRequest.model_validate(draft(client, doc, ["Correct words.", "", "A page."]))
    settings.llm_provider, settings.llm_api_key, settings.llm_model = "openai_compatible", "test-key", "test-model"
    monkeypatch.setattr(sentences, "get_settings", lambda: settings)
    before = learning(factory)
    def after_edit(self, instruction, data, schema):
        with factory() as other:
            save_proofread(other, doc["id"], body, 1)
        if invalid_result:
            raise ValueError("invalid response")
        return schema.model_validate({"sentence": data["current_sentence"], "translation": "译文",
            "structure": "结构", "clauses": [{"text": data["current_sentence"], "role": "主句", "explanation": "说明"}],
            "key_points": ["语法"]})
    monkeypatch.setattr(sentences.LLMService, "_complete", after_edit)
    with factory() as session, pytest.raises(HTTPException) as stale:
        sentences.analyze_sentence(session, doc["words"][0]["id"], 1)
    assert stale.value.status_code == 409
    assert not list((settings.storage_dir / "sentence-cache").glob("*.json"))
    assert learning(factory) == before


def test_public_proofreading_is_scoped_and_preview_does_not_spend_ocr_budget(public_app, monkeypatch):
    from backend.services import pdf_import
    client, factory, settings, _ = public_app
    monkeypatch.setattr(pdf_import, "get_settings", lambda: settings)
    _, one = guest(client)
    _, two = guest(client)
    imported_response = client.post("/api/documents/pdf", headers=one,
        files={"file": ("Reading.pdf", pdf_bytes(), "application/pdf")}, data={"request_id": str(uuid4())})
    assert imported_response.status_code == 201, imported_response.text
    doc = imported_response.json()
    current = client.get(url(doc), headers=one)
    body = {"request_id": str(uuid4()), "base_revision": current.json()["revision"],
            "pages": [{"number": 1, "text": "Corrected discovery."}, {"number": 2, "text": ""},
                      {"number": 3, "text": "Another context."}]}
    assert client.put(url(doc), headers=one, json=body).status_code == 200
    before = learning(factory)
    for headers, status in (({}, 401), (two, 404)):
        assert client.get(url(doc), headers=headers).status_code == status
        assert client.put(url(doc), headers=headers, json=body).status_code == status
        assert client.get(f"/api/documents/{doc['id']}/pdf/pages/1/image", headers=headers).status_code == status
    result = client.get(f"/api/documents/{doc['id']}/pdf/pages/1/image", headers=one)
    assert result.status_code == 200, result.text
    assert not any(key.startswith(("ocr:", "llm:")) for key in counts(factory))
    assert any(key.startswith("pdf-preview:") for key in counts(factory))
    assert learning(factory) == before


def test_preview_public_rate_limit_is_atomic_without_learning_or_ocr_charges(public_app, monkeypatch):
    from backend.services import pdf_import
    client, factory, settings, _ = public_app
    monkeypatch.setattr(pdf_import, "get_settings", lambda: settings)
    identity, headers = guest(client)
    now = int(public_limits._now().timestamp())
    bucket = f"pdf-preview:{now // 60}:user:{identity['user_id']}"
    with factory() as session:
        public_limits.consume(session, [(bucket, 30, now + 60)], units=30)
    before = all_rows(factory)
    response = client.get("/api/documents/999/pdf/pages/1/image", headers=headers)
    assert response.status_code == 429 and int(response.headers["retry-after"]) > 0
    assert all_rows(factory) == before
    assert not any(key.startswith(("ocr:", "llm:")) for key in counts(factory))
