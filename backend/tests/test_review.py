from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.auth import token_digest
from backend.config import Settings
from backend.database import get_session
from backend.main import app
from backend.models import Base, Document, GuestCredential, LearningEvent, ReviewReceipt, User, Vocabulary, WordOccurrence
from backend.repositories.vocabulary import VocabularyRepository
from backend.schemas import Status
from backend.services.review import ReviewService
from backend.services.review_marks import ReviewMarkService
from backend.services.vocabulary import VocabularyService


NOW = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        db.add_all([User(id=1), User(id=2), User(id=3)])
        db.commit()
        yield db
    engine.dispose()


@pytest.fixture
def client(session, monkeypatch):
    settings = Settings(app_mode="local", llm_provider="offline", _env_file=None)
    monkeypatch.setattr("backend.auth.get_settings", lambda: settings)
    monkeypatch.setattr("backend.main.initialize_database", lambda: None)
    app.dependency_overrides[get_session] = lambda: session
    try:
        with TestClient(app) as connection:
            yield connection
    finally:
        app.dependency_overrides.clear()


def vocabulary(session, lemma, status="UNKNOWN", **fields):
    entry = Vocabulary(word=lemma, lemma=lemma, status=status, user_id=fields.pop("user_id", 1),
                       created_at=NOW, updated_at=NOW, **fields)
    session.add(entry)
    return entry


def record(session, lemma, at, event_type="MARK_UNKNOWN", *, user_id=1, details=None, document_id=None):
    entry = LearningEvent(user_id=user_id, word=lemma, lemma=lemma, event_type=event_type,
                          created_at=at, details={"origin": "review"} if details is None else details,
                          document_id=document_id)
    session.add(entry)
    return entry


def document(session, lemmas, *, title="fixture", at=NOW, user_id=1):
    doc = Document(user_id=user_id, title=title, image_path="fixture.png", width=10,
                   height=10, ocr_text=" ".join(lemmas), created_at=at)
    session.add(doc)
    session.flush()
    for index, lemma in enumerate(lemmas):
        doc.words.append(WordOccurrence(word=lemma.capitalize(), lemma=lemma,
                                        sentence=f"{title}: {lemma} {index}", bbox=[0, 0, 1, 1],
                                        line=index, confidence=1, bbox_source="fixture"))
    session.flush()
    return doc


def lemmas(result):
    return [item.vocabulary.lemma for item in result.items]


def test_empty_queue_excludes_known_unassessed_and_other_users(session):
    assert ReviewService(session).read().model_dump(include={"total", "items"}) == {"total": 0, "items": []}
    vocabulary(session, "known", "KNOWN")
    vocabulary(session, "unassessed", None)
    vocabulary(session, "other", user_id=2)
    session.commit()
    assert ReviewService(session).read().model_dump(include={"total", "items"}) == {"total": 0, "items": []}


def test_priority_is_unreviewed_oldest_review_low_mastery_seen_count_and_stable_lemma(session):
    vocabulary(session, "unreviewedhigh", mastery_score=.9)
    vocabulary(session, "unreviewedlow", mastery_score=.1)
    vocabulary(session, "unscored", mastery_score=None, seen_count=1)
    vocabulary(session, "halfa", mastery_score=.5, seen_count=2)
    vocabulary(session, "halfb", "UNCERTAIN", mastery_score=.5, seen_count=2)
    vocabulary(session, "oldreviewhigh", mastery_score=.9)
    vocabulary(session, "newreviewlow", mastery_score=0)
    vocabulary(session, "oldreviewlow", mastery_score=.1)
    for lemma, days in (("oldreviewhigh", 10), ("newreviewlow", 1), ("oldreviewlow", 10)):
        record(session, lemma, NOW - timedelta(days=days))
    # Ordinary reading, lookup and recommendation activity do not review a word.
    record(session, "unreviewedlow", NOW, details={})
    record(session, "unreviewedhigh", NOW, event_type="LOOKUP")
    session.commit()

    result = ReviewService(session).read()
    assert result.total == 8
    assert lemmas(result) == ["unreviewedlow", "halfa", "halfb", "unscored", "unreviewedhigh",
                              "oldreviewlow", "oldreviewhigh", "newreviewlow"]


def test_last_review_requires_current_user_matching_lemma_mark_and_explicit_origin(session):
    vocabulary(session, "model")
    vocabulary(session, "reading")
    expected = NOW - timedelta(days=2)
    record(session, "model", NOW - timedelta(days=3), event_type="MARK_KNOWN")
    record(session, "model", expected, event_type="MARK_UNCERTAIN")
    record(session, "model", NOW - timedelta(days=1), details={})
    record(session, "model", NOW, details={"origin": "reading"})
    record(session, "model", NOW, event_type="LOOKUP")
    record(session, "model", NOW, user_id=2)
    record(session, "different", NOW)
    record(session, "reading", NOW, details={"origin": None})
    session.commit()

    result = ReviewService(session).read()
    assert lemmas(result) == ["reading", "model"]
    assert result.items[0].last_reviewed_at is None
    assert result.items[1].last_reviewed_at.replace(tzinfo=timezone.utc) == expected
    assert result.items[1].model_dump(mode="json")["last_reviewed_at"].endswith("+00:00")


def test_context_uses_latest_owned_actual_occurrence_and_stable_document_occurrence_ties(session):
    vocabulary(session, "model")
    vocabulary(session, "recommendation")
    document(session, ["model"], title="newest by time")
    # A higher document ID cannot override the newer document timestamp.
    document(session, ["model"], title="older", at=NOW - timedelta(days=1))
    latest = document(session, ["model", "model"], title="latest tied document")
    document(session, ["model"], title="older added last", at=NOW - timedelta(days=2))
    foreign = document(session, ["model", "recommendation"], title="foreign secret",
                       at=NOW + timedelta(days=1), user_id=2)
    record(session, "recommendation", NOW, event_type="MARK_UNKNOWN", document_id=latest.id)
    record(session, "model", NOW, document_id=foreign.id)
    session.commit()

    items = {item.vocabulary.lemma: item for item in ReviewService(session).read().items}
    assert items["model"].occurrence.model_dump() == {
        "id": latest.words[-1].id, "document_id": latest.id,
        "document_title": "latest tied document", "word": "Model",
        "sentence": "latest tied document: model 1",
        "target_start": None, "target_end": None,
    }
    assert items["recommendation"].occurrence is None


def test_limit_caps_items_without_capping_total_or_changing_tie_order(session):
    for index in reversed(range(55)):
        vocabulary(session, f"word{index:02}", mastery_score=.3)
    session.commit()
    result = ReviewService(session).read(50)
    assert result.total == 55
    assert lemmas(result) == [f"word{index:02}" for index in range(50)]
    assert lemmas(ReviewService(session).read(1)) == ["word00"]


def test_reads_are_bounded_selects_and_never_flush_pending_work_or_record_events(session, client):
    for index in range(12):
        vocabulary(session, f"word{index:02}")
    document(session, [f"word{index:02}" for index in range(12)])
    session.commit()
    pending = vocabulary(session, "pending")
    statements = []

    def capture(_connection, _cursor, statement, *_args):
        statements.append(statement.strip().split()[0].upper())

    event.listen(session.bind, "before_cursor_execute", capture)
    try:
        first = client.get("/api/review")
        second = client.get("/api/review")
        assert first.status_code == 200, first.text
        assert {k: v for k, v in first.json().items() if k != "as_of"} == {
            k: v for k, v in second.json().items() if k != "as_of"}
        assert first.json()["total"] == 12 and len(first.json()["items"]) == 10
        assert statements == ["SELECT"] * 6
        assert pending in session.new
        with session.no_autoflush:
            assert session.scalar(select(func.count(LearningEvent.id))) == 0
            assert session.scalar(select(func.count(Vocabulary.id))) == 12
    finally:
        event.remove(session.bind, "before_cursor_execute", capture)
        session.rollback()


@pytest.mark.parametrize("query", ["limit=0", "limit=-1", "limit=51", "limit=abc", "limit=1.5", "limit="])
def test_review_rejects_invalid_limits(client, query):
    assert client.get(f"/api/review?{query}").status_code == 422


@pytest.mark.parametrize("origin", ["reading", "REVIEW", "", 1, True, ["review"], {"origin": "review"}])
def test_mark_rejects_invalid_review_origins_without_mutating(session, client, origin):
    vocabulary(session, "model", mastery_score=.45)
    session.commit()
    response = client.put("/api/vocabulary/model", json={"status": "KNOWN", "origin": origin})
    assert response.status_code == 422, response.text
    assert session.scalar(select(func.count(LearningEvent.id))) == 0
    entry = session.scalar(select(Vocabulary))
    assert entry.status == "UNKNOWN" and entry.mastery_score == .45


@pytest.mark.parametrize("status,expected_score,expected_total", [
    ("KNOWN", .6, 0), ("UNKNOWN", .25, 0), ("UNCERTAIN", .37, 0),
])
def test_review_mark_reuses_existing_mastery_rule_and_updates_queue(session, client, status, expected_score, expected_total):
    vocabulary(session, "model", mastery_score=.45, seen_count=4, lookup_count=2)
    doc = document(session, ["model"])
    session.commit()
    response = client.put("/api/vocabulary/model", json={
        "status": status, "origin": "review", "document_id": doc.id,
    })
    assert response.status_code == 200, response.text
    assert response.json()["status"] == status
    assert response.json()["mastery_score"] == expected_score
    assert response.json()["seen_count"] == 4 and response.json()["lookup_count"] == 2
    events = list(session.scalars(select(LearningEvent)))
    assert len(events) == 1
    assert events[0].document_id == doc.id and events[0].event_type == f"MARK_{status}"
    assert events[0].details == {
        "origin": "review", "previous_status": "UNKNOWN", "mastery_before": .45,
        "mastery_after": expected_score, "rule_version": "heuristic_v1",
    }
    queue = client.get("/api/review").json()
    assert queue["total"] == expected_total
    if expected_total:
        assert queue["items"][0]["last_reviewed_at"].endswith("+00:00")
    assert session.scalar(select(func.count(Vocabulary.id))) == 1


@pytest.mark.parametrize("payload", [{"status": "UNCERTAIN"}, {"status": "UNCERTAIN", "origin": None}])
def test_ordinary_marks_do_not_count_as_review_or_erase_previous_review(session, client, payload):
    vocabulary(session, "model", mastery_score=.3)
    session.commit()
    assert client.put("/api/vocabulary/model", json=payload).status_code == 200
    assert client.get("/api/review").json()["items"][0]["last_reviewed_at"] is None
    assert "origin" not in session.scalar(select(LearningEvent)).details
    assert client.put("/api/vocabulary/model", json={"status": "UNKNOWN", "origin": "review"}).status_code == 200
    assert client.get("/api/review").json()["total"] == 0
    reviewed_at = session.scalar(select(LearningEvent).where(
        LearningEvent.details["origin"].as_string() == "review")).created_at
    assert client.put("/api/vocabulary/model", json=payload).status_code == 200
    assert datetime.fromisoformat(client.get("/api/review").json()["items"][0]["last_reviewed_at"]).replace(
        tzinfo=None) == reviewed_at.replace(tzinfo=None)
    last = session.scalar(select(LearningEvent).order_by(LearningEvent.id.desc()))
    assert "origin" not in last.details and last.document_id is None


def test_public_review_authentication_and_isolation_cover_queue_context_history_and_marks(session, client, monkeypatch):
    settings = Settings(app_mode="public", cors_origins="https://reader.example", _env_file=None)
    monkeypatch.setattr("backend.auth.get_settings", lambda: settings)
    for user_id, token in ((2, "a" * 43), (3, "b" * 43)):
        session.add(GuestCredential(token_hash=token_digest(token), user_id=user_id))
    vocabulary(session, "localprivate", user_id=1)
    vocabulary(session, "model", user_id=2)
    vocabulary(session, "model", user_id=3)
    vocabulary(session, "thirdprivate", user_id=3)
    own = document(session, ["model"], title="own", user_id=2)
    foreign = document(session, ["model"], title="foreign private", user_id=3, at=NOW + timedelta(days=1))
    record(session, "model", NOW, user_id=3)
    session.commit()
    one = {"Authorization": "Bearer " + "a" * 43}
    two = {"Authorization": "Bearer " + "b" * 43}
    for headers in ({}, {"X-User-Id": "2"}, {"Authorization": "Bearer invalid"}):
        assert client.get("/api/review", headers=headers).status_code == 401
        assert client.put("/api/vocabulary/model", headers=headers,
                          json={"status": "KNOWN", "origin": "review"}).status_code == 401

    first = client.get("/api/review", headers=one).json()
    assert first["total"] == 1
    assert first["items"][0]["vocabulary"]["user_id"] == 2
    assert first["items"][0]["last_reviewed_at"] is None
    assert first["items"][0]["occurrence"]["document_id"] == own.id
    assert client.get("/api/review", headers=two).json()["total"] == 2
    assert client.put("/api/vocabulary/model", headers=one,
                      json={"status": "KNOWN", "origin": "review", "document_id": foreign.id}).status_code == 404
    assert session.scalar(select(func.count(LearningEvent.id))) == 1
    assert client.put("/api/vocabulary/model", headers=one,
                      json={"status": "KNOWN", "origin": "review", "document_id": own.id}).status_code == 200
    assert client.get("/api/review", headers=one).json()["total"] == 0
    other = client.get("/api/review", headers=two).json()
    assert other["total"] == 2
    model = next(item for item in other["items"] if item["vocabulary"]["lemma"] == "model")
    assert model["vocabulary"]["status"] == "UNKNOWN"
    assert model["occurrence"]["document_id"] == foreign.id
    assert len(client.get("/api/events", headers=two).json()) == 1


@pytest.fixture
def review_file_app(tmp_path, monkeypatch):
    # Separate connections and real file locks exercise retry transactions.
    engine = create_engine(f"sqlite:///{tmp_path / 'review.sqlite3'}",
                           connect_args={"check_same_thread": False, "timeout": 10})

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add_all([User(id=1), User(id=2), User(id=3)])
        db.flush()
        vocabulary(db, "model", mastery_score=.5, seen_count=4, lookup_count=2)
        own = document(db, ["model"])
        foreign = document(db, ["model"], user_id=2)
        db.commit()
        own_id, foreign_id = own.id, foreign.id

    def session_override():
        with factory() as db:
            yield db

    settings = Settings(app_mode="local", llm_provider="offline", _env_file=None)
    monkeypatch.setattr("backend.auth.get_settings", lambda: settings)
    monkeypatch.setattr("backend.main.initialize_database", lambda: None)
    app.dependency_overrides[get_session] = session_override
    try:
        with TestClient(app, raise_server_exceptions=False) as connection:
            yield connection, factory, own_id, foreign_id
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def review_payload(status="KNOWN", *, request_id=None, document_id=None):
    return {"status": status, "origin": "review", "request_id": request_id or str(uuid4()),
            "document_id": document_id}


def test_lost_response_exact_retry_commits_one_mark_score_and_receipt(review_file_app):
    client, factory, own_id, _ = review_file_app
    payload = review_payload(document_id=own_id)
    # Discard the first response, as when a network failure follows the commit.
    client.put("/api/vocabulary/Models", json=payload)
    result = client.put("/api/vocabulary/model", json=payload)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "KNOWN" and result.json()["mastery_score"] == .65
    assert result.json()["seen_count"] == 4 and result.json()["lookup_count"] == 2
    with factory() as db:
        assert db.scalar(select(func.count(Vocabulary.id))) == 1
        assert db.scalar(select(func.count(LearningEvent.id))) == 1
        receipt = db.scalar(select(ReviewReceipt))
        assert receipt.lemma == "model" and receipt.status == "KNOWN" and receipt.document_id == own_id
        assert receipt.request_id == payload["request_id"]
        mark = db.scalar(select(LearningEvent))
        assert mark.details["origin"] == "review" and mark.details["request_id"] == payload["request_id"]


def test_delayed_retry_returns_current_state_after_a_later_choice(review_file_app):
    client, factory, own_id, _ = review_file_app
    first = review_payload(document_id=own_id)
    assert client.put("/api/vocabulary/model", json=first).status_code == 200
    later = client.put("/api/vocabulary/model", json=review_payload("UNKNOWN", document_id=own_id))
    assert later.status_code == 200
    assert later.json()["mastery_score"] == .45
    before = client.get("/api/events").json()
    retry = client.put("/api/vocabulary/model", json=first)
    assert retry.status_code == 200 and retry.json() == later.json()
    assert client.get("/api/events").json() == before
    with factory() as db:
        assert db.scalar(select(func.count(ReviewReceipt.id))) == 2


@pytest.mark.parametrize("changed_field", ["word", "status", "document_id"])
def test_reused_review_key_with_different_payload_conflicts_without_writing(review_file_app, changed_field):
    client, factory, own_id, _ = review_file_app
    payload = review_payload(document_id=own_id)
    assert client.put("/api/vocabulary/model", json=payload).status_code == 200
    changed, word = dict(payload), "model"
    if changed_field == "word":
        word = "example"
    elif changed_field == "status":
        changed["status"] = "UNKNOWN"
    else:
        changed["document_id"] = None
    before = client.get("/api/events").json()
    response = client.put(f"/api/vocabulary/{word}", json=changed)
    assert response.status_code == 409, response.text
    assert client.get("/api/events").json() == before
    with factory() as db:
        assert db.scalar(select(func.count(ReviewReceipt.id))) == 1
        assert db.scalar(select(func.count(Vocabulary.id))) == 1


@pytest.mark.parametrize("origin", [None, "omitted"])
def test_request_id_requires_explicit_review_origin_and_valid_uuid(session, client, origin):
    payload = {"status": "KNOWN", "request_id": str(uuid4())}
    if origin is None:
        payload["origin"] = None
    assert client.put("/api/vocabulary/model", json=payload).status_code == 422
    assert client.put("/api/vocabulary/model", json={"status": "KNOWN", "origin": "review",
                                                  "request_id": "invalid-uuid"}).status_code == 422
    assert session.scalar(select(func.count(Vocabulary.id))) == 0
    assert session.scalar(select(func.count(LearningEvent.id))) == 0
    assert session.scalar(select(func.count(ReviewReceipt.id))) == 0


@pytest.mark.parametrize("failure_type,expected_status", [(RuntimeError, 500), (IntegrityError, 503)])
def test_failure_after_mark_flush_rolls_back_score_events_and_receipt(review_file_app, monkeypatch, failure_type, expected_status):
    client, factory, own_id, _ = review_file_app
    payload = review_payload(document_id=own_id)
    original = VocabularyService.mark

    def fail_after_flush(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.repo.session.flush()
        if failure_type is IntegrityError:
            raise IntegrityError("injected unrelated failure", {}, RuntimeError("fixture failure"))
        raise RuntimeError("Injected failure after mark and receipt writes")

    monkeypatch.setattr(VocabularyService, "mark", fail_after_flush)
    response = client.put("/api/vocabulary/model", json=payload)
    assert response.status_code == expected_status, response.text
    with factory() as db:
        assert db.scalar(select(Vocabulary)).mastery_score == .5
        assert db.scalar(select(Vocabulary)).status == "UNKNOWN"
        assert db.scalar(select(func.count(LearningEvent.id))) == 0
        assert db.scalar(select(func.count(ReviewReceipt.id))) == 0
    monkeypatch.setattr(VocabularyService, "mark", original)
    assert client.put("/api/vocabulary/model", json=payload).json()["mastery_score"] == .65


def test_integrity_race_recovers_only_an_existing_matching_receipt(review_file_app, monkeypatch):
    client, factory, own_id, _ = review_file_app
    payload = review_payload(document_id=own_id)
    expected = client.put("/api/vocabulary/model", json=payload).json()
    original = ReviewMarkService._receipt
    reads = 0

    def miss_first_read(self, key):
        nonlocal reads
        reads += 1
        return None if reads == 1 else original(self, key)

    # Simulate another worker committing between receipt read and insert.
    monkeypatch.setattr(ReviewMarkService, "_receipt", miss_first_read)
    retry = client.put("/api/vocabulary/model", json=payload)
    assert retry.status_code == 200, retry.text
    assert retry.json() == expected
    assert reads == 2
    with factory() as db:
        assert db.scalar(select(func.count(LearningEvent.id))) == 1
        assert db.scalar(select(func.count(ReviewReceipt.id))) == 1


@pytest.mark.parametrize("word", ["model", "latent"])
def test_simultaneous_review_retries_commit_one_delta_event_and_receipt(review_file_app, word):
    client, factory, own_id, _ = review_file_app
    payload = review_payload(document_id=own_id)
    barrier = Barrier(2)

    def send():
        barrier.wait(timeout=5)
        return client.put(f"/api/vocabulary/{word}", json=payload)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(send) for _ in range(2)]
        responses = [future.result(timeout=15) for future in futures]
    assert all(response.status_code == 200 for response in responses), [response.text for response in responses]
    assert responses[0].json() == responses[1].json()
    assert responses[0].json()["mastery_score"] == .65
    with factory() as db:
        assert db.scalar(select(func.count(LearningEvent.id))) == 1
        assert db.scalar(select(func.count(ReviewReceipt.id))) == 1
        assert db.scalar(select(func.count(Vocabulary.id)).where(Vocabulary.lemma == word)) == 1


def test_receipts_are_scoped_to_public_user_and_document_ownership(review_file_app, monkeypatch):
    client, factory, own_id, foreign_id = review_file_app
    request_id = str(uuid4())
    assert client.put("/api/vocabulary/model", json=review_payload(request_id=request_id, document_id=own_id)).status_code == 200
    settings = Settings(app_mode="public", cors_origins="https://reader.example", _env_file=None)
    monkeypatch.setattr("backend.auth.get_settings", lambda: settings)
    with factory() as db:
        db.add_all([GuestCredential(token_hash=token_digest("a" * 43), user_id=2),
                    GuestCredential(token_hash=token_digest("b" * 43), user_id=3)])
        db.commit()
    one = {"Authorization": "Bearer " + "a" * 43}
    two = {"Authorization": "Bearer " + "b" * 43}
    payload = review_payload("UNKNOWN", request_id=request_id, document_id=foreign_id)
    assert client.put("/api/vocabulary/model", json=payload).status_code == 401
    assert client.put("/api/vocabulary/model", headers=two, json=payload).status_code == 404
    saved = client.put("/api/vocabulary/model", headers=one, json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["user_id"] == 2 and saved.json()["mastery_score"] == .3
    assert client.put("/api/vocabulary/model", headers=one, json=payload).json() == saved.json()
    with factory() as db:
        assert db.scalar(select(func.count(ReviewReceipt.id))) == 2
        assert db.scalar(select(func.count(LearningEvent.id))) == 2
        local = db.scalar(select(Vocabulary).where(Vocabulary.user_id == 1))
        assert local.status == "KNOWN" and local.mastery_score == .65


def test_reused_session_refreshes_current_mastery_and_delayed_retry_state(review_file_app):
    _, factory, own_id, _ = review_file_app
    with factory() as stale, factory() as other:
        cached = stale.scalar(select(Vocabulary).where(Vocabulary.lemma == "model"))
        initial_key, later_key = uuid4(), uuid4()
        service = ReviewMarkService(VocabularyRepository(stale))
        ReviewMarkService(VocabularyRepository(other)).apply("model", Status.KNOWN, own_id, initial_key)
        assert cached.mastery_score == .5
        result = service.apply("model", Status.UNKNOWN, own_id, later_key)
        assert result.mastery_score == .45
        VocabularyService(VocabularyRepository(other)).mark("model", Status.KNOWN)
        delayed = service.apply("model", Status.UNKNOWN, own_id, later_key)
        assert delayed.status == "KNOWN" and delayed.mastery_score == .6


def test_legacy_review_without_request_key_keeps_each_explicit_mark(review_file_app):
    client, factory, _, _ = review_file_app
    payload = {"status": "UNKNOWN", "origin": "review"}
    assert client.put("/api/vocabulary/model", json=payload).json()["mastery_score"] == .3
    assert client.put("/api/vocabulary/model", json=payload).json()["mastery_score"] == .1
    with factory() as db:
        assert db.scalar(select(func.count(LearningEvent.id))) == 2
        assert db.scalar(select(func.count(ReviewReceipt.id))) == 0
