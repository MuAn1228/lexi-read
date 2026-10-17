"""Public budgets persist, reserve atomically, and reject work before side effects."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
from threading import Barrier, BoundedSemaphore

from fastapi import HTTPException
from PIL import Image
import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from backend.config import Settings
from backend.models import Base, Document, GuestCredential, LearningEvent, User
from backend.ocr.service import OCRResult, OCRUnavailable, OCRWord
from backend import public_limits as limits
from backend.public_limits import PublicUsage
from backend.services.llm import CandidateBatch, ExplanationData, LLMService
from backend.tests.test_public_auth import demo, guest, public_app  # noqa: F401


@pytest.fixture
def clock(monkeypatch):
    current = [datetime(2026, 10, 17, 12, 59, 59, tzinfo=timezone.utc)]
    monkeypatch.setattr(limits, "_now", lambda: current[0])
    return current


@pytest.fixture
def budget_store(tmp_path, monkeypatch, clock):
    settings = Settings(app_mode="public", cors_origins="https://reader.example",
                        data_dir=tmp_path, _env_file=None)
    monkeypatch.setattr(limits, "get_settings", lambda: settings)
    engine = create_engine(f"sqlite:///{tmp_path / 'budgets.sqlite3'}",
                           connect_args={"check_same_thread": False, "timeout": 10})

    @event.listens_for(engine, "connect")
    def configure(connection, _):
        connection.execute("PRAGMA journal_mode=WAL")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory, settings
    finally:
        engine.dispose()


def counts(factory):
    with factory() as session:
        return dict(session.execute(select(PublicUsage.bucket, PublicUsage.count)).all())


def request_from(address, forwarded=None):
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request({"type": "http", "client": (address, 1234), "headers": headers})


def test_concurrent_file_database_reservations_never_exceed_global_or_user_caps(budget_store, clock):
    factory, _ = budget_store
    expiry = int(clock[0].timestamp()) + 3600
    start = Barrier(24)

    def reserve(index):
        start.wait(timeout=10)
        with factory() as session:
            try:
                limits.consume(session, [("shared", 7, expiry), (f"user:{index % 4}", 3, expiry)])
                return 200
            except HTTPException as exc:
                assert exc.status_code == 429
                assert exc.headers["Retry-After"] == "3600"
                return 429

    with ThreadPoolExecutor(max_workers=24) as workers:
        statuses = list(workers.map(reserve, range(24)))
    assert statuses.count(200) == 7
    assert statuses.count(429) == 17
    stored = counts(factory)
    assert stored.pop("shared") == 7
    assert sum(stored.values()) == 7
    assert all(0 < count <= 3 for count in stored.values())


@pytest.mark.parametrize("kind", ["llm", "ocr"])
def test_exhausted_user_does_not_spend_global_budget(budget_store, clock, kind):
    factory, settings = budget_store
    setattr(settings, f"public_{kind}_daily_limit", 2)
    setattr(settings, f"public_{kind}_user_daily_limit", 1)
    day = int(clock[0].timestamp()) // 86400
    with factory() as session:
        limits._reserve(session, 11, kind)
        with pytest.raises(HTTPException) as error:
            limits._reserve(session, 11, kind)
        assert error.value.status_code == 429
        assert counts(factory) == {f"{kind}:{day}:global": 1, f"{kind}:{day}:user:11": 1}
        limits._reserve(session, 12, kind)
        with pytest.raises(HTTPException):
            limits._reserve(session, 13, kind)
    assert counts(factory) == {
        f"{kind}:{day}:global": 2, f"{kind}:{day}:user:11": 1, f"{kind}:{day}:user:12": 1,
    }


def test_budget_survives_caller_rollback_and_engine_restart(budget_store, clock):
    factory, _ = budget_store
    expiry = int(clock[0].timestamp()) + 3600
    with factory() as session:
        session.add(User(id=99))
        limits.consume(session, [("persisted", 1, expiry)])
        # Reserving a quota must not commit pending learning/user changes.
        session.rollback()
    with factory() as session:
        assert session.get(User, 99) is None

    original_engine = factory.kw["bind"]
    database_url = original_engine.url
    original_engine.dispose()
    restarted = create_engine(database_url)
    try:
        restarted_factory = sessionmaker(bind=restarted)
        assert counts(restarted_factory) == {"persisted": 1}
        with restarted_factory() as session, pytest.raises(HTTPException) as error:
            limits.consume(session, [("persisted", 1, expiry)])
        assert error.value.status_code == 429
    finally:
        restarted.dispose()


def test_rejected_budget_does_not_roll_back_caller_changes(budget_store, clock):
    factory, _ = budget_store
    expiry = int(clock[0].timestamp()) + 3600
    with factory() as session:
        limits.consume(session, [("full", 1, expiry)])
        session.add(User(id=99))
        with pytest.raises(HTTPException):
            limits.consume(session, [("new", 2, expiry), ("full", 1, expiry)])
        session.commit()
    with factory() as session:
        assert session.get(User, 99) is not None
    assert counts(factory) == {"full": 1}


@pytest.mark.parametrize("kind", ["llm", "ocr"])
def test_daily_budget_resets_at_utc_midnight(budget_store, clock, kind):
    factory, settings = budget_store
    setattr(settings, f"public_{kind}_user_daily_limit", 1)
    clock[0] = clock[0].replace(hour=23, minute=59, second=59)
    old_day = int(clock[0].timestamp()) // 86400
    with factory() as session:
        limits._reserve(session, 7, kind)
        with pytest.raises(HTTPException) as error:
            limits._reserve(session, 7, kind)
        assert error.value.headers["Retry-After"] == "1"
        clock[0] += timedelta(seconds=1)
        limits._reserve(session, 7, kind)
    assert counts(factory) == {
        f"{kind}:{old_day}:global": 1, f"{kind}:{old_day}:user:7": 1,
        f"{kind}:{old_day + 1}:global": 1, f"{kind}:{old_day + 1}:user:7": 1,
    }


def test_signup_caps_share_global_budget_and_rotate_ip_hashes(budget_store, clock):
    factory, settings = budget_store
    settings.public_signup_hourly_limit = 1
    settings.public_signup_daily_limit = 3
    address = "203.0.113.8"
    old_day = int(clock[0].timestamp()) // 86400
    old_fingerprint = hashlib.sha256(f"{old_day}:{address}".encode()).hexdigest()
    with factory() as session:
        limits.check_signup_budget(request_from(address, "198.51.100.1"), session)
        with pytest.raises(HTTPException) as error:
            limits.check_signup_budget(request_from(address, "198.51.100.2"), session)
        assert error.value.headers["Retry-After"] == "1"
        limits.check_signup_budget(request_from("203.0.113.9"), session)
        clock[0] += timedelta(seconds=1)
        limits.check_signup_budget(request_from(address), session)
        with pytest.raises(HTTPException) as error:
            limits.check_signup_budget(request_from("203.0.113.10"), session)
        assert error.value.status_code == 429
        assert counts(factory)[f"signup:{old_day}:global"] == 3
        clock[0] = (clock[0] + timedelta(days=1)).replace(hour=0, minute=0, second=0)
        limits.check_signup_budget(request_from(address), session)

    stored = counts(factory)
    ip_keys = [key for key in stored if ":ip:" in key]
    new_fingerprint = hashlib.sha256(f"{old_day + 1}:{address}".encode()).hexdigest()
    assert any(key.endswith(old_fingerprint) for key in ip_keys)
    assert any(key.endswith(new_fingerprint) for key in ip_keys)
    assert old_fingerprint != new_fingerprint
    assert all(address not in key and "198.51.100" not in key for key in stored)
    assert sum(stored[key] for key in ip_keys) == 4


@pytest.mark.parametrize("kind,capacity", [("llm", 2), ("ocr", 1)])
def test_expensive_work_slots_reject_excess_work_without_spending_budget(budget_store, kind, capacity):
    factory, _ = budget_store
    require = contextmanager(getattr(limits, f"require_{kind}_budget"))
    with factory() as session, ExitStack() as stack:
        for _ in range(capacity):
            stack.enter_context(require(user_id=7, session=session))
        before = counts(factory)
        with pytest.raises(HTTPException) as error:
            with require(user_id=7, session=session):
                pytest.fail("Capacity rejection must occur before expensive work")
        assert error.value.status_code == 429
        assert error.value.headers["Retry-After"] == "10"
        assert counts(factory) == before
    with factory() as session, require(user_id=7, session=session):
        pass


@pytest.mark.parametrize("kind", ["llm", "ocr"])
def test_work_slot_is_released_after_provider_failure_and_quota_rejection(budget_store, monkeypatch, kind):
    factory, settings = budget_store
    setattr(settings, f"public_{kind}_user_daily_limit", 2)
    slots = BoundedSemaphore(1)
    monkeypatch.setattr(limits, f"_{kind}_slots", slots)
    require = contextmanager(getattr(limits, f"require_{kind}_budget"))
    with factory() as session:
        with pytest.raises(RuntimeError, match="provider failed"):
            with require(user_id=7, session=session):
                raise RuntimeError("provider failed")
        with require(user_id=7, session=session):
            pass
        with pytest.raises(HTTPException) as error:
            with require(user_id=7, session=session):
                pytest.fail("A failed attempt still spends quota")
        assert error.value.status_code == 429
    assert slots.acquire(blocking=False)
    slots.release()


@pytest.mark.parametrize("kind", ["llm", "ocr"])
def test_local_work_bypasses_both_counters_and_busy_slots(budget_store, monkeypatch, kind):
    factory, settings = budget_store
    settings.app_mode = "local"
    slots = BoundedSemaphore(1)
    assert slots.acquire(blocking=False)
    monkeypatch.setattr(limits, f"_{kind}_slots", slots)
    require = contextmanager(getattr(limits, f"require_{kind}_budget"))
    with factory() as session, require(user_id=1, session=session):
        pass
    assert counts(factory) == {}
    assert not slots.acquire(blocking=False)
    slots.release()


def test_local_signup_does_not_reserve_budget(budget_store):
    factory, settings = budget_store
    settings.app_mode = "local"
    with factory() as session:
        limits.check_signup_budget(request_from("203.0.113.8"), session)
    assert counts(factory) == {}


def test_signup_route_rejects_before_creating_another_guest(public_app, clock):
    client, factory, settings, _ = public_app
    settings.public_signup_daily_limit = 1
    guest(client)
    response = client.post("/api/session")
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0
    with factory() as session:
        assert session.scalar(select(func.count(User.id))) == 2  # Legacy user and one guest.
        assert session.scalar(select(func.count(GuestCredential.user_id))) == 1
        assert session.scalar(select(func.count(LearningEvent.id))) == 0


@pytest.mark.parametrize("first_path", ["analyze", "recommend"])
def test_llm_routes_share_quota_and_reject_without_provider_or_learning_changes(public_app, clock,
                                                                              monkeypatch, first_path):
    client, factory, settings, _ = public_app
    settings.public_llm_user_daily_limit = 1
    settings.llm_provider = "openai_compatible"
    _, headers = guest(client)
    document = demo(client, headers)
    payload = {"occurrence_id": document["words"][0]["id"]}
    calls = []

    def complete(self, instruction, data, schema):
        calls.append(schema)
        if schema is CandidateBatch:
            return CandidateBatch(candidates=[])
        assert schema is ExplanationData
        return ExplanationData(word=data["current_word"], lemma=data["lemma"], meaning_in_context="含义",
                               simple_explanation="解释", part_of_speech="noun", importance=.5)

    monkeypatch.setattr(LLMService, "_complete", complete)
    response = client.post(f"/api/words/{first_path}", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    before_events = client.get("/api/events", headers=headers).json()
    before_vocabulary = client.get("/api/vocabulary", headers=headers).json()
    assert len(calls) == 1
    for path in ("analyze", "recommend"):
        response = client.post(f"/api/words/{path}", json=payload, headers=headers)
        assert response.status_code == 429
        assert int(response.headers["Retry-After"]) > 0
    assert len(calls) == 1
    assert client.get("/api/events", headers=headers).json() == before_events
    assert client.get("/api/vocabulary", headers=headers).json() == before_vocabulary
    assert sum(count for key, count in counts(factory).items() if key.startswith("llm:")) == 2


def test_invalid_llm_request_spends_attempt_and_releases_work_slot(public_app, clock, monkeypatch):
    client, factory, settings, _ = public_app
    settings.public_llm_user_daily_limit = 2
    monkeypatch.setattr(limits, "_llm_slots", BoundedSemaphore(1))
    _, headers = guest(client)
    document = demo(client, headers)
    before_events = client.get("/api/events", headers=headers).json()
    response = client.post("/api/words/analyze", json={}, headers=headers)
    assert response.status_code == 422
    assert client.get("/api/events", headers=headers).json() == before_events
    payload = {"occurrence_id": document["words"][0]["id"]}
    response = client.post("/api/words/analyze", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    response = client.post("/api/words/analyze", json=payload, headers=headers)
    assert response.status_code == 429
    assert sum(count for key, count in counts(factory).items() if key.startswith("llm:")) == 4


@pytest.mark.parametrize("path", ["/api/documents", "/api/documents/upload"])
def test_ocr_failure_counts_but_releases_slot_and_quota_rejection_has_no_side_effects(public_app, clock,
                                                                                  monkeypatch, path):
    client, factory, settings, _ = public_app
    settings.public_ocr_user_daily_limit = 2
    guest_data, headers = guest(client)
    image = BytesIO()
    Image.new("RGB", (120, 60), "white").save(image, "PNG")
    calls = []

    class FakeOCR:
        def recognize(self, image_path):
            calls.append(image_path)
            if len(calls) == 1:
                raise OCRUnavailable("test provider unavailable")
            return OCRResult("model", 120, 60, [OCRWord("model", [0, 0, 40, 20], 0, 1)])

    monkeypatch.setattr("backend.services.documents.get_ocr_service", lambda: FakeOCR())

    def upload():
        return client.post(path, headers=headers,
                           files={"file": ("image.png", image.getvalue(), "image/png")})

    assert upload().status_code == 503
    assert client.get("/api/documents", headers=headers).json() == []
    assert client.get("/api/events", headers=headers).json() == []
    assert not list((settings.storage_dir / "images").glob("*.png"))
    response = upload()
    assert response.status_code == 201, response.text
    before_events = client.get("/api/events", headers=headers).json()
    before_vocabulary = client.get("/api/vocabulary", headers=headers).json()
    before_images = list((settings.storage_dir / "images").glob("*.png"))
    assert before_events == []
    assert len(before_vocabulary) == 1 and before_vocabulary[0]["seen_count"] == 1
    response = upload()
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0
    assert len(calls) == 2
    assert client.get("/api/events", headers=headers).json() == before_events
    assert client.get("/api/vocabulary", headers=headers).json() == before_vocabulary
    assert list((settings.storage_dir / "images").glob("*.png")) == before_images
    with factory() as session:
        assert session.scalar(select(func.count(Document.id)).where(Document.user_id == guest_data["user_id"])) == 1
