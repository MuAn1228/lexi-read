from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.database import get_session
from backend.main import app
from backend.models import Base, Document, LearningEvent, User, Vocabulary, WordOccurrence
from backend.services.dashboard import DashboardService


NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        db.add_all([User(id=1), User(id=2)])
        db.commit()
        yield db
    engine.dispose()


def vocabulary(session, lemma, status="UNKNOWN", **fields):
    entry = Vocabulary(word=lemma, lemma=lemma, status=status, user_id=fields.pop("user_id", 1),
                       created_at=fields.pop("created_at", NOW), updated_at=NOW, **fields)
    session.add(entry)
    return entry


def record(session, lemma, event_type, at, *, user_id=1, details=None):
    session.add(LearningEvent(user_id=user_id, word=lemma, lemma=lemma, event_type=event_type,
                              created_at=at, details=details or {}))


def document(session, lemmas, at=NOW, user_id=1):
    doc = Document(user_id=user_id, title="fixture", image_path="fixture.png", width=10,
                   height=10, ocr_text=" ".join(lemmas), created_at=at)
    session.add(doc)
    session.flush()
    for lemma in lemmas:
        session.add(WordOccurrence(document_id=doc.id, word=lemma, lemma=lemma, sentence=lemma,
                                   bbox=[0, 0, 1, 1], line=0, confidence=1, bbox_source="fixture"))
    return doc


def lemmas(items):
    return [item.vocabulary.lemma for item in items]


def test_empty_dashboard_is_explicit_zero_and_empty_lists(session):
    result = DashboardService(session).read(NOW)
    assert set(result.summary.model_dump().values()) == {0}
    assert result.profile.model_dump() == {
        "assessed_count": 0, "assessment_coverage": 0, "unscored_count": 0,
    }
    for key, value in result.model_dump().items():
        if key not in {"summary", "profile"}:
            assert value == []


def test_summary_uses_intentional_lookups_window_and_user_scope(session):
    vocabulary(session, "known", "KNOWN", mastery_score=.8)
    vocabulary(session, "unknown", mastery_score=.2)
    vocabulary(session, "uncertain", "UNCERTAIN")
    vocabulary(session, "unassessed", None)
    vocabulary(session, "other", user_id=2, mastery_score=.5)
    document(session, ["unknown"])
    document(session, ["other"], user_id=2)
    for at, details in [
        (NOW, {"intentional": True}),
        (NOW - timedelta(days=7), {"intentional": True}),
        (NOW - timedelta(days=7, microseconds=1), {"intentional": True}),
        (NOW + timedelta(microseconds=1), {"intentional": True}),
        (NOW, {"intentional": False}),
        (NOW, {}),
    ]:
        record(session, "unknown", "LOOKUP", at, details=details)
    record(session, "unknown", "LOOKUP", NOW, user_id=2, details={"intentional": True})
    record(session, "unknown", "MARK_UNKNOWN", NOW)
    session.commit()

    result = DashboardService(session).read(NOW)
    assert result.summary.model_dump() == {
        "total": 4, "known": 1, "unknown": 1, "uncertain": 1, "unassessed": 1,
        "new_unknown_7d": 1, "lookups_7d": 2, "document_count": 1, "scored_count": 2,
    }
    assert result.profile.assessed_count == 3
    assert result.profile.assessment_coverage == .75
    assert result.profile.unscored_count == 2
    assert lemmas(result.lowest_mastery) == ["unknown", "known"]
    other = DashboardService(session, user_id=2).read(NOW)
    assert other.summary.total == 1 and other.summary.lookups_7d == 1
    assert lemmas(other.lowest_mastery) == ["other"]


def test_recent_frequency_uses_document_occurrences_not_lifetime_counts(session):
    vocabulary(session, "old", seen_count=100)
    vocabulary(session, "fresh", seen_count=2, lookup_count=2)
    vocabulary(session, "known", "KNOWN", seen_count=30, lookup_count=3)
    vocabulary(session, "single", "UNCERTAIN", seen_count=1)
    vocabulary(session, "unassessed", None, seen_count=9)
    document(session, ["old"] * 100, NOW - timedelta(days=30, microseconds=1))
    document(session, ["fresh"] * 2, NOW - timedelta(days=30))
    document(session, ["known"] * 30 + ["single"] + ["unassessed"] * 9)
    document(session, ["old"] * 10 + ["single"] * 10, user_id=2)
    document(session, ["old"] * 5, NOW + timedelta(seconds=1))
    session.commit()

    result = DashboardService(session).read(NOW)
    assert lemmas(result.recent_frequent_unknown) == ["fresh"]
    assert result.recent_frequent_unknown[0].recent_seen_count == 2
    assert lemmas(result.most_seen_unknown) == ["old", "fresh", "single"]
    assert result.most_seen_unknown[0].recent_seen_count == 0
    assert lemmas(result.repeated_lookups) == ["known", "fresh"]
    assert lemmas(result.most_queried) == ["known", "fresh"]


def test_first_unfamiliar_mark_drives_windows_and_known_words_are_excluded(session):
    vocabulary(session, "newlymarked", created_at=NOW - timedelta(days=90))
    vocabulary(session, "oldunknown", "UNCERTAIN", created_at=NOW)
    vocabulary(session, "knownagain", "KNOWN")
    vocabulary(session, "justoutside")
    vocabulary(session, "future")
    vocabulary(session, "nohistory")
    record(session, "newlymarked", "MARK_KNOWN", NOW - timedelta(days=90))
    record(session, "newlymarked", "MARK_UNCERTAIN", NOW - timedelta(days=7))
    record(session, "newlymarked", "MARK_UNKNOWN", NOW - timedelta(days=1))
    record(session, "newlymarked", "MARK_UNKNOWN", NOW - timedelta(days=50), user_id=2)
    record(session, "oldunknown", "MARK_UNCERTAIN", NOW - timedelta(days=30))
    record(session, "oldunknown", "MARK_UNKNOWN", NOW)
    record(session, "knownagain", "MARK_UNKNOWN", NOW - timedelta(days=50))
    record(session, "knownagain", "MARK_KNOWN", NOW)
    record(session, "justoutside", "MARK_UNKNOWN", NOW - timedelta(days=7, microseconds=1))
    record(session, "future", "MARK_UNKNOWN", NOW + timedelta(seconds=1))
    session.commit()

    result = DashboardService(session).read(NOW)
    assert result.summary.new_unknown_7d == 1
    assert lemmas(result.recent_unknown) == ["newlymarked"]
    assert lemmas(result.longstanding_unknown) == ["oldunknown"]
    assert result.recent_unknown[0].first_unfamiliar_at.replace(tzinfo=timezone.utc) == NOW - timedelta(days=7)
    assert "+00:00" in result.recent_unknown[0].model_dump_json()


def test_rankings_have_stable_ties_and_limit_but_summary_is_not_capped(session):
    for index in reversed(range(15)):
        lemma = f"word{index:02}"
        vocabulary(session, lemma, seen_count=2, lookup_count=2, mastery_score=.2)
        record(session, lemma, "MARK_UNKNOWN", NOW)
    session.commit()
    result = DashboardService(session).read(NOW)
    assert result.summary.total == 15 and result.summary.new_unknown_7d == 15
    expected = [f"word{index:02}" for index in range(10)]
    for key in ("repeated_lookups", "recent_unknown", "lowest_mastery", "most_seen_unknown", "most_queried"):
        assert lemmas(getattr(result, key)) == expected


def test_dashboard_api_is_read_only_and_serializes_dates(session, monkeypatch):
    vocabulary(session, "model", mastery_score=.3)
    record(session, "model", "MARK_UNKNOWN", NOW)
    session.commit()
    monkeypatch.setattr("backend.services.dashboard.utcnow", lambda: NOW)
    monkeypatch.setattr("backend.main.initialize_database", lambda: None)
    app.dependency_overrides[get_session] = lambda: session
    statements = []

    def capture(_connection, _cursor, statement, *_args):
        statements.append(statement.strip().split()[0].upper())

    event.listen(session.bind, "before_cursor_execute", capture)
    try:
        with TestClient(app) as client:
            first = client.get("/api/dashboard")
            second = client.get("/api/dashboard")
        assert first.status_code == 200, first.text
        assert first.json() == second.json()
        assert first.json()["recent_unknown"][0]["first_unfamiliar_at"].endswith("+00:00")
        assert set(statements) == {"SELECT"}
        assert session.scalar(select(func.count(LearningEvent.id))) == 1
        assert session.scalar(select(func.count(Vocabulary.id))) == 1
    finally:
        event.remove(session.bind, "before_cursor_execute", capture)
        app.dependency_overrides.clear()


def test_dashboard_does_not_autoflush_pending_work(session):
    vocabulary(session, "persisted")
    session.commit()
    session.add(Vocabulary(user_id=1, word="pending", lemma="pending"))
    result = DashboardService(session).read(NOW)
    assert result.summary.total == 1
    assert len(session.new) == 1
    session.rollback()
