"""Exercise time boundaries, upgrade preservation and atomic schedule changes."""
from datetime import datetime, timedelta, timezone
import sqlite3
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select

from backend import migrations
from backend.auth import token_digest
from backend.config import Settings
from backend.models import Base, GuestCredential, LearningEvent, ReviewSchedule, Vocabulary
from backend.repositories.vocabulary import VocabularyRepository
from backend.schemas import Status
from backend.services.review import ReviewService
from backend.services.scheduling import utc
from backend.services.vocabulary import VocabularyService
from backend.tests.test_feedback import batch, feedback, feedback_app  # noqa: F401
from backend.tests.test_migrations import legacy, rows  # noqa: F401
from backend.tests.test_review import (  # noqa: F401
    NOW, client, lemmas, record, review_file_app, review_payload, session, vocabulary,
)


def state(session, lemma="model", user_id=1):
    return session.scalar(select(ReviewSchedule).join(Vocabulary).where(
        Vocabulary.lemma == lemma, Vocabulary.user_id == user_id,
    ).execution_options(populate_existing=True))


def snapshot(session, lemma="model"):
    entry = state(session, lemma)
    return (utc(entry.due_at), entry.known_streak)


def scheduled(session, lemma, due_at, status="UNKNOWN", *, user_id=1, streak=0):
    entry = vocabulary(session, lemma, status, user_id=user_id)
    session.flush()
    session.add(ReviewSchedule(vocabulary_id=entry.id, user_id=user_id,
                               due_at=due_at, known_streak=streak))
    return entry


@pytest.mark.parametrize("status,seconds", [(Status.UNKNOWN, 600), (Status.UNCERTAIN, 86400)])
def test_unfamiliar_review_sets_short_interval_resets_streak_and_returns_at_exact_due(session, status, seconds):
    scheduled(session, "model", NOW, streak=4)
    session.commit()
    VocabularyService(VocabularyRepository(session)).mark("models", status,
                                                        metadata={"origin": "review"}, now=NOW)
    assert snapshot(session) == (NOW + timedelta(seconds=seconds), 0)
    assert ReviewService(session, now=NOW).read().total == 0
    assert ReviewService(session, now=NOW + timedelta(seconds=seconds, microseconds=-1)).read().total == 0
    result = ReviewService(session, now=NOW + timedelta(seconds=seconds)).read()
    assert lemmas(result) == ["model"]
    assert result.items[0].next_intervals_seconds == {
        Status.UNKNOWN: 600, Status.UNCERTAIN: 86400, Status.KNOWN: 3 * 86400,
    }


def test_consecutive_known_reviews_follow_all_intervals_and_cap_with_known_words_returning(session):
    service = VocabularyService(VocabularyRepository(session))
    now = NOW
    for index, days in enumerate((3, 7, 14, 30, 60, 60, 60)):
        service.mark("model", Status.KNOWN, metadata={"origin": "review"}, now=now)
        due_at = now + timedelta(days=days)
        assert snapshot(session) == (due_at, min(index + 1, 5))
        assert ReviewService(session, now=due_at - timedelta(microseconds=1)).read().total == 0
        result = ReviewService(session, now=due_at).read()
        assert lemmas(result) == ["model"] and result.items[0].vocabulary.status == Status.KNOWN
        assert result.items[0].known_streak == min(index + 1, 5)
        next_days = (7, 14, 30, 60, 60, 60, 60)[index]
        assert result.items[0].next_intervals_seconds[Status.KNOWN] == next_days * 86400
        now = due_at


@pytest.mark.parametrize("status,days", [(Status.UNKNOWN, 0), (Status.UNCERTAIN, 0), (Status.KNOWN, 3)])
def test_ordinary_marks_reset_streak_and_schedule_without_counting_as_review(session, status, days):
    scheduled(session, "model", NOW - timedelta(days=10), streak=5)
    session.commit()
    service = VocabularyService(VocabularyRepository(session))
    service.mark("model", status, now=NOW)
    assert snapshot(session) == (NOW + timedelta(days=days), 0)
    result = ReviewService(session, now=NOW).read()
    assert result.total == (0 if days else 1) and result.reviewed_today == 0
    service.mark("model", Status.KNOWN, metadata={"origin": "review"}, now=NOW)
    assert snapshot(session) == (NOW + timedelta(days=3), 1)


def test_lookup_and_reading_exposure_do_not_create_or_change_schedules(session):
    scheduled(session, "model", NOW + timedelta(days=7), streak=2)
    session.commit()
    before = snapshot(session)
    service = VocabularyService(VocabularyRepository(session))
    service.observe("models", "model", 2)
    service.lookup("models", None, "offline")
    service.observe("fresh", "fresh")
    service.lookup("fresh", None, "offline")
    assert snapshot(session) == before
    assert state(session, "fresh") is None
    assert ReviewService(session, now=NOW).read().reviewed_today == 0


def test_due_summary_contains_known_overdue_and_unscheduled_unfamiliar_but_never_unassessed(session):
    scheduled(session, "oldest", NOW - timedelta(days=2), "KNOWN", streak=2)
    scheduled(session, "equal", NOW, "UNCERTAIN")
    scheduled(session, "soon", NOW + timedelta(minutes=10))
    scheduled(session, "tomorrow", NOW.replace(hour=16), "KNOWN")  # midnight in UTC+8
    scheduled(session, "unassessed", NOW - timedelta(days=3), None)
    scheduled(session, "foreign", NOW - timedelta(days=3), user_id=2)
    vocabulary(session, "legacy")
    vocabulary(session, "legacyknown", "KNOWN")
    session.commit()
    result = ReviewService(session, now=NOW).read()
    assert result.total == 3 and result.later_today == 1 and result.reviewed_today == 0
    assert utc(result.next_due_at) == NOW + timedelta(minutes=10)
    assert result.as_of == NOW
    # NULL due dates have a stable position ahead of actual timestamps.
    assert lemmas(result) == ["legacy", "oldest", "equal"]
    limited = ReviewService(session, now=NOW).read(1)
    assert limited.total == 3 and len(limited.items) == 1
    output = result.model_dump(mode="json")
    assert output["as_of"].endswith("+00:00") and output["next_due_at"].endswith("+00:00")
    assert output["items"][0]["due_at"] is None


@pytest.mark.parametrize("offset", [-720, -300, 0, 480, 840])
def test_daily_counts_use_local_midnight_include_start_exclude_end_and_count_distinct_words(session, offset):
    zone = timezone(timedelta(minutes=offset))
    now = datetime(2026, 10, 17, 0, 5, tzinfo=zone).astimezone(timezone.utc)
    start = datetime(2026, 10, 17, tzinfo=zone).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    # No vocabulary is needed to count committed reviews from today's history.
    record(session, "before", start - timedelta(microseconds=1))
    record(session, "first", start)
    record(session, "first", start + timedelta(minutes=1), event_type="MARK_KNOWN")
    record(session, "second", now, event_type="MARK_UNCERTAIN")
    record(session, "after", end)
    record(session, "foreign", now, user_id=2)
    record(session, "reading", now, details={})
    record(session, "lookup", now, event_type="LOOKUP")
    scheduled(session, "later", end - timedelta(microseconds=1))
    scheduled(session, "nextday", end)
    session.commit()
    result = ReviewService(session, now=now).read(tz_offset_minutes=offset)
    assert result.total == 0 and result.reviewed_today == 2 and result.later_today == 1
    assert utc(result.next_due_at) == end - timedelta(microseconds=1)
    rolled = ReviewService(session, now=end).read(tz_offset_minutes=offset)
    assert rolled.reviewed_today == 1 and rolled.total == 2 and rolled.later_today == 0
    assert rolled.next_due_at is None


def test_today_review_count_is_correct_when_vocabulary_is_empty(session):
    record(session, "model", NOW)
    session.commit()
    result = ReviewService(session, now=NOW).read()
    assert result.total == result.later_today == 0 and result.reviewed_today == 1
    assert result.items == [] and result.next_due_at is None


def test_resumed_batch_ids_filter_items_only_and_never_return_foreign_future_or_missing_words(session):
    first = scheduled(session, "first", NOW)
    second = scheduled(session, "second", NOW - timedelta(days=1))
    future = scheduled(session, "future", NOW + timedelta(minutes=10))
    foreign = scheduled(session, "foreign", NOW, user_id=2)
    record(session, "model", NOW)
    session.commit()
    result = ReviewService(session, now=NOW).read(vocabulary_ids=[first.id, future.id, foreign.id, 999999])
    assert lemmas(result) == ["first"]
    assert result.total == 2 and result.later_today == 1 and result.reviewed_today == 1
    assert ReviewService(session, now=NOW).read(vocabulary_ids=[]).items == []
    assert lemmas(ReviewService(session, now=NOW).read(vocabulary_ids=[second.id, first.id, first.id])) == ["second", "first"]


@pytest.mark.parametrize("query", [
    "tz_offset_minutes=-721", "tz_offset_minutes=841", "tz_offset_minutes=1.5",
    "tz_offset_minutes=bad", "vocabulary_ids=0", "vocabulary_ids=-1", "vocabulary_ids=bad",
    "&".join("vocabulary_ids=1" for _ in range(51)),
])
def test_queue_rejects_invalid_timezones_and_batch_ids_without_writes(client, session, query):
    assert client.get(f"/api/review?{query}").status_code == 422
    assert list(session.scalars(select(ReviewSchedule))) == []
    assert list(session.scalars(select(LearningEvent))) == []


def test_queue_api_accepts_repeated_ids_and_timezone_extremes(client, session):
    first = vocabulary(session, "first")
    second = vocabulary(session, "second")
    session.commit()
    result = client.get(f"/api/review?vocabulary_ids={first.id}&vocabulary_ids={second.id}&tz_offset_minutes=-720")
    assert result.status_code == 200, result.text
    assert result.json()["total"] == 2 and len(result.json()["items"]) == 2
    assert client.get("/api/review?tz_offset_minutes=840").status_code == 200


def test_public_schedules_and_today_totals_are_owned_even_with_forged_batch_ids(client, session, monkeypatch):
    settings = Settings(app_mode="public", cors_origins="https://reader.example", _env_file=None)
    monkeypatch.setattr("backend.auth.get_settings", lambda: settings)
    for user_id, token in ((2, "a" * 43), (3, "b" * 43)):
        session.add(GuestCredential(token_hash=token_digest(token), user_id=user_id))
    due_at = datetime.now(timezone.utc) - timedelta(days=1)
    own = scheduled(session, "model", due_at, "KNOWN", user_id=2, streak=2)
    other = scheduled(session, "model", due_at, "KNOWN", user_id=3, streak=4)
    session.commit()
    headers = {"Authorization": "Bearer " + "a" * 43}
    response = client.get(f"/api/review?vocabulary_ids={own.id}&vocabulary_ids={other.id}", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    assert [item["vocabulary"]["id"] for item in response.json()["items"]] == [own.id]
    assert response.json()["items"][0]["known_streak"] == 2
    foreign_due = utc(state(session, user_id=3).due_at)
    session.commit()  # Release the test read transaction before the API writer.
    assert client.put("/api/vocabulary/model", headers=headers, json=review_payload()).status_code == 200
    result = client.get("/api/review", headers=headers).json()
    assert result["total"] == 0 and result["reviewed_today"] == 1
    assert state(session, user_id=2).known_streak == 3
    foreign = state(session, user_id=3)
    assert foreign.known_streak == 4 and utc(foreign.due_at) == foreign_due
    second = client.get("/api/review", headers={"Authorization": "Bearer " + "b" * 43}).json()
    assert second["total"] == 1 and second["reviewed_today"] == 0


def test_receipt_retry_does_not_advance_interval_or_overwrite_later_schedule(review_file_app):
    client, factory, _, _ = review_file_app
    key = str(uuid4())
    payload = review_payload(request_id=key)
    assert client.put("/api/vocabulary/model", json=payload).status_code == 200
    with factory() as db:
        initial = snapshot(db)
        assert initial[1] == 1
    assert client.put("/api/vocabulary/model", json=payload).status_code == 200
    with factory() as db:
        assert snapshot(db) == initial
    assert client.put("/api/vocabulary/model", json=review_payload()).status_code == 200
    with factory() as db:
        progressed = snapshot(db)
        assert progressed[1] == 2
        mark = db.scalar(select(LearningEvent).order_by(LearningEvent.id.desc()))
        assert progressed[0] == utc(mark.created_at) + timedelta(days=7)
    assert client.put("/api/vocabulary/model", json=payload).status_code == 200
    with factory() as db:
        assert snapshot(db) == progressed
    assert client.put("/api/vocabulary/model", json={"status": "UNKNOWN"}).status_code == 200
    with factory() as db:
        reset = snapshot(db)
        assert reset[1] == 0
    assert client.put("/api/vocabulary/model", json=payload).json()["status"] == "UNKNOWN"
    with factory() as db:
        assert snapshot(db) == reset


def test_failure_after_schedule_flush_rolls_back_existing_schedule_with_mark_and_receipt(review_file_app, monkeypatch):
    client, factory, _, _ = review_file_app
    assert client.put("/api/vocabulary/model", json=review_payload()).status_code == 200
    with factory() as db:
        before = snapshot(db)
    original = VocabularyService.mark

    def fail(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.repo.session.flush()
        raise RuntimeError("failure after schedule update")

    payload = review_payload("UNKNOWN")
    monkeypatch.setattr(VocabularyService, "mark", fail)
    assert client.put("/api/vocabulary/model", json=payload).status_code == 500
    with factory() as db:
        assert snapshot(db) == before
        assert db.scalar(select(Vocabulary)).status == "KNOWN"
        assert len(list(db.scalars(select(LearningEvent)))) == 1
    monkeypatch.setattr(VocabularyService, "mark", original)
    assert client.put("/api/vocabulary/model", json=payload).status_code == 200
    with factory() as db:
        after = snapshot(db)
        mark = db.scalar(select(LearningEvent).order_by(LearningEvent.id.desc()))
        assert after == (utc(mark.created_at) + timedelta(minutes=10), 0)


@pytest.mark.parametrize("action,days", [("UNKNOWN", 0), ("KNOWN", 3)])
def test_recommendation_marks_sync_schedule_and_retries_preserve_it_without_review_credit(feedback_app, action, days):
    client, factory = feedback_app
    issued, _, _ = batch(client)
    word = issued["items"][0]["word"]
    key = str(uuid4())
    response = feedback(client, issued, action, word, key)
    assert response.status_code == 200, response.text
    with factory() as db:
        before = snapshot(db, word)
        mark = db.scalar(select(LearningEvent).where(LearningEvent.event_type == f"MARK_{action}"))
        assert before == (utc(mark.created_at) + timedelta(days=days), 0)
        assert ReviewService(db).read().reviewed_today == 0
    assert feedback(client, issued, action, word, key).status_code == 200
    # Even a newly issued duplicate recommendation choice is a semantic no-op.
    assert feedback(client, issued, action, word).status_code == 200
    with factory() as db:
        assert snapshot(db, word) == before


def test_nonassessment_recommendation_actions_never_create_schedule(feedback_app):
    client, factory = feedback_app
    issued, _, _ = batch(client)
    for action in ("SHOWN", "VIEWED", "USEFUL", "NOT_USEFUL"):
        assert feedback(client, issued, action).status_code == 200
    with factory() as db:
        assert list(db.scalars(select(ReviewSchedule))) == []


def test_recommendation_reassessment_resets_an_existing_review_streak(feedback_app):
    client, factory = feedback_app
    issued, _, _ = batch(client)
    word = issued["items"][0]["word"]
    assert feedback(client, issued, "UNKNOWN", word).status_code == 200
    for _ in range(2):
        assert client.put(f"/api/vocabulary/{word}", json=review_payload()).status_code == 200
    with factory() as db:
        assert state(db, word).known_streak == 2
    assert feedback(client, issued, "UNKNOWN", word).status_code == 200
    with factory() as db:
        due_at, streak = snapshot(db, word)
        mark = db.scalar(select(LearningEvent).order_by(LearningEvent.id.desc()))
        assert streak == 0 and due_at == utc(mark.created_at)
        assert ReviewService(db).read().reviewed_today == 1


def test_schedule_upgrade_seeds_only_assessed_words_preserves_evidence_and_backups_once(legacy, monkeypatch):
    path, engine, connection = legacy
    all_migrations = migrations.MIGRATIONS[:7]
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations[:-1])
    migrations.migrate_database(engine)
    # This is a released database containing every current assessment state.
    connection.execute("UPDATE vocabulary SET status = 'UNCERTAIN' WHERE id = 6")
    connection.execute("UPDATE vocabulary SET status = NULL WHERE id = 4")
    connection.commit()
    tables = ("vocabulary", "learning_events", "users", "documents", "word_occurrences")
    before = {table: rows(path, f"SELECT * FROM {table} ORDER BY id") for table in tables}
    start = datetime.now(timezone.utc)
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations)
    backup = migrations.migrate_database(engine)
    end = datetime.now(timezone.utc)
    assert backup and "before-007_spaced_review_schedules" in backup.name
    assert rows(backup, "SELECT name FROM sqlite_master WHERE name = 'review_schedules'") == []
    actual = rows(path, "SELECT * FROM review_schedules ORDER BY vocabulary_id")
    assessed = [row for row in before["vocabulary"] if row["status"] is not None]
    assert len(actual) == len(assessed) == 3
    for schedule, entry in zip(actual, assessed):
        assert (schedule["vocabulary_id"], schedule["user_id"], schedule["known_streak"]) == (entry["id"], entry["user_id"], 0)
        delay = timedelta(days=3 if entry["status"] == "KNOWN" else 0)
        assert start + delay <= utc(datetime.fromisoformat(schedule["due_at"])) <= end + delay
    for table in tables:
        assert rows(path, f"SELECT * FROM {table} ORDER BY id") == before[table]
        assert rows(backup, f"SELECT * FROM {table} ORDER BY id") == before[table]
    assert migrations.migrate_database(engine) is None
    assert len(list((path.parent / "backups").glob("*.sqlite3"))) == 2
    assert rows(path, "SELECT * FROM review_schedules ORDER BY vocabulary_id") == actual
    # An installed table without its migration marker must keep existing dates.
    connection.execute("DELETE FROM schema_migrations WHERE version = ?", (all_migrations[-1][0],))
    connection.commit()
    assert migrations.migrate_database(engine) is not None
    assert rows(path, "SELECT * FROM review_schedules ORDER BY vocabulary_id") == actual


def test_schedule_upgrade_rollback_leaves_original_database_and_can_retry(legacy, monkeypatch):
    path, engine, _ = legacy
    all_migrations = migrations.MIGRATIONS[:7]
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations[:-1])
    migrations.migrate_database(engine)
    before = rows(path, "SELECT * FROM vocabulary ORDER BY id")

    def fail(connection, tables):
        migrations._create_review_schedules(connection, tables)
        assert connection.execute("SELECT COUNT(*) FROM review_schedules").fetchone()[0] > 0
        raise RuntimeError("schedule seed failed")

    monkeypatch.setattr(migrations, "MIGRATIONS", [*all_migrations[:-1], (all_migrations[-1][0], fail)])
    with pytest.raises(RuntimeError, match="schedule seed failed"):
        migrations.migrate_database(engine)
    assert rows(path, "SELECT name FROM sqlite_master WHERE name = 'review_schedules'") == []
    assert rows(path, "SELECT * FROM vocabulary ORDER BY id") == before
    assert len(rows(path, "SELECT * FROM schema_migrations")) == len(all_migrations) - 1
    backup = next((path.parent / "backups").glob("*before-007_spaced_review_schedules*.sqlite3"))
    assert rows(backup, "SELECT * FROM vocabulary ORDER BY id") == before
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations)
    assert migrations.migrate_database(engine) is not None
    assert migrations.migrate_database(engine) is None


def test_schedule_upgrade_schema_matches_fresh_install_and_enforces_constraints(legacy, tmp_path):
    path, engine, connection = legacy
    migrations.migrate_database(engine)
    fresh = tmp_path / "fresh-spaced-review.sqlite3"
    fresh_engine = create_engine(f"sqlite:///{fresh}")
    Base.metadata.create_all(fresh_engine)
    fresh_engine.dispose()
    assert rows(path, "PRAGMA table_info(review_schedules)") == rows(fresh, "PRAGMA table_info(review_schedules)")
    for pragma in ("foreign_key_list", "index_list"):
        migrated = rows(path, f"PRAGMA {pragma}(review_schedules)")
        installed = rows(fresh, f"PRAGMA {pragma}(review_schedules)")
        # SQLite index sequence depends on creation order, not schema meaning.
        comparable = lambda entries: sorted(
            tuple(sorted((key, value) for key, value in entry.items() if key not in {"seq", "id"}))
            for entry in entries)
        assert comparable(migrated) == comparable(installed)
    for streak in (-1, 6):
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            connection.execute("UPDATE review_schedules SET known_streak = ?", (streak,))
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        connection.execute("UPDATE review_schedules SET user_id = 9999")
    connection.rollback()
    assert rows(path, "PRAGMA foreign_key_check") == []
