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
    assert len(rows(path, "SELECT * FROM schema_migrations")) == len(migrations.MIGRATIONS)


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
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE name IN ('recommendation_feedback', 'feedback_receipts')"
        ).all() == []
    Base.metadata.create_all(engine)
    assert migrations.migrate_database(engine) is None
    with engine.connect() as connection:
        assert "first_seen_at" in {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(vocabulary)")}
        assert "lemma" in {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(learning_events)")}
        assert {row[0] for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE name IN ('recommendation_feedback', 'feedback_receipts')"
        )} == {"recommendation_feedback", "feedback_receipts"}
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
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations[:2])
    backup = migrations.migrate_database(engine)
    assert "before-002_mastery_heuristic" in backup.name
    assert rows(backup, "SELECT mastery_score FROM vocabulary WHERE id = 1")[0]["mastery_score"] is None
    assert rows(path, "SELECT mastery_score FROM vocabulary WHERE id = 1")[0]["mastery_score"] == .45
    assert len(rows(path, "SELECT * FROM schema_migrations")) == 2


@pytest.mark.parametrize("already_upgraded", [False, True])
def test_latest_assessment_survives_newer_lookup_of_an_older_known_form(legacy, monkeypatch, already_upgraded):
    path, engine, connection = legacy
    # generalized was marked KNOWN on day 2; generalizing UNKNOWN on day 3.
    # Looking up the older KNOWN form on day 6 changes its row's updated_at.
    connection.execute("UPDATE vocabulary SET updated_at = ? WHERE id = 1", (date(6),))
    connection.execute("INSERT INTO learning_events VALUES (5, 1, 'generalized', 'LOOKUP', 1, '{}', ?)",
                       (date(6),))
    connection.commit()
    all_migrations = migrations.MIGRATIONS[:3]
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations)
    if already_upgraded:
        monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations[:2])
        migrations.migrate_database(engine)
        before = rows(path, "SELECT * FROM vocabulary ORDER BY id")
        assert before[0]["status"] == "KNOWN"  # The previously shipped bug.
        events_before = rows(path, "SELECT * FROM learning_events ORDER BY id")
        monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations)
    backup = migrations.migrate_database(engine)
    assert "before-003_latest_explicit_assessment" in backup.name
    after = rows(path, "SELECT * FROM vocabulary ORDER BY id")
    assert after[0]["lemma"] == "generalize" and after[0]["status"] == "UNKNOWN"
    assert (after[0]["seen_count"], after[0]["lookup_count"], after[0]["mastery_score"]) == (6, 3, .8)
    assert after[2]["user_id"] == 2 and after[2]["status"] == "KNOWN"
    assert after[3]["lemma"] == "unknown" and after[3]["status"] == "UNKNOWN"  # No mark evidence.
    if already_upgraded:
        before[0]["status"] = "UNKNOWN"
        assert after == before  # This repair changes no other columns.
        assert rows(path, "SELECT * FROM learning_events ORDER BY id") == events_before
        assert rows(backup, "SELECT status FROM vocabulary WHERE id = 1")[0]["status"] == "KNOWN"
    assert migrations.migrate_database(engine) is None
    assert rows(path, "SELECT * FROM vocabulary ORDER BY id") == after


def test_latest_assessment_orders_actual_instants_then_event_ids(legacy):
    path, engine, connection = legacy
    connection.executemany("INSERT INTO learning_events VALUES (?, ?, ?, ?, ?, ?, ?)", [
        (5, 1, "generalizes", "MARK_KNOWN", 1, '{}', "2024-01-07T10:00:00+08:00"),
        (6, 1, "generalizing", "MARK_UNKNOWN", 1, '{}', "2024-01-07T03:00:00Z"),
        (7, 1, "generalized", "MARK_UNCERTAIN", 1, '{}', "2024-01-07T03:00:00+00:00"),
        (8, 2, "generalizes", "MARK_UNKNOWN", None, '{}', "2024-01-08T00:00:00Z"),
    ])
    connection.commit()
    migrations.migrate_database(engine)
    assert rows(path, "SELECT status FROM vocabulary WHERE user_id = 1 AND lemma = 'generalize'")[0]["status"] == "UNCERTAIN"
    assert rows(path, "SELECT status FROM vocabulary WHERE user_id = 2 AND lemma = 'generalize'")[0]["status"] == "UNKNOWN"


def test_feedback_upgrade_from_004_preserves_data_and_has_matching_orm_schema(legacy, monkeypatch, tmp_path):
    from backend.models import Base

    path, engine, connection = legacy
    all_migrations = migrations.MIGRATIONS[:5]
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations[:4])
    migrations.migrate_database(engine)
    before_vocabulary = rows(path, "SELECT * FROM vocabulary ORDER BY id")
    before_events = rows(path, "SELECT * FROM learning_events ORDER BY id")
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations)
    backup = migrations.migrate_database(engine)
    assert backup and "before-005_recommendation_feedback" in backup.name
    assert rows(backup, "SELECT name FROM sqlite_master WHERE name = 'recommendation_feedback'") == []
    assert rows(backup, "SELECT * FROM learning_events ORDER BY id") == before_events
    assert rows(path, "SELECT * FROM vocabulary ORDER BY id") == before_vocabulary
    assert rows(path, "SELECT * FROM learning_events ORDER BY id") == before_events

    # SQL migration and fresh ORM setup must agree on columns, FK targets and
    # indexes; otherwise an installed database can behave differently to tests.
    fresh = tmp_path / "orm.sqlite3"
    fresh_engine = create_engine(f"sqlite:///{fresh}")
    Base.metadata.create_all(fresh_engine)
    fresh_engine.dispose()
    for table in ("recommendation_feedback", "feedback_receipts"):
        assert rows(path, f"PRAGMA table_info({table})") == rows(fresh, f"PRAGMA table_info({table})")
        def foreign_keys(database):
            return sorted((row["table"], row["from"], row["to"]) for row in rows(database, f"PRAGMA foreign_key_list({table})"))
        assert foreign_keys(path) == foreign_keys(fresh)
        def indexes(database):
            return sorted((row["name"], row["unique"], row["origin"], row["partial"])
                          for row in rows(database, f"PRAGMA index_list({table})"))
        assert indexes(path) == indexes(fresh)

    connection.execute(
        "INSERT INTO recommendation_feedback VALUES (1,1,4,'overfitting',1,1,'UNKNOWN',0,1,?,?)",
        (date(7), date(8)),
    )
    connection.execute("INSERT INTO feedback_receipts VALUES (1,1,'request-id',4,'overfitting','UNKNOWN',?)", (date(8),))
    connection.commit()
    feedback = rows(path, "SELECT * FROM recommendation_feedback")
    receipts = rows(path, "SELECT * FROM feedback_receipts")
    assert migrations.migrate_database(engine) is None
    assert rows(path, "SELECT * FROM recommendation_feedback") == feedback
    assert rows(path, "SELECT * FROM feedback_receipts") == receipts
    assert len(list((path.parent / "backups").glob("*.sqlite3"))) == 2
    assert rows(path, "PRAGMA foreign_key_check") == []
    assert rows(path, "PRAGMA integrity_check")[0]["integrity_check"] == "ok"

    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
        connection.execute("INSERT INTO recommendation_feedback VALUES (2,1,4,'overfitting',0,0,NULL,NULL,0,?,?)", (date(8), date(8)))
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
        connection.execute("INSERT INTO feedback_receipts VALUES (2,1,'request-id',4,'overfitting','KNOWN',?)", (date(8),))
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        connection.execute("INSERT INTO recommendation_feedback VALUES (2,1,4,'variance',0,0,'UNCERTAIN',NULL,0,?,?)", (date(8), date(8)))
    for user_id, batch_id in ((99, 4), (1, 999)):
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            connection.execute("INSERT INTO recommendation_feedback VALUES (2,?,?,'variance',0,0,NULL,NULL,0,?,?)", (user_id, batch_id, date(8), date(8)))
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            connection.execute("INSERT INTO feedback_receipts VALUES (2,?,'other-request',?,'variance','VIEW',?)", (user_id, batch_id, date(8)))
    connection.rollback()


def test_feedback_upgrade_failure_rolls_back_both_tables_and_keeps_backup(legacy, monkeypatch):
    path, engine, _ = legacy
    all_migrations = migrations.MIGRATIONS[:5]
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations[:4])
    migrations.migrate_database(engine)
    before_vocabulary = rows(path, "SELECT * FROM vocabulary ORDER BY id")
    before_events = rows(path, "SELECT * FROM learning_events ORDER BY id")

    def fail_after_creation(connection, tables):
        migrations._create_feedback_tracking(connection, tables)
        connection.execute("DELETE FROM vocabulary WHERE id = 1")
        raise RuntimeError("feedback migration failed")

    monkeypatch.setattr(migrations, "MIGRATIONS", [*all_migrations[:4], (all_migrations[4][0], fail_after_creation)])
    with pytest.raises(RuntimeError, match="feedback migration failed"):
        migrations.migrate_database(engine)
    assert rows(path, "SELECT * FROM vocabulary ORDER BY id") == before_vocabulary
    assert rows(path, "SELECT * FROM learning_events ORDER BY id") == before_events
    assert rows(path, "SELECT name FROM sqlite_master WHERE name IN ('feedback_receipts','recommendation_feedback')") == []
    assert len(rows(path, "SELECT * FROM schema_migrations")) == 4
    backups = list((path.parent / "backups").glob("*before-005_recommendation_feedback*.sqlite3"))
    assert len(backups) == 1
    assert rows(backups[0], "SELECT * FROM vocabulary ORDER BY id") == before_vocabulary
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations)
    assert migrations.migrate_database(engine) is not None
    assert len(rows(path, "SELECT * FROM schema_migrations")) == 5


def insert_relation_event(connection, event_id, event_type="VIEW_RELATED", *, user_id=1,
                          details=None, created_at=None):
    if details is None:
        details = {"source": "recommendation_feedback", "source_lemma": "generalized",
                   "target_lemma": "models", "relation": "concept"}
    connection.execute(
        "INSERT INTO learning_events (id,user_id,word,lemma,event_type,document_id,details,created_at) "
        "VALUES (?,?,?,?,?,NULL,?,?)",
        (event_id, user_id, "models", "model", event_type,
         details if isinstance(details, str) else json.dumps(details), created_at or date(7)),
    )


def test_relations_upgrade_backfills_only_explicit_valid_feedback_without_changing_evidence(legacy, monkeypatch):
    path, engine, connection = legacy
    all_migrations = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations[:5])
    migrations.migrate_database(engine)
    for event_id, action, day in [(5, "VIEW_RELATED", 5), (6, "MARK_UNKNOWN", 6),
                                   (7, "RECOMMENDATION_USEFUL", 4),
                                   (8, "RECOMMENDATION_NOT_USEFUL", 8), (9, "MARK_KNOWN", 9)]:
        insert_relation_event(connection, event_id, action, created_at=date(day))
    insert_relation_event(connection, 10, "RECOMMENDATION_USEFUL", user_id=2)
    base = {"source": "recommendation_feedback", "source_lemma": "generalizes",
            "target_lemma": "model", "relation": "concept"}
    insert_relation_event(connection, 11, "RECOMMENDATION_NOT_USEFUL", details={**base, "relation": "SYNONYM"})
    insert_relation_event(connection, 12, details={**base, "source_lemma": "models", "target_lemma": "generalized",
                                                  "relation": "domain_related"})
    for event_id, action in [(13, "RECOMMENDATION_SHOWN"), (14, "RECOMMENDATION_GENERATED"), (15, "LOOKUP")]:
        insert_relation_event(connection, event_id, action)
    invalid_details = [{**base, "source": "manual"}, [], "malformed JSON",
                       {**base, "source_lemma": "123"}, {**base, "relation": "invalid"},
                       {**base, "target_lemma": "generalizing"}, {**base, "target_lemma": None},
                       {**base, "source_lemma": 123}, {}]
    for event_id, details in enumerate(invalid_details, 16):
        insert_relation_event(connection, event_id, details=details)
    insert_relation_event(connection, 25, created_at="invalid date")
    connection.execute(
        "INSERT INTO recommendation_feedback VALUES (1,1,4,'model',1,1,'UNKNOWN',1,1,?,?)",
        (date(7), date(8)),
    )
    connection.execute("INSERT INTO feedback_receipts VALUES (1,1,'request-id',4,'model','UNKNOWN',?)", (date(8),))
    connection.commit()
    before_events = rows(path, "SELECT * FROM learning_events ORDER BY id")
    before_vocab = rows(path, "SELECT * FROM vocabulary ORDER BY id")
    unchanged_tables = ("users", "documents", "word_occurrences", "recommendation_feedback", "feedback_receipts")
    before_other = {table: rows(path, f"SELECT * FROM {table} ORDER BY id") for table in unchanged_tables}
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations)
    backup = migrations.migrate_database(engine)
    assert backup and "before-006_personal_vocabulary_relations" in backup.name
    assert rows(backup, "SELECT name FROM sqlite_master WHERE name = 'vocabulary_relations'") == []
    assert rows(backup, "SELECT * FROM learning_events ORDER BY id") == before_events
    relations = rows(path, "SELECT * FROM vocabulary_relations ORDER BY id")
    assert len(relations) == 4
    assert [(row["user_id"], row["source_word"], row["target_word"], row["relation_type"], row["weight"])
            for row in relations] == [
        (1, "generalize", "model", "CONCEPT", .56), (2, "generalize", "model", "CONCEPT", .65),
        (1, "generalize", "model", "SYNONYM", .3), (1, "model", "generalize", "DOMAIN_RELATED", .55),
    ]
    assert (relations[0]["created_at"], relations[0]["updated_at"], relations[0]["last_event_id"]) == (date(5), date(9), 9)
    assert rows(path, "SELECT * FROM learning_events ORDER BY id") == before_events
    assert rows(path, "SELECT * FROM vocabulary ORDER BY id") == before_vocab
    for table, contents in before_other.items():
        assert rows(path, f"SELECT * FROM {table} ORDER BY id") == contents
        assert rows(backup, f"SELECT * FROM {table} ORDER BY id") == contents
    assert migrations.migrate_database(engine) is None
    assert rows(path, "SELECT * FROM vocabulary_relations ORDER BY id") == relations
    assert len(list((path.parent / "backups").glob("*.sqlite3"))) == 2
    assert rows(path, "PRAGMA foreign_key_check") == []
    # Even if the table already existed before its marker, last_event_id prevents
    # a second replay from applying the same feedback a second time.
    connection.execute("DELETE FROM schema_migrations WHERE version = ?", (all_migrations[5][0],))
    connection.commit()
    assert migrations.migrate_database(engine) is not None
    assert rows(path, "SELECT * FROM vocabulary_relations ORDER BY id") == relations


@pytest.mark.parametrize("relation,action,expected", [
    ("word_family", "VIEW_RELATED", .55),
    ("synonym", "MARK_UNKNOWN", .58),
    ("antonym", "MARK_KNOWN", .48),
    ("concept", "RECOMMENDATION_USEFUL", .65),
    ("collocation", "RECOMMENDATION_NOT_USEFUL", .30),
    ("domain_related", "VIEW_RELATED", .55),
])
def test_relation_backfill_accepts_all_relation_types_and_uses_each_frozen_delta(
        legacy, monkeypatch, relation, action, expected):
    path, engine, connection = legacy
    all_migrations = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations[:5])
    migrations.migrate_database(engine)
    insert_relation_event(connection, 5, action, details={
        "source": "recommendation_feedback", "source_lemma": "MEETINGS",
        "target_lemma": "bacterium", "relation": f" {relation} ",
    })
    connection.commit()
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations)
    migrations.migrate_database(engine)
    # Event.word is "models"; relation identity must come from the evidence's
    # canonical endpoints, including dictionary chains and cycles.
    assert rows(path, "SELECT source_word,target_word,relation_type,weight,last_event_id FROM vocabulary_relations") == [{
        "source_word": "meet", "target_word": "bacteria", "relation_type": relation.upper(),
        "weight": expected, "last_event_id": 5,
    }]


def test_relation_backfill_existing_edge_applies_only_events_after_replay_guard(legacy, monkeypatch):
    path, engine, connection = legacy
    all_migrations = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations[:5])
    migrations.migrate_database(engine)
    insert_relation_event(connection, 5, "RECOMMENDATION_USEFUL", created_at=date(5))
    connection.commit()
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations)
    migrations.migrate_database(engine)
    before = rows(path, "SELECT * FROM vocabulary_relations")[0]
    assert before["weight"] == .65

    connection.execute("DELETE FROM schema_migrations WHERE version = ?", (all_migrations[5][0],))
    insert_relation_event(connection, 6, "RECOMMENDATION_NOT_USEFUL", created_at=date(6))
    insert_relation_event(connection, 7, "RECOMMENDATION_SHOWN", created_at=date(7))
    connection.commit()
    backup = migrations.migrate_database(engine)
    after = rows(path, "SELECT * FROM vocabulary_relations")[0]
    assert after == {**before, "weight": .45, "updated_at": date(6), "last_event_id": 6}
    assert rows(backup, "SELECT * FROM vocabulary_relations") == [before]
    assert migrations.migrate_database(engine) is None
    assert rows(path, "SELECT * FROM vocabulary_relations") == [after]


@pytest.mark.parametrize("action,expected", [("RECOMMENDATION_USEFUL", 1.0), ("RECOMMENDATION_NOT_USEFUL", 0.0)])
def test_relation_backfill_weight_bounds(legacy, monkeypatch, action, expected):
    path, engine, connection = legacy
    all_migrations = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations[:5])
    migrations.migrate_database(engine)
    for event_id in range(5, 15):
        insert_relation_event(connection, event_id, action)
    connection.commit()
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations)
    migrations.migrate_database(engine)
    relation = rows(path, "SELECT * FROM vocabulary_relations")[0]
    assert relation["weight"] == expected and relation["last_event_id"] == 14


def test_relation_upgrade_rollback_preserves_events_and_retries_once(legacy, monkeypatch):
    path, engine, connection = legacy
    all_migrations = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations[:5])
    migrations.migrate_database(engine)
    insert_relation_event(connection, 5)
    connection.commit()
    before_events = rows(path, "SELECT * FROM learning_events ORDER BY id")
    before_vocab = rows(path, "SELECT * FROM vocabulary ORDER BY id")

    def fail_after_backfill(connection, tables):
        migrations._create_personal_relations(connection, tables)
        assert connection.execute("SELECT weight FROM vocabulary_relations").fetchone()[0] == .55
        raise RuntimeError("relation backfill failed")

    monkeypatch.setattr(migrations, "MIGRATIONS", [*all_migrations[:5], (all_migrations[5][0], fail_after_backfill)])
    with pytest.raises(RuntimeError, match="relation backfill failed"):
        migrations.migrate_database(engine)
    assert rows(path, "SELECT name FROM sqlite_master WHERE name = 'vocabulary_relations'") == []
    assert len(rows(path, "SELECT * FROM schema_migrations")) == 5
    assert rows(path, "SELECT * FROM learning_events ORDER BY id") == before_events
    assert rows(path, "SELECT * FROM vocabulary ORDER BY id") == before_vocab
    backups = list((path.parent / "backups").glob("*before-006_personal_vocabulary_relations*.sqlite3"))
    assert len(backups) == 1
    assert rows(backups[0], "SELECT * FROM learning_events ORDER BY id") == before_events
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations)
    migrations.migrate_database(engine)
    assert rows(path, "SELECT weight FROM vocabulary_relations") == [{"weight": .55}]
    assert migrations.migrate_database(engine) is None


def test_relation_migration_matches_fresh_schema_and_enforces_constraints(legacy, tmp_path):
    from backend.models import Base

    path, engine, connection = legacy
    migrations.migrate_database(engine)
    fresh = tmp_path / "fresh-relations.sqlite3"
    fresh_engine = create_engine(f"sqlite:///{fresh}")
    assert migrations.migrate_database(fresh_engine) is None
    assert rows(fresh, "SELECT name FROM sqlite_master WHERE name = 'vocabulary_relations'") == []
    Base.metadata.create_all(fresh_engine)
    fresh_engine.dispose()
    assert rows(path, "PRAGMA table_info(vocabulary_relations)") == rows(fresh, "PRAGMA table_info(vocabulary_relations)")
    assert len(rows(path, "PRAGMA table_info(vocabulary_relations)")) == 9
    def foreign_keys(database):
        return sorted((row["table"], row["from"], row["to"]) for row in rows(database, "PRAGMA foreign_key_list(vocabulary_relations)"))
    assert foreign_keys(path) == foreign_keys(fresh)
    def indexes(database):
        return sorted((row["name"], row["unique"]) for row in rows(database, "PRAGMA index_list(vocabulary_relations)"))
    assert indexes(path) == indexes(fresh)
    insert = "INSERT INTO vocabulary_relations VALUES (?,?,'generalize','model',?,?,?, ?,?)"
    connection.execute(insert, (1, 1, "CONCEPT", .55, date(7), date(7), 4))
    for relation, weight in [("INVALID", .5), ("concept", .5), ("CONCEPT", -.1), ("CONCEPT", 1.1)]:
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            connection.execute(insert, (2, 1, relation, weight, date(7), date(7), 4))
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
        connection.execute(insert, (2, 1, "CONCEPT", .6, date(7), date(7), 4))
    for user_id, event_id in [(99, 4), (2, 999)]:
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
            connection.execute(insert, (2, user_id, "CONCEPT", .6, date(7), date(7), event_id))
    connection.rollback()
