"""Upgrade real legacy SQL tables, without constructing them from today's ORM."""
import json
import sqlite3

import pytest
from sqlalchemy import create_engine

from backend import migrations


LEGACY_SCHEMA = """
CREATE TABLE users (id INTEGER PRIMARY KEY, created_at DATETIME NOT NULL);
CREATE TABLE vocabulary (
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
  word VARCHAR(200) NOT NULL, lemma VARCHAR(200) NOT NULL, status VARCHAR(20),
  mastery_score FLOAT CHECK(mastery_score IS NULL OR mastery_score BETWEEN 0 AND 1),
  seen_count INTEGER NOT NULL, lookup_count INTEGER NOT NULL,
  created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, last_seen_at DATETIME,
  UNIQUE(user_id, lemma));
CREATE TABLE documents (
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), created_at DATETIME NOT NULL);
CREATE TABLE word_occurrences (
  id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id), word TEXT NOT NULL, lemma TEXT NOT NULL);
CREATE TABLE learning_events (
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), word VARCHAR(200) NOT NULL,
  event_type VARCHAR(30) NOT NULL, document_id INTEGER REFERENCES documents(id), details JSON NOT NULL,
  created_at DATETIME NOT NULL);
"""


def date(day):
    return f"2024-01-{day:02d} 12:00:00.000000"


@pytest.fixture
def legacy(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(LEGACY_SCHEMA)
    connection.executemany("INSERT INTO users VALUES (?, ?)", [(1, date(1)), (2, date(1))])
    connection.executemany("INSERT INTO vocabulary VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
        (1, 1, "Generalized", "generalized", "KNOWN", .8, 2, 1, date(2), date(2), date(2)),
        (2, 1, "generalizing", "generalizing", "UNKNOWN", .2, 3, 2, date(3), date(3), date(3)),
        (3, 1, "generalizes", "generalizes", None, None, 1, 0, date(5), date(5), date(5)),
        (4, 1, "generalization", "generalization", "KNOWN", .9, 1, 0, date(4), date(4), date(4)),
        (5, 2, "generalizes", "generalizes", "KNOWN", .9, 1, 0, date(6), date(6), date(6)),
        (6, 1, "unknown", "unknown", "UNKNOWN", None, 0, 0, date(6), date(6), None),
    ])
    connection.execute("INSERT INTO documents VALUES (1, 1, ?)", (date(1),))
    connection.execute("INSERT INTO word_occurrences VALUES (1, 1, 'Generalized', 'generalized')")
    connection.executemany("INSERT INTO learning_events VALUES (?, ?, ?, ?, ?, ?, ?)", [
        (1, 1, "Generalized", "MARK_KNOWN", 1, '{"previous_status": null}', date(2)),
        (2, 1, "generalizing", "MARK_UNKNOWN", 1, '{"previous_status": "KNOWN"}', date(3)),
        (3, 1, "generalizing", "LOOKUP", 1, '{"source": "offline"}', date(4)),
        (4, 1, "generalization", "VIEW_RELATED", 1, '{"recommended_words": ["overfitting"]}', date(5)),
    ])
    connection.commit()
    # Keep this connection open: committed data may still reside in WAL.
    engine = create_engine(f"sqlite:///{path}")
    yield path, engine, connection
    engine.dispose()
    connection.close()


def rows(path, query):
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(query)]


def test_legacy_upgrade_preserves_counts_state_times_events_and_original_backup(legacy):
    path, engine, _ = legacy
    backup = migrations.migrate_database(engine)
    assert backup and backup.is_file()
    vocab = rows(path, "SELECT * FROM vocabulary ORDER BY id")
    assert len(vocab) == 4
    merged = vocab[0]
    assert merged["lemma"] == "generalize"
    assert (merged["seen_count"], merged["lookup_count"]) == (6, 3)
    assert (merged["status"], merged["mastery_score"]) == ("UNKNOWN", .2)
    assert (merged["created_at"], merged["updated_at"]) == (date(2), date(5))
    assert (merged["first_seen_at"], merged["last_seen_at"]) == (date(1), date(5))
    assert vocab[1]["lemma"] == "generalization"  # Derivation remains its own concept.
    assert vocab[1]["first_seen_at"] == date(4)  # Legacy exposure with missing document.
    assert vocab[2]["user_id"] == 2 and vocab[2]["status"] == "KNOWN"
    assert vocab[3]["first_seen_at"] is None  # Marking alone is not a reading exposure.
    occurrence = rows(path, "SELECT * FROM word_occurrences")[0]
    assert (occurrence["word"], occurrence["lemma"]) == ("Generalized", "generalize")
    events = rows(path, "SELECT * FROM learning_events ORDER BY id")
    assert events[0]["word"] == "Generalized" and events[0]["lemma"] == "generalize"
    assert json.loads(events[1]["details"])["previous_status"] == "KNOWN"
    assert events[3]["event_type"] == "RECOMMENDATION_GENERATED"
    assert json.loads(events[3]["details"]) == {
        "recommended_words": ["overfitting"], "legacy_event_type": "VIEW_RELATED"}
    original = rows(backup, "SELECT * FROM vocabulary ORDER BY id")
    assert len(original) == 6 and original[0]["lemma"] == "generalized"
    assert "first_seen_at" not in original[0]
    assert rows(backup, "SELECT * FROM learning_events ORDER BY id")[3]["event_type"] == "VIEW_RELATED"
    assert rows(path, "PRAGMA integrity_check")[0]["integrity_check"] == "ok"
    assert rows(path, "PRAGMA foreign_key_check") == []


def test_upgrade_is_idempotent_and_does_not_make_redundant_backups(legacy):
    path, engine, _ = legacy
    migrations.migrate_database(engine)
    before = rows(path, "SELECT * FROM vocabulary")
    assert migrations.migrate_database(engine) is None
    assert rows(path, "SELECT * FROM vocabulary") == before
    assert len(list((path.parent / "backups").glob("*.sqlite3"))) == 1
    assert len(rows(path, "SELECT * FROM schema_migrations")) == 2


def test_upgrade_failure_rolls_back_schema_and_data_without_losing_backup(legacy, monkeypatch):
    path, engine, _ = legacy

    def fail_after_mutation(connection, tables):
        connection.execute("DELETE FROM vocabulary WHERE id = 1")
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(migrations, "_merge_vocabulary", fail_after_mutation)
    with pytest.raises(RuntimeError, match="simulated failure"):
        migrations.migrate_database(engine)
    original = rows(path, "SELECT * FROM vocabulary")
    assert len(original) == 6 and "first_seen_at" not in original[0]
    assert rows(path, "SELECT name FROM sqlite_master WHERE name = 'schema_migrations'") == []
    backups = list((path.parent / "backups").glob("*.sqlite3"))
    assert len(backups) == 1 and len(rows(backups[0], "SELECT * FROM vocabulary")) == 6


def test_upgrade_aborts_before_change_when_backup_fails(legacy, monkeypatch):
    path, engine, _ = legacy

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(migrations, "_backup", fail)
    with pytest.raises(OSError, match="disk full"):
        migrations.migrate_database(engine)
    original = rows(path, "SELECT * FROM vocabulary")
    assert len(original) == 6 and "first_seen_at" not in original[0]


def test_fresh_database_initializes_latest_schema(tmp_path):
    from backend.models import Base
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.sqlite3'}")
    assert migrations.migrate_database(engine) is None
    Base.metadata.create_all(engine)
    assert migrations.migrate_database(engine) is None
    with engine.connect() as connection:
        assert "first_seen_at" in {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(vocabulary)")}
        assert "lemma" in {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(learning_events)")}
    engine.dispose()


def test_mastery_backfill_replays_marks_and_only_intentional_queries(legacy):
    path, engine, connection = legacy
    connection.execute("UPDATE vocabulary SET mastery_score = NULL WHERE user_id = 1 AND id IN (1, 2)")
    connection.execute("INSERT INTO learning_events VALUES (5, 1, 'generalizes', 'LOOKUP', 1, ?, ?)",
                       ('{"source":"offline","intentional":true}', date(6)))
    connection.execute("INSERT INTO learning_events VALUES (6, 1, 'unknown', 'LOOKUP', 1, '{}', ?)", (date(6),))
    connection.commit()
    migrations.migrate_database(engine)
    vocab = rows(path, "SELECT * FROM vocabulary ORDER BY id")
    assert vocab[0]["mastery_score"] == .42  # .5 + .15 - .20 - .03; old passive LOOKUP ignored.
    assert vocab[0]["status"] == "UNKNOWN" and vocab[0]["lookup_count"] == 3
    assert vocab[1]["mastery_score"] == .9  # Existing scores preserved.
    assert vocab[2]["mastery_score"] == .9  # Other user's state preserved.
    assert vocab[3]["mastery_score"] is None  # No explicit assessment evidence.
    events = rows(path, "SELECT * FROM learning_events ORDER BY id")
    assert len(events) == 6
    first = json.loads(events[0]["details"])
    assert first == {"previous_status": None, "mastery_before": None, "mastery_after": .65,
                     "rule_version": "heuristic_v1", "mastery_backfilled": True}
    assert json.loads(events[2]["details"]) == {"source": "offline"}
    last = json.loads(events[4]["details"])
    assert last["mastery_before"] == .45 and last["mastery_after"] == .42
    assert last["source"] == "offline" and last["intentional"] is True
    assert migrations.migrate_database(engine) is None
    assert rows(path, "SELECT * FROM learning_events ORDER BY id") == events


def test_mastery_can_upgrade_previously_migrated_database(legacy, monkeypatch):
    path, engine, connection = legacy
    connection.execute("UPDATE vocabulary SET mastery_score = NULL WHERE id IN (1, 2)")
    connection.commit()
    all_migrations = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations[:1])
    migrations.migrate_database(engine)
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations)
    backup = migrations.migrate_database(engine)
    assert "before-002_mastery_heuristic" in backup.name
    assert rows(backup, "SELECT mastery_score FROM vocabulary WHERE id = 1")[0]["mastery_score"] is None
    assert rows(path, "SELECT mastery_score FROM vocabulary WHERE id = 1")[0]["mastery_score"] == .45
    assert len(rows(path, "SELECT * FROM schema_migrations")) == 2
