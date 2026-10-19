"""Scanned/mixed PDFs with fake local OCR; no models, network or personal files."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from io import BytesIO
import math
import sys
from threading import Barrier, Event
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject
from PIL import Image
import pytest

from backend.models import Document, TextImportReceipt, WordOccurrence
from backend.ocr.service import OCRUnavailable
from backend.services import pdf_import, pdf_ocr
from backend.services.pdf_import import extract_pdf, import_pdf
from backend.services.text_import import import_text
from backend.schemas import TextImportRequest
from backend.tests.test_article_library import snapshot
from backend.tests.test_backups import archive_data, backup_app, preview, restore  # noqa: F401
from backend.tests.test_pdf_import import pdf_app, pdf_bytes, upload  # noqa: F401
from backend.tests.test_text_import import count, text_app  # noqa: F401


def scan_pdf(kinds=("scan",), header=""):
    """Real pypdf pages; tiny embedded images are never decoded in preflight."""
    source = PdfReader(BytesIO(pdf_bytes(tuple(
        ("A native model learns patterns." if kind == "native" else header if kind == "scan" else "")
        for kind in kinds))))
    writer = PdfWriter()
    for page, kind in zip(source.pages, kinds):
        writer.add_page(page)
        if kind == "scan":
            image = DecodedStreamObject()
            image.set_data(b"\xff\xff\xff")
            image.update({NameObject("/Type"): NameObject("/XObject"),
                          NameObject("/Subtype"): NameObject("/Image"),
                          NameObject("/Width"): NumberObject(1), NameObject("/Height"): NumberObject(1),
                          NameObject("/BitsPerComponent"): NumberObject(8),
                          NameObject("/ColorSpace"): NameObject("/DeviceRGB")})
            page = writer.pages[-1]
            resources = page.get("/Resources", DictionaryObject())
            resources[NameObject("/XObject")] = DictionaryObject({NameObject("/Im0"): writer._add_object(image)})
            page[NameObject("/Resources")] = resources
            existing = page.get_contents()
            stream = DecodedStreamObject()
            stream.set_data((existing.get_data() if existing is not None else b"") + b"\nq 400 0 0 600 50 50 cm /Im0 Do Q")
            page[NameObject("/Contents")] = writer._add_object(stream)
    stream = BytesIO()
    writer.write(stream)
    return stream.getvalue()


def image_metadata_pdf(sizes=((6000, 5000),), *, inline=False, nested=False,
                       header="", shared=False):
    """Tiny payloads with large declared dimensions; image decoding is invalid."""
    writer = PdfWriter()
    writer.add_page(PdfReader(BytesIO(pdf_bytes((header,)))).pages[0])
    page = writer.pages[0]
    original = page.get_contents()
    prefix = original.get_data() if original is not None else b""
    if inline:
        width, height = sizes[0]
        payload = f"q BI /W {width} /H {height} /CS /RGB /BPC 8 /F /AHx ID ffffff> EI Q".encode()
        resources = DictionaryObject()
    else:
        images = DictionaryObject()
        reference = None
        for index, (width, height) in enumerate(sizes):
            if not shared or reference is None:
                image = DecodedStreamObject()
                image.set_data(b"not a real image")
                image.update({NameObject("/Subtype"): NameObject("/Image"),
                              NameObject("/Width"): NumberObject(width),
                              NameObject("/Height"): NumberObject(height),
                              NameObject("/BitsPerComponent"): NumberObject(8),
                              NameObject("/ColorSpace"): NameObject("/DeviceRGB")})
                reference = writer._add_object(image)
            images[NameObject(f"/Im{index}")] = reference
        resources = DictionaryObject({NameObject("/XObject"): images})
        payload = b"q /Im0 Do Q"
    if nested:
        form = DecodedStreamObject()
        form.set_data(payload)
        form.update({NameObject("/Subtype"): NameObject("/Form"),
                     NameObject("/Resources"): resources})
        resources = DictionaryObject({NameObject("/XObject"): DictionaryObject({
            NameObject("/Form0"): writer._add_object(form)})})
        payload = b"q /Form0 Do Q"
    existing_resources = page.get("/Resources", DictionaryObject())
    existing_resources.update(resources)
    page[NameObject("/Resources")] = existing_resources
    stream = DecodedStreamObject()
    stream.set_data(prefix + b"\n" + payload)
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def direct(factory, data, mode="auto", key=None, budget=None):
    with factory() as session:
        document = import_pdf(session, UploadFile(file=BytesIO(data), filename="Scanned.pdf"),
                              key or uuid4(), mode=mode, ocr_budget=budget)
        return document.id


def test_mixed_scan_native_blank_exact_unicode_offsets_and_no_fabricated_learning(pdf_app, monkeypatch):
    client, factory, settings = pdf_app
    calls = []
    def recognize(data, numbers):
        calls.append(numbers)
        return {2: "🙂中文 Models aren’t ready.\r\nRead model!"}
    monkeypatch.setattr(pdf_import, "recognize_pdf_pages", recognize)
    response = upload(client, data=scan_pdf(("native", "scan", "blank", "native")))
    assert response.status_code == 201, response.text
    doc = response.json()
    assert calls == [[2]] and doc["page_count"] == 4 and doc["source"] == "pdf"
    assert [page["number"] for page in doc["pages"]] == [1, 2, 3, 4]
    assert doc["pages"][2]["text_start"] == doc["pages"][2]["text_end"]
    assert "🙂中文 Models aren’t ready.\nRead model!" in doc["ocr_text"]
    for word in doc["words"]:
        assert doc["ocr_text"][word["text_start"]:word["text_end"]] == word["word"]
        assert word["line"] == doc["ocr_text"][:word["text_start"]].count("\n")
        assert word["bbox"] is None and word["confidence"] is None and word["bbox_source"] == "pdf"
        assert any(p["text_start"] <= word["text_start"] < word["text_end"] <= p["text_end"] for p in doc["pages"])
    assert client.get(doc["pdf_url"]).status_code == 200
    assert client.get("/api/events").json() == []
    assert all(w["status"] is None and w["lookup_count"] == 0 for w in client.get("/api/vocabulary").json())
    assert len(list(settings.storage_dir.rglob("*.pdf"))) == 1
    assert not list(settings.storage_dir.rglob("*.png"))


def test_sparse_english_header_over_scan_triggers_ocr_but_full_text_illustration_does_not(monkeypatch):
    calls = []
    monkeypatch.setattr(pdf_import, "recognize_pdf_pages", lambda data, pages: calls.append(pages) or {1: "Actual scanned content."})
    content, _, _ = extract_pdf(scan_pdf(header="Page One"))
    assert content == "Actual scanned content." and calls == [[1]]
    content, _, _ = extract_pdf(scan_pdf(header=" ".join(["model"] * 20)))
    assert content.count("model") == 20 and calls == [[1]]


def test_force_scan_rereads_bad_text_layer_and_text_mode_never_uses_ocr(monkeypatch):
    calls = []
    monkeypatch.setattr(pdf_import, "recognize_pdf_pages", lambda data, pages: calls.append(pages) or {1: "Corrected model text."})
    assert extract_pdf(pdf_bytes(("Wrong text",)), "scan")[0] == "Corrected model text."
    assert calls == [[1]]
    assert extract_pdf(scan_pdf(header="Sparse header"), "text")[0].strip() == "Sparse header"
    assert calls == [[1]]
    with pytest.raises(HTTPException) as empty:
        extract_pdf(scan_pdf(), "text")
    assert empty.value.status_code == 422 and calls == [[1]]


def test_true_blank_pages_stay_numbered_and_skip_even_forced_ocr(monkeypatch):
    calls = []
    monkeypatch.setattr(pdf_import, "recognize_pdf_pages", lambda data, pages: calls.append(pages) or {2: "Recognized text."})
    content, pages, _ = extract_pdf(pdf_bytes(("", "Wrong text", "")), "scan")
    assert calls == [[2]] and content == "\n\nRecognized text.\n\n"
    assert pages[0]["text_start"] == pages[0]["text_end"] == 0
    assert pages[-1]["text_start"] == pages[-1]["text_end"] == len(content)


def test_text_extraction_failure_falls_back_to_ocr_only_in_scan_capable_modes(monkeypatch):
    monkeypatch.setattr("pypdf._page.PageObject.extract_text", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad map")))
    monkeypatch.setattr(pdf_import, "recognize_pdf_pages", lambda data, pages: {1: "A recovered model."})
    assert extract_pdf(pdf_bytes(("Wrong text",)))[0] == "A recovered model."
    with pytest.raises(HTTPException) as failure:
        extract_pdf(pdf_bytes(("Wrong text",)), "text")
    assert failure.value.status_code == 422


def test_twenty_scan_limit_is_preflight_and_hundred_native_pages_still_work(monkeypatch):
    calls = []
    monkeypatch.setattr(pdf_import, "recognize_pdf_pages", lambda data, pages: calls.append(pages) or {n: "A model." for n in pages})
    budget_calls = []
    @contextmanager
    def budget(pages):
        budget_calls.append(pages)
        yield
    with pytest.raises(HTTPException) as too_many:
        extract_pdf(scan_pdf(("scan",) * 21), ocr_budget=budget)
    assert too_many.value.status_code == 413 and not calls and not budget_calls
    assert len(extract_pdf(scan_pdf(("scan",) * 20), ocr_budget=budget)[1]) == 20
    assert budget_calls == [20] and len(calls) == 1
    assert len(extract_pdf(pdf_bytes(("Native text",) * 100), ocr_budget=budget)[1]) == 100
    assert budget_calls == [20]


@pytest.mark.parametrize("failure", [OCRUnavailable("not installed"), HTTPException(422, "broken page"), HTTPException(413, "too long")])
def test_any_required_scan_failure_leaves_no_rows_receipt_or_file(pdf_app, monkeypatch, failure):
    client, factory, settings = pdf_app
    before = snapshot(factory)
    def fail(data, pages):
        assert pages == [2, 3]
        raise failure
    monkeypatch.setattr(pdf_import, "recognize_pdf_pages", fail)
    response = upload(client, data=scan_pdf(("native", "scan", "scan")))
    assert response.status_code == (503 if isinstance(failure, OCRUnavailable) else failure.status_code)
    assert snapshot(factory) == before
    assert not list(settings.storage_dir.rglob("*.pdf"))
    assert count(factory, Document) == count(factory, TextImportReceipt) == count(factory, WordOccurrence) == 0


def test_ocr_character_limit_counts_combined_native_text_and_separators(pdf_app, monkeypatch):
    _, factory, settings = pdf_app
    monkeypatch.setattr(pdf_import, "recognize_pdf_pages", lambda data, pages: {2: "model" + " " * 199_995})
    before = snapshot(factory)
    with pytest.raises(HTTPException) as failure:
        direct(factory, scan_pdf(("native", "scan")))
    assert failure.value.status_code == 413 and snapshot(factory) == before
    assert not list(settings.storage_dir.rglob("*.pdf"))


def test_ocr_runs_outside_database_transaction_and_rechecks_capacity_at_commit(pdf_app, monkeypatch):
    _, factory, settings = pdf_app
    settings.app_mode = "public"
    settings.public_max_documents_per_user = 1
    active_session = None
    def recognize(data, pages):
        assert not active_session.in_transaction()
        with factory() as other:
            import_text(other, TextImportRequest(text="A different model.", request_id=uuid4()))
        return {1: "Scanned model."}
    monkeypatch.setattr(pdf_import, "recognize_pdf_pages", recognize)
    with factory() as session:
        active_session = session
        with pytest.raises(HTTPException) as capacity:
            import_pdf(session, UploadFile(file=BytesIO(scan_pdf()), filename="Read.pdf"), uuid4())
    assert capacity.value.status_code == 409 and count(factory, Document) == 1
    assert not list(settings.storage_dir.rglob("*.pdf"))


def test_receipt_avoids_repeat_ocr_budget_and_different_mode_conflicts(pdf_app, monkeypatch):
    _, factory, _ = pdf_app
    calls, budgets = [], []
    monkeypatch.setattr(pdf_import, "recognize_pdf_pages", lambda data, pages: calls.append(pages) or {1: "A model."})
    @contextmanager
    def budget(pages):
        budgets.append(pages)
        yield
    data, key = scan_pdf(), uuid4()
    first = direct(factory, data, key=key, budget=budget)
    before = snapshot(factory)
    assert direct(factory, data, key=key, budget=budget) == first
    assert snapshot(factory) == before and calls == [[1]] and budgets == [1]
    with pytest.raises(HTTPException) as conflict:
        direct(factory, data, mode="scan", key=key, budget=budget)
    assert conflict.value.status_code == 409 and calls == [[1]] and budgets == [1]


def test_scanned_pdf_backup_round_trip_preserves_page_spans_source_and_original_file(backup_app, monkeypatch):
    client, _, _ = backup_app
    data = scan_pdf(("scan", "blank", "native"))
    monkeypatch.setattr(pdf_import, "recognize_pdf_pages", lambda data, pages: {1: "🙂 A scanned model."})
    response = upload(client, data=data)
    assert response.status_code == 201, response.text
    document = response.json()
    archive = archive_data(client)
    info = preview(client, archive)
    assert info.status_code == 200, info.text
    assert info.json()["document_count"] == 1 and info.json()["file_count"] == 1
    assert client.delete(f"/api/documents/{document['id']}").status_code == 200
    result = restore(client, info.json()["token"])
    assert result.status_code == 200, result.text
    restored = client.get(f"/api/documents/{document['id']}").json()
    assert restored == document
    assert client.get(restored["pdf_url"]).content == data


def test_quota_refusal_happens_before_renderer_and_releases_for_retry(pdf_app, monkeypatch):
    _, factory, settings = pdf_app
    before = snapshot(factory)
    monkeypatch.setattr(pdf_import, "recognize_pdf_pages", lambda *args: pytest.fail("Quota must precede rendering"))
    @contextmanager
    def denied(pages):
        assert pages == 2
        raise HTTPException(429, "quota")
        yield
    with pytest.raises(HTTPException) as failure:
        direct(factory, scan_pdf(("scan", "scan")), budget=denied)
    assert failure.value.status_code == 429 and snapshot(factory) == before
    assert not list(settings.storage_dir.rglob("*.pdf"))


@pytest.mark.parametrize("inline,nested", [(False, False), (False, True), (True, False), (True, True)])
def test_oversized_embedded_images_rejected_from_metadata_before_ocr_and_budget(monkeypatch, inline, nested):
    monkeypatch.setattr(pdf_import, "recognize_pdf_pages", lambda *args: pytest.fail("Oversized images must not reach renderer"))
    def budget(pages):
        pytest.fail("Image validation must precede quota reservation")
    data = image_metadata_pdf(inline=inline, nested=nested)
    assert len(data) < 2000
    with pytest.raises(HTTPException) as failure:
        extract_pdf(data, ocr_budget=budget)
    assert failure.value.status_code == 413 and "压缩扫描图分辨率" in failure.value.detail


def test_source_image_guard_does_not_change_native_only_or_text_mode(monkeypatch):
    monkeypatch.setattr(pdf_import, "recognize_pdf_pages", lambda *args: pytest.fail("Native text must not render images"))
    native = " ".join(["model"] * 20)
    data = image_metadata_pdf(header=native)
    assert extract_pdf(data)[0].count("model") == 20
    assert extract_pdf(data, "text")[0].count("model") == 20
    with pytest.raises(HTTPException) as failure:
        extract_pdf(data, "scan")
    assert failure.value.status_code == 413


def test_total_unique_source_image_pixels_per_page_are_bounded_and_shared_images_count_once(monkeypatch):
    calls = []
    monkeypatch.setattr(pdf_import, "recognize_pdf_pages", lambda data, pages: calls.append(pages) or {1: "Recognized model."})
    sizes = ((4250, 4000),) * 3  # Individually 17M pixels, collectively 51M.
    with pytest.raises(HTTPException) as failure:
        extract_pdf(image_metadata_pdf(sizes))
    assert failure.value.status_code == 413 and not calls
    assert extract_pdf(image_metadata_pdf(sizes, shared=True))[0] == "Recognized model."
    assert calls == [[1]]


def test_oversized_image_masks_are_guarded_even_when_base_image_is_small(monkeypatch):
    writer = PdfWriter()
    writer.add_page(PdfReader(BytesIO(image_metadata_pdf(((1, 1),)))).pages[0])
    base = writer.pages[0]["/Resources"]["/XObject"]["/Im0"]
    mask = DecodedStreamObject()
    mask.set_data(b"not a decoded mask")
    mask.update({NameObject("/Subtype"): NameObject("/Image"),
                 NameObject("/Width"): NumberObject(6000), NameObject("/Height"): NumberObject(5000)})
    base[NameObject("/SMask")] = writer._add_object(mask)
    output = BytesIO()
    writer.write(output)
    monkeypatch.setattr(pdf_import, "recognize_pdf_pages", lambda *args: pytest.fail("Masks must be validated before rendering"))
    with pytest.raises(HTTPException) as failure:
        extract_pdf(output.getvalue())
    assert failure.value.status_code == 413


def test_same_uuid_concurrent_requests_only_ocr_once(pdf_app, monkeypatch):
    _, factory, _ = pdf_app
    calls, entered, release, gate = [], Event(), Event(), Barrier(2)
    def recognize(data, pages):
        calls.append(pages)
        entered.set()
        assert release.wait(timeout=10)
        return {1: "One model."}
    monkeypatch.setattr(pdf_import, "recognize_pdf_pages", recognize)
    data, key = scan_pdf(), uuid4()
    def run():
        gate.wait(timeout=10)
        return direct(factory, data, key=key)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run) for _ in range(2)]
        assert entered.wait(timeout=10)
        release.set()
        results = [future.result(timeout=10) for future in futures]
    assert results[0] == results[1] and calls == [[1]]
    assert count(factory, Document) == count(factory, TextImportReceipt) == 1


def fake_rasterizer(monkeypatch, blank=()):
    paths, events = [], []
    @contextmanager
    def renderer(data):
        events.append("open")
        try:
            yield object()
        finally:
            events.append("close")
    def render(document, number, path):
        paths.append(path)
        events.append(f"render:{number}")
        assert not path.exists()
        if number in blank:
            return False
        path.write_bytes(b"temporary image")
        return True
    monkeypatch.setattr(pdf_ocr, "open_renderer", renderer)
    monkeypatch.setattr(pdf_ocr, "render_page", render)
    return paths, events


def test_renderer_uses_one_temporary_png_sequentially_and_deletes_everything(monkeypatch):
    paths, events = fake_rasterizer(monkeypatch, blank={2})
    def recognize(path):
        assert path.is_file()
        events.append("ocr")
        return SimpleNamespace(text="A model.")
    monkeypatch.setattr(pdf_ocr, "get_ocr_service", lambda: SimpleNamespace(recognize=recognize))
    assert pdf_ocr.recognize_pdf_pages(b"data", [1, 2, 3]) == {1: "A model.", 2: "", 3: "A model."}
    assert events == ["open", "render:1", "ocr", "render:2", "render:3", "ocr", "close"]
    assert len(set(paths)) == 1 and not paths[0].exists() and not paths[0].parent.exists()


def test_cumulative_ocr_character_limit_stops_before_rendering_next_page(monkeypatch):
    paths, events = fake_rasterizer(monkeypatch)
    monkeypatch.setattr(pdf_ocr, "MAX_OCR_CHARACTERS", 10)
    calls = []
    def recognize(path):
        calls.append(path)
        return SimpleNamespace(text="model.")
    monkeypatch.setattr(pdf_ocr, "get_ocr_service", lambda: SimpleNamespace(recognize=recognize))
    with pytest.raises(HTTPException) as failure:
        pdf_ocr.recognize_pdf_pages(b"data", [1, 2, 3])
    assert failure.value.status_code == 413 and len(calls) == 2
    assert events == ["open", "render:1", "render:2", "close"]
    assert not paths[0].exists() and not paths[0].parent.exists()


def test_complete_native_lifecycle_is_locked_and_bitmap_is_copied_before_close(tmp_path, monkeypatch):
    events = []
    class Bitmap:
        def __init__(self):
            self.shared = Image.new("RGB", (50, 60), "white")
            self.shared.putpixel((5, 5), (0, 0, 0))
        def to_pil(self):
            assert pdf_ocr._pdfium_lock.locked()
            return self.shared
        def close(self):
            assert pdf_ocr._pdfium_lock.locked()
            events.append("bitmap:close")
            self.shared.close()
    class Page:
        def get_size(self):
            assert pdf_ocr._pdfium_lock.locked()
            return 20, 24
        def render(self, scale, rev_byteorder, limit_image_cache):
            assert pdf_ocr._pdfium_lock.locked() and scale == 2.5 and rev_byteorder is True and limit_image_cache is True
            return Bitmap()
        def close(self):
            assert pdf_ocr._pdfium_lock.locked()
            events.append("page:close")
    class NativeDocument:
        def __init__(self, data):
            assert pdf_ocr._pdfium_lock.locked() and data == b"pdf"
            events.append("document:open")
        def __getitem__(self, index):
            assert pdf_ocr._pdfium_lock.locked() and index == 0
            return Page()
        def close(self):
            assert pdf_ocr._pdfium_lock.locked()
            events.append("document:close")
    monkeypatch.setitem(sys.modules, "pypdfium2", SimpleNamespace(PdfDocument=NativeDocument))
    path = tmp_path / "rendered.png"
    with pdf_ocr.open_renderer(b"pdf") as document:
        assert pdf_ocr.render_page(document, 1, path)
    with Image.open(path) as image:
        assert image.size == (50, 60) and image.getpixel((5, 5)) == (0, 0, 0)
    assert events == ["document:open", "bitmap:close", "page:close", "document:close"]
    assert not pdf_ocr._pdfium_lock.locked()


@pytest.mark.parametrize("failure,status", [(OCRUnavailable("engine failed"), 503), (RuntimeError("render failed"), 422)])
def test_renderer_failure_cleanup_and_page_specific_message(monkeypatch, failure, status):
    paths, events = fake_rasterizer(monkeypatch)
    def recognize(path):
        raise failure
    monkeypatch.setattr(pdf_ocr, "get_ocr_service", lambda: SimpleNamespace(recognize=recognize))
    with pytest.raises((HTTPException, OCRUnavailable)) as caught:
        pdf_ocr.recognize_pdf_pages(b"data", [4, 5])
    assert "4" in str(caught.value) and events[-1] == "close"
    assert (503 if isinstance(caught.value, OCRUnavailable) else caught.value.status_code) == status
    assert not paths[0].exists() and not paths[0].parent.exists()


@pytest.mark.parametrize("width,height", [(612, 792), (1, 1), (100_000, 100_000), (10_000, 20), (1, 100_000)])
def test_render_scale_bounds_extreme_pdf_page_dimensions(width, height):
    scale = pdf_ocr.render_scale(width, height)
    output = math.ceil(width * scale), math.ceil(height * scale)
    assert max(output) <= pdf_ocr.MAX_RENDER_EDGE
    assert math.prod(output) <= pdf_ocr.MAX_RENDER_PIXELS


@pytest.mark.parametrize("width,height", [(0, 100), (-1, 1), (float("inf"), 2), (2, float("nan"))])
def test_invalid_render_dimensions_are_rejected_before_allocation(width, height):
    with pytest.raises(ValueError):
        pdf_ocr.render_scale(width, height)
