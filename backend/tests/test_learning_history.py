from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.api.history_routes import router
from backend.auth import token_digest
from backend.config import Settings
from backend.database import get_session
from backend.models import (
    Base, Document, GuestCredential, LearningEvent, ReviewSchedule,
    User, Vocabulary, WordOccurrence,
)
from backend.services.learning_history import LearningHistoryService


NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)


@pytest.fixture
def session(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(LearningHistoryService, "_meaning", lambda _self, _word, _lemma: None)
    monkeypatch.setattr("backend.services.learning_history.utcnow", lambda: NOW)
    with Session(engine, expire_on_commit=False) as db:
        db.add_all([User(id=1), User(id=2), User(id=3)])
        db.commit()
        yield db
    engine.dispose()


@pytest.fixture
def client(session, monkeypatch):
    settings = Settings(app_mode="local", llm_provider="offline", _env_file=None)
    monkeypatch.setattr("backend.auth.get_settings", lambda: settings)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as connection:
        yield connection


def vocabulary(session, lemma, status="UNKNOWN", *, user_id=1, **fields):
    entry = Vocabulary(user_id=user_id, word=fields.pop("word", lemma), lemma=lemma, status=status,
                       created_at=NOW, updated_at=NOW, **fields)
    session.add(entry)
    session.flush()
    return entry


def record(session, lemma, event_type="MARK_UNKNOWN", *, at=NOW, user_id=1,
           details=None, document_id=None, word=None):
    entry = LearningEvent(user_id=user_id, word=word or lemma, lemma=lemma, event_type=event_type,
                          created_at=at, details=details or {}, document_id=document_id)
    session.add(entry)
    return entry


def document(session, word, *, title="Own article", user_id=1, at=NOW, deleted=False, valid=True):
    sentence = f"We study {word} every day."
    doc = Document(user_id=user_id, title=title, image_path="", width=0, height=0,
                   ocr_text=sentence, source="text", created_at=at,
                   deleted_at=NOW if deleted else None)
    session.add(doc)
    session.flush()
    start = sentence.index(word)
    occurrence = WordOccurrence(document_id=doc.id, word=word, lemma=word, sentence=sentence,
                                bbox=[0, 0, 1, 1], line=0, confidence=1, bbox_source="text",
                                text_start=start if valid else 0, text_end=start + len(word))
    session.add(occurrence)
    session.flush()
    return doc, occurrence


def read(session, **kwargs):
    return LearningHistoryService(session, now=NOW).read(**kwargs)


def test_empty_history_has_full_calendar_and_explicit_zeroes(session):
    result = read(session, days=7)
    assert result.range.start_date.isoformat() == "2026-10-18"
    assert result.range.end_date.isoformat() == "2026-10-24"
    assert len(result.days) == 7
    assert set(result.summary.model_dump().values()) == {0}
    assert all(day.actions == day.words == day.reviews == day.lookups == day.marks == 0 for day in result.days)
    assert result.entries == result.focus_words == []
    assert result.pagination.model_dump() == {"page": 1, "page_size": 20, "total": 0, "total_pages": 1}


def test_summary_counts_actions_and_distinct_words_with_precise_event_semantics(session):
    alpha = vocabulary(session, "alpha")
    vocabulary(session, "beta", "KNOWN")
    record(session, "alpha", details={"origin": "review"})
    record(session, "alpha", "MARK_UNCERTAIN", details={"origin": "review"})
    record(session, "alpha")
    for _ in range(2):
        record(session, "alpha", "LOOKUP", details={"intentional": True})
    for event_type in ("VIEW_RELATED", "RECOMMENDATION_GENERATED", "RECOMMENDATION_USEFUL"):
        record(session, "beta", event_type)
    session.commit()
    result = read(session, days=7)
    assert result.summary.model_dump() == {
        "active_days": 1, "studied_words": 2, "reviewed_words": 1,
        "lookup_count": 2, "mark_count": 1, "total_actions": 8, "due_total": 1,
    }
    assert result.days[-1].model_dump(exclude={"date"}) == {
        "words": 2, "reviews": 1, "lookups": 2, "marks": 1, "actions": 8,
    }
    focus = result.focus_words[0]
    assert focus.vocabulary_id == alpha.id and focus.unfamiliar_count == 3
    assert focus.review_unfamiliar_count == 2 and focus.lookup_count == 2
    grouped = {entry.lemma: entry for entry in result.entries}
    assert grouped["alpha"].review_count == 2 and grouped["alpha"].mark_count == 1
    assert grouped["alpha"].actions == 5
    assert grouped["beta"].related_count == 3 and grouped["beta"].status == "KNOWN"


@pytest.mark.parametrize("details", [{}, {"intentional": False}, {"intentional": 1},
                                     {"intentional": "true"}, {"intentional": "false"},
                                     {"intentional": None}])
def test_lookup_requires_explicit_boolean_intentional_true(session, details):
    record(session, "alpha", "LOOKUP", details=details)
    session.commit()
    result = read(session)
    assert result.summary.total_actions == 0 and result.entries == []


def test_imports_shown_undo_and_reverted_marks_are_not_study_actions(session):
    for event_type in ("IMPORT_MAIMEMO", "RECOMMENDATION_SHOWN", "VOCABULARY_UNDO",
                       "VOCAB_MARK_REVERTED", "IMPORT", "DOCUMENT_OPENED"):
        record(session, "alpha", event_type, details={"origin": "review", "original_event_type": "MARK_KNOWN"})
    record(session, "alpha", "RECOMMENDATION_NOT_USEFUL")
    session.commit()
    result = read(session)
    assert result.summary.total_actions == 1
    assert result.entries[0].related_count == 1
    assert result.entries[0].last_event_type == "RECOMMENDATION_NOT_USEFUL"


def test_history_is_not_truncated_at_100_and_grouped_counts_are_complete(session):
    for index in range(140):
        record(session, "alpha" if index < 130 else "beta", at=NOW - timedelta(seconds=index))
    session.commit()
    result = read(session, days=7, page_size=1)
    assert result.summary.total_actions == 140 and result.summary.studied_words == 2
    assert result.pagination.total == 2 and result.entries[0].actions == 130
    assert read(session, days=7, page_size=1, page=2).entries[0].actions == 10


def test_local_date_boundaries_are_inclusive_and_future_events_are_excluded(session):
    start = datetime(2026, 10, 17, 16, tzinfo=timezone.utc)
    record(session, "first", at=start)
    record(session, "tooearly", at=start - timedelta(microseconds=1))
    record(session, "today", at=NOW)
    record(session, "future", at=NOW + timedelta(microseconds=1))
    session.commit()
    result = read(session, days=7, tz_offset_minutes=480)
    assert result.summary.total_actions == 2 and result.summary.active_days == 2
    assert result.days[0].words == result.days[-1].words == 1
    assert [entry.lemma for entry in result.entries] == ["today", "first"]
    assert result.entries[-1].date.isoformat() == "2026-10-18"
    assert result.entries[-1].model_dump(mode="json")["last_at"].endswith("+00:00")


def test_negative_timezone_uses_its_own_calendar_not_server_date(session):
    now = datetime(2026, 10, 24, 3, tzinfo=timezone.utc)
    record(session, "yesterday", at=now)
    session.commit()
    result = LearningHistoryService(session, now=now).read(days=7, tz_offset_minutes=-720)
    assert result.range.end_date.isoformat() == "2026-10-23"
    assert result.entries[0].date.isoformat() == "2026-10-23"


def test_filters_apply_to_events_before_grouping_and_never_change_summary_calendar_focus(session):
    vocabulary(session, "run")
    yesterday = NOW - timedelta(days=1)
    record(session, "run", "MARK_UNKNOWN", word="Running", at=yesterday)
    record(session, "run", "MARK_UNCERTAIN", word="Ran", at=yesterday + timedelta(seconds=1))
    record(session, "run", "MARK_KNOWN", at=NOW, details={"origin": "review"})
    record(session, "run", "LOOKUP", at=NOW, details={"intentional": True})
    session.commit()
    complete = read(session, days=7)
    filtered = read(session, days=7, day=yesterday.date(), kind="mark", q="rUNN")
    assert filtered.summary == complete.summary and filtered.days == complete.days
    assert filtered.focus_words == complete.focus_words
    assert len(filtered.entries) == 1 and filtered.entries[0].actions == 1
    assert filtered.entries[0].word == "Running" and filtered.entries[0].mark_count == 1
    assert filtered.entries[0].last_event_type == "MARK_UNKNOWN"
    review = read(session, days=7, kind="review")
    assert review.entries[0].review_count == 1 and review.entries[0].last_was_review is True
    assert review.entries[0].lookup_count == review.entries[0].mark_count == 0


def test_pagination_is_stable_grouped_and_clamps_to_last_page(session):
    for index in range(55):
        record(session, f"word{index:02}")
    session.commit()
    first = read(session, page_size=20)
    second = read(session, page_size=20, page=2)
    last = read(session, page_size=20, page=999)
    assert first.pagination.total == 55 and first.pagination.total_pages == 3
    assert first.entries[0].lemma == "word54" and second.entries[0].lemma == "word34"
    assert last.pagination.page == 3 and len(last.entries) == 15
    assert not {entry.key for entry in first.entries} & {entry.key for entry in second.entries}
    assert len({entry.key for entry in [*first.entries, *second.entries, *last.entries]}) == 55
    empty = read(session, q="no results", page=999)
    assert empty.entries == [] and empty.pagination.page == empty.pagination.total_pages == 1
    assert empty.summary.total_actions == 55


def test_same_word_on_different_days_is_two_entries_and_reviews_are_unique_per_day(session):
    for at in (NOW, NOW - timedelta(minutes=1), NOW - timedelta(days=1)):
        record(session, "alpha", details={"origin": "review"}, at=at)
    session.commit()
    result = read(session)
    assert len(result.entries) == 2 and result.summary.reviewed_words == 1
    assert result.days[-1].reviews == result.days[-2].reviews == 1
    assert result.days[-1].actions == 2


def test_focus_uses_current_unfamiliar_status_period_repetition_and_maximum_six(session):
    for index in range(9):
        lemma = f"hard{index}"
        vocabulary(session, lemma)
        for _ in range(2):
            record(session, lemma, details={"origin": "review"} if index == 0 else {})
    vocabulary(session, "known", "KNOWN")
    vocabulary(session, "unassessed", None)
    vocabulary(session, "one")
    vocabulary(session, "old")
    for lemma in ("known", "unassessed", "removed"):
        for _ in range(4):
            record(session, lemma)
    record(session, "one")
    for _ in range(2):
        record(session, "old", at=NOW - timedelta(days=30))
    vocabulary(session, "lookups", "UNCERTAIN")
    for _ in range(2):
        record(session, "lookups", "LOOKUP", details={"intentional": True})
    session.commit()
    result = read(session, days=7)
    assert len(result.focus_words) == 6 and result.focus_words[0].lemma == "hard0"
    assert not {"known", "unassessed", "removed", "one", "old"} & {entry.lemma for entry in result.focus_words}
    search = read(session, days=7, q="lookups")
    assert search.focus_words == result.focus_words


def test_lookup_repetition_can_prioritize_a_word_without_unknown_mark(session):
    vocabulary(session, "alpha", "UNCERTAIN")
    for _ in range(2):
        record(session, "alpha", "LOOKUP", details={"intentional": True})
    session.commit()
    focus = read(session).focus_words[0]
    assert focus.lookup_count == 2 and focus.unfamiliar_count == focus.review_unfamiliar_count == 0


def test_deleted_vocab_keeps_history_without_exposing_foreign_vocab_action_id(session):
    vocabulary(session, "alpha", "KNOWN", user_id=2)
    record(session, "alpha")
    session.commit()
    entry = read(session).entries[0]
    assert entry.word == "alpha" and entry.vocabulary_id is None and entry.status is None


def test_context_prefers_actual_event_doc_then_real_recent_context_not_recommendation_source(session):
    vocabulary(session, "alpha")
    older, old_word = document(session, "alpha", at=NOW - timedelta(days=1), title="Earlier source")
    recent, recent_word = document(session, "alpha", title="Recent source")
    unrelated, _ = document(session, "beta", title="Unrelated recommendation source")
    record(session, "alpha", document_id=older.id)
    record(session, "gamma", "VIEW_RELATED", document_id=unrelated.id)
    session.commit()
    rows = {entry.lemma: entry for entry in read(session).entries}
    assert rows["alpha"].context.model_dump() == {
        "document_id": older.id, "document_title": older.title,
        "occurrence_id": old_word.id, "sentence": old_word.sentence,
    }
    assert rows["gamma"].context is None
    record(session, "alpha", at=NOW + timedelta(microseconds=1), document_id=unrelated.id)
    session.commit()
    later = LearningHistoryService(session, now=NOW + timedelta(seconds=1)).read()
    # The latest source has no alpha occurrence, so the next real event source
    # is still preferred to an unrelated recommendation document.
    assert next(item for item in later.entries if item.lemma == "alpha").context.document_id == older.id


def test_context_excludes_foreign_deleted_future_and_invalid_occurrences(session):
    real, occurrence = document(session, "alpha", at=NOW - timedelta(days=2))
    deleted, _ = document(session, "alpha", deleted=True)
    foreign, _ = document(session, "alpha", user_id=2)
    document(session, "alpha", title="Future", at=NOW + timedelta(seconds=1))
    document(session, "alpha", title="Wrong offsets", valid=False)
    record(session, "alpha", document_id=foreign.id)
    record(session, "alpha", document_id=deleted.id)
    session.commit()
    result = read(session)
    assert result.entries[0].context.document_id == real.id
    assert result.entries[0].context.occurrence_id == occurrence.id
    assert "Wrong offsets" not in result.model_dump_json()


def test_due_total_matches_real_schedules_instead_of_unknown_word_count(session):
    known = vocabulary(session, "known", "KNOWN")
    unknown = vocabulary(session, "unknown")
    vocabulary(session, "unassessed", None)
    vocabulary(session, "legacy", "UNCERTAIN")
    session.add_all([
        ReviewSchedule(user_id=1, vocabulary_id=known.id, due_at=NOW, known_streak=1),
        ReviewSchedule(user_id=1, vocabulary_id=unknown.id, due_at=NOW + timedelta(days=1), known_streak=0),
    ])
    session.commit()
    assert read(session).summary.due_total == 2


def test_api_is_read_only_and_never_autoflushes_pending_rows(session, client):
    vocabulary(session, "alpha")
    record(session, "alpha")
    session.commit()
    pending = Vocabulary(user_id=1, word="pending", lemma="pending", status="UNKNOWN")
    pending_event = LearningEvent(user_id=1, word="pending", lemma="pending", event_type="MARK_UNKNOWN")
    session.add_all([pending, pending_event])
    statements = []

    def capture(_connection, _cursor, statement, *_args):
        statements.append(statement.strip().split()[0].upper())

    event.listen(session.bind, "before_cursor_execute", capture)
    try:
        first = client.get("/api/learning-history?days=7")
        second = client.get("/api/learning-history?days=7")
        assert first.status_code == 200, first.text
        assert first.json() == second.json()
        assert first.json()["summary"]["total_actions"] == 1
        assert set(statements) == {"SELECT"}
        assert pending in session.new and pending_event in session.new
        with session.no_autoflush:
            assert session.scalar(select(func.count(LearningEvent.id))) == 1
            assert session.scalar(select(func.count(Vocabulary.id))) == 1
    finally:
        event.remove(session.bind, "before_cursor_execute", capture)
        session.rollback()


@pytest.mark.parametrize("query", ["days=8", "days=0", "days=abc", "days=7.5", "page=0", "page=-1",
                                   "page_size=0", "page_size=51", "kind=invalid", "tz_offset_minutes=841",
                                   "tz_offset_minutes=-721", "day=2026-02-30", "day=2026-10-17",
                                   "day=2026-10-25", "q=" + "x" * 101])
def test_api_validates_filters_without_learning_writes(session, client, query):
    response = client.get("/api/learning-history?days=7&" + query)
    assert response.status_code == 422, response.text
    assert session.scalar(select(func.count(LearningEvent.id))) == 0


@pytest.mark.parametrize("days", [7, 30, 90])
def test_api_accepts_all_supported_day_queries(client, days):
    result = client.get(f"/api/learning-history?days={days}")
    assert result.status_code == 200, result.text
    assert result.json()["range"]["days"] == days and len(result.json()["days"]) == days


def test_public_auth_scopes_every_summary_entry_focus_and_context(session, client, monkeypatch):
    settings = Settings(app_mode="public", cors_origins="https://reader.example", _env_file=None)
    monkeypatch.setattr("backend.auth.get_settings", lambda: settings)
    for user_id, token in ((2, "a" * 43), (3, "b" * 43)):
        session.add(GuestCredential(token_hash=token_digest(token), user_id=user_id))
        vocabulary(session, "alpha", user_id=user_id)
        for _ in range(2):
            record(session, "alpha", user_id=user_id)
    vocabulary(session, "localprivate")
    record(session, "localprivate")
    own, _ = document(session, "alpha", user_id=2, title="User two article")
    foreign, _ = document(session, "alpha", user_id=3, title="User three secret")
    record(session, "alpha", user_id=2, document_id=foreign.id)
    session.commit()
    for headers in ({}, {"X-User-Id": "2"}, {"Authorization": "Bearer invalid"}):
        assert client.get("/api/learning-history", headers=headers).status_code == 401
    first = client.get("/api/learning-history", headers={"Authorization": "Bearer " + "a" * 43})
    assert first.status_code == 200, first.text
    payload = first.json()
    assert payload["summary"]["total_actions"] == 3 and payload["summary"]["due_total"] == 1
    assert len(payload["entries"]) == len(payload["focus_words"]) == 1
    assert payload["entries"][0]["context"]["document_id"] == own.id
    assert payload["focus_words"][0]["context"]["document_id"] == own.id
    assert "localprivate" not in first.text and "User three secret" not in first.text
    second = client.get("/api/learning-history", headers={"Authorization": "Bearer " + "b" * 43})
    assert second.json()["summary"]["total_actions"] == 2
    assert second.json()["entries"][0]["context"]["document_id"] == foreign.id
