"""Scanned PDFs share public OCR capacity and charge pages, never confirmed retries."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from hashlib import sha256
from threading import Barrier, BoundedSemaphore
from uuid import uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import func, select

from backend import public_limits as limits
from backend.models import Document
from backend.ocr.service import OCRUnavailable
from backend.services import pdf_import
from backend.tests.test_article_library import snapshot
from backend.tests.test_pdf_import import pdf_bytes
from backend.tests.test_pdf_scan import scan_pdf
from backend.tests.test_public_auth import guest, public_app  # noqa: F401
from backend.tests.test_public_limits import budget_store, clock, counts  # noqa: F401


@pytest.fixture
def scan_public_app(public_app, monkeypatch):
    client, factory, settings, legacy_id = public_app
    monkeypatch.setattr(pdf_import, "get_settings", lambda: settings)
    return client, factory, settings, legacy_id


def ocr_counts(factory):
    return {key: value for key, value in counts(factory).items() if key.startswith("ocr:")}


def stored_files(settings):
    return {str(path.relative_to(settings.storage_dir)): sha256(path.read_bytes()).hexdigest()
            for folder in ("pdfs", "images")
            for path in (settings.storage_dir / folder).rglob("*") if path.is_file()}


def upload(client, headers, data, key=None, mode="auto"):
    return client.post("/api/documents/pdf", headers=headers,
                       files={"file": ("Scanned.pdf", data, "application/pdf")},
                       data={"request_id": key or str(uuid4()), "ocr_mode": mode})


def test_weighted_page_reservations_are_atomic_for_global_and_individual_caps(budget_store, clock):
    factory, settings = budget_store
    settings.public_ocr_daily_limit = 8
    settings.public_ocr_user_daily_limit = 3
    day = int(clock[0].timestamp()) // 86400
    with factory() as session:
        limits._reserve(session, 11, "ocr", units=2)
        before = counts(factory)
        with pytest.raises(HTTPException) as user_full:
            limits._reserve(session, 11, "ocr", units=2)
        assert user_full.value.status_code == 429 and counts(factory) == before
        limits._reserve(session, 12, "ocr", units=3)
        limits._reserve(session, 13, "ocr", units=3)
        before = counts(factory)
        with pytest.raises(HTTPException) as global_full:
            limits._reserve(session, 14, "ocr", units=1)
        assert global_full.value.status_code == 429 and counts(factory) == before
    assert counts(factory) == {
        f"ocr:{day}:global": 8,
        f"ocr:{day}:user:11": 2,
        f"ocr:{day}:user:12": 3,
        f"ocr:{day}:user:13": 3,
    }


@pytest.mark.parametrize("global_limit,user_limit", [(2, 9), (9, 2)])
def test_first_multi_page_request_over_either_cap_leaves_no_partial_charge(budget_store, global_limit, user_limit):
    factory, settings = budget_store
    settings.public_ocr_daily_limit = global_limit
    settings.public_ocr_user_daily_limit = user_limit
    with factory() as session, pytest.raises(HTTPException) as rejected:
        limits._reserve(session, 7, "ocr", units=3)
    assert rejected.value.status_code == 429
    assert int(rejected.value.headers["Retry-After"]) > 0
    assert counts(factory) == {}


def test_concurrent_different_page_counts_never_overcharge_or_exceed_caps(budget_store, clock):
    factory, _ = budget_store
    expiry = int(clock[0].timestamp()) + 3600
    start = Barrier(16)

    def reserve(index):
        user, pages = index % 4, 2 + index % 2
        start.wait(timeout=10)
        with factory() as session:
            try:
                limits.consume(session, [("global", 11, expiry), (f"user:{user}", 5, expiry)], units=pages)
                return user, pages
            except HTTPException as error:
                assert error.status_code == 429
                return None

    with ThreadPoolExecutor(max_workers=16) as workers:
        results = list(workers.map(reserve, range(16)))
    successful = [result for result in results if result is not None]
    assert successful and len(successful) < len(results)
    stored = counts(factory)
    assert stored.pop("global") == sum(pages for _, pages in successful) <= 11
    assert stored == {f"user:{user}": sum(pages for owner, pages in successful if owner == user)
                      for user in {owner for owner, _ in successful}}
    assert all(value <= 5 for value in stored.values())


def test_pdf_pages_and_image_ocr_share_slot_and_release_after_failure_or_quota_rejection(budget_store, monkeypatch):
    factory, settings = budget_store
    settings.public_ocr_user_daily_limit = 4
    settings.public_ocr_daily_limit = 8
    slots = BoundedSemaphore(1)
    monkeypatch.setattr(limits, "_ocr_slots", slots)
    image_budget = contextmanager(limits.require_ocr_budget)
    with factory() as session:
        with pytest.raises(OCRUnavailable):
            with limits.pdf_ocr_budget(session, 7, 2):
                before = counts(factory)
                with pytest.raises(HTTPException) as occupied:
                    with image_budget(user_id=7, session=session):
                        pytest.fail("An image cannot run during PDF OCR")
                assert occupied.value.headers["Retry-After"] == "10"
                assert counts(factory) == before
                raise OCRUnavailable("simulated OCR failure")
        with image_budget(user_id=7, session=session):
            pass
        before = counts(factory)
        with pytest.raises(HTTPException):
            with limits.pdf_ocr_budget(session, 7, 2):
                pytest.fail("The two-page request exceeds remaining user quota")
        assert counts(factory) == before
        # A quota rejection must release the slot, leaving one image attempt.
        with image_budget(user_id=7, session=session):
            pass
    assert sorted(counts(factory).values()) == [4, 4]
    assert slots.acquire(blocking=False)
    slots.release()


def test_local_pdf_ocr_bypasses_occupied_public_slot_and_page_budget(budget_store, monkeypatch):
    factory, settings = budget_store
    settings.app_mode = "local"
    slots = BoundedSemaphore(1)
    assert slots.acquire(blocking=False)
    monkeypatch.setattr(limits, "_ocr_slots", slots)
    with factory() as session, limits.pdf_ocr_budget(session, 1, 20):
        pass
    assert counts(factory) == {}
    assert not slots.acquire(blocking=False)
    slots.release()


@pytest.mark.parametrize("mode", ["auto", "text"])
def test_native_pages_skip_even_exhausted_public_ocr_budget_and_busy_slot(scan_public_app, clock, monkeypatch, mode):
    client, factory, settings, _ = scan_public_app
    settings.public_ocr_user_daily_limit = 1
    owner, headers = guest(client)
    with factory() as session:
        limits._reserve(session, owner["user_id"], "ocr")
    before = ocr_counts(factory)
    slots = BoundedSemaphore(1)
    assert slots.acquire(blocking=False)
    monkeypatch.setattr(limits, "_ocr_slots", slots)
    monkeypatch.setattr(pdf_import, "recognize_pdf_pages", lambda *args: pytest.fail("Native PDF must not request OCR"))
    try:
        response = upload(client, headers, pdf_bytes(("Native model text.", "", "Read another model.")), mode=mode)
        assert response.status_code == 201, response.text
        assert response.json()["page_count"] == 3
        assert ocr_counts(factory) == before
    finally:
        slots.release()


@pytest.mark.parametrize("mode", ["invalid", "AUTO", "scan "])
def test_invalid_form_mode_rejects_before_ocr_quota_or_any_learning_write(scan_public_app, clock, monkeypatch, mode):
    client, factory, settings, _ = scan_public_app
    _, headers = guest(client)
    before, quota, files = snapshot(factory), ocr_counts(factory), stored_files(settings)
    monkeypatch.setattr(pdf_import, "recognize_pdf_pages", lambda *args: pytest.fail("Invalid mode must not use OCR"))
    response = upload(client, headers, scan_pdf(), mode=mode)
    assert response.status_code == 422, response.text
    assert snapshot(factory) == before and ocr_counts(factory) == quota
    assert stored_files(settings) == files
    assert client.get("/api/documents", headers=headers).json() == []


def test_planned_pages_over_user_quota_reject_before_ocr_and_leave_no_doc_or_file(scan_public_app, clock, monkeypatch):
    client, factory, settings, _ = scan_public_app
    settings.public_ocr_user_daily_limit = 1
    _, headers = guest(client)
    before, files = snapshot(factory), stored_files(settings)
    monkeypatch.setattr(pdf_import, "recognize_pdf_pages", lambda *args: pytest.fail("Quota rejection must precede OCR"))
    response = upload(client, headers, scan_pdf(("native", "scan", "scan")))
    assert response.status_code == 429, response.text
    assert snapshot(factory) == before and ocr_counts(factory) == {}
    assert stored_files(settings) == files
    assert client.get("/api/documents", headers=headers).json() == []


def test_ocr_failure_spends_planned_pages_but_releases_slot_and_keeps_learning_atomic(scan_public_app, clock, monkeypatch):
    client, factory, settings, _ = scan_public_app
    settings.public_ocr_user_daily_limit = 3
    owner, headers = guest(client)
    calls = []
    slots = BoundedSemaphore(1)
    monkeypatch.setattr(limits, "_ocr_slots", slots)

    def recognize(data, pages):
        calls.append(pages)
        if len(calls) == 1:
            raise OCRUnavailable("simulated first-page failure")
        return {number: "Recognized model text." for number in pages}

    monkeypatch.setattr(pdf_import, "recognize_pdf_pages", recognize)
    before, files = snapshot(factory), stored_files(settings)
    failed_key = str(uuid4())
    failed = upload(client, headers, scan_pdf(("scan", "scan")), key=failed_key)
    assert failed.status_code == 503, failed.text
    assert calls == [[1, 2]]
    day = int(clock[0].timestamp()) // 86400
    assert ocr_counts(factory) == {f"ocr:{day}:global": 2, f"ocr:{day}:user:{owner['user_id']}": 2}
    assert snapshot(factory) == before and stored_files(settings) == files
    assert client.get("/api/documents", headers=headers).json() == []
    # Failure left no completed receipt, so the same request attempts again and
    # is refused atomically when its two planned pages cannot fit the quota.
    rejected = upload(client, headers, scan_pdf(("scan", "scan")), key=failed_key)
    assert rejected.status_code == 429 and calls == [[1, 2]]
    assert snapshot(factory) == before and stored_files(settings) == files
    # The remaining single page can still acquire the slot and succeed.
    accepted = upload(client, headers, scan_pdf())
    assert accepted.status_code == 201, accepted.text
    assert calls == [[1, 2], [1]]
    assert sorted(ocr_counts(factory).values()) == [3, 3]


def test_confirmed_retry_is_free_and_same_uuid_is_private_to_each_guest(scan_public_app, clock, monkeypatch):
    client, factory, settings, _ = scan_public_app
    settings.public_ocr_user_daily_limit = 2
    settings.public_ocr_daily_limit = 4
    owner, one = guest(client)
    other, two = guest(client)
    calls = []
    monkeypatch.setattr(pdf_import, "recognize_pdf_pages", lambda data, pages: calls.append(pages)
                        or {number: "A scanned model." for number in pages})
    data, key = scan_pdf(("native", "scan", "scan")), str(uuid4())
    first = upload(client, one, data, key=key)
    assert first.status_code == 201, first.text
    first_doc = first.json()
    assert calls == [[2, 3]]
    before, quota, files = snapshot(factory), ocr_counts(factory), stored_files(settings)
    # No remaining quota and an occupied work slot cannot prevent receipt replay.
    slots = BoundedSemaphore(1)
    assert slots.acquire(blocking=False)
    monkeypatch.setattr(limits, "_ocr_slots", slots)
    try:
        again = upload(client, one, data, key=key)
        assert again.status_code == 201 and again.json() == first_doc
        assert calls == [[2, 3]] and ocr_counts(factory) == quota
        assert snapshot(factory) == before and stored_files(settings) == files
        conflict = upload(client, one, data, key=key, mode="scan")
        assert conflict.status_code == 409
        assert calls == [[2, 3]] and ocr_counts(factory) == quota
    finally:
        slots.release()
    second = upload(client, two, data, key=key)
    assert second.status_code == 201, second.text
    second_doc = second.json()
    assert second_doc["id"] != first_doc["id"] and calls == [[2, 3], [2, 3]]
    assert client.get(first_doc["pdf_url"], headers=two).status_code == 404
    assert client.get(f"/api/documents/{first_doc['id']}", headers=two).status_code == 404
    assert client.get(second_doc["pdf_url"], headers=one).status_code == 404
    assert client.get("/api/documents", headers=one).json()[0]["id"] == first_doc["id"]
    assert client.get("/api/documents", headers=two).json()[0]["id"] == second_doc["id"]
    day = int(clock[0].timestamp()) // 86400
    assert ocr_counts(factory) == {f"ocr:{day}:global": 4, f"ocr:{day}:user:{owner['user_id']}": 2,
                                   f"ocr:{day}:user:{other['user_id']}": 2}
    with factory() as session:
        assert session.scalar(select(func.count(Document.id)).where(Document.user_id == owner["user_id"])) == 1
        assert session.scalar(select(func.count(Document.id)).where(Document.user_id == other["user_id"])) == 1
    assert client.get("/api/events", headers=one).json() == []
    assert client.get("/api/events", headers=two).json() == []
