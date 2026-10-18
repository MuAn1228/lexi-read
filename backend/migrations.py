"""Small, versioned SQLite upgrades that preserve the installed MVP database.

Backups use SQLite's online backup API (including committed WAL data), not a
filesystem copy. Schema additions and canonical-lemma merging share a transaction.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

from sqlalchemy.engine import Engine

from backend.services.lemmatization import normalize_word

VERSION = "001_vocabulary_identity_and_events"


def _canonical(word: str) -> str:
    try:
        return normalize_word(word)
    except ValueError:
        # Do not delete unexpected legacy data just because validation improved.
        return word.strip().lower()


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)


def _first(values):
    present = [value for value in values if value]
    return min(present, key=_time) if present else None


def _last(values):
    present = [value for value in values if value]
    return max(present, key=_time) if present else None


def _backup(connection: sqlite3.Connection, version: str) -> Path | None:
    database = next((row[2] for row in connection.execute("PRAGMA database_list") if row[1] == "main"), "")
    if not database:  # An in-memory test database has no user file to back up.
        return None
    source = Path(database)
    folder = source.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = folder / f"{source.stem}-before-{version}-{stamp}.sqlite3"
    with sqlite3.connect(destination) as snapshot:
        connection.backup(snapshot)
        if snapshot.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("SQLite migration backup failed its integrity check")
    return destination


def _merge_vocabulary(connection: sqlite3.Connection, tables: set[str]) -> None:
    rows = [dict(row) for row in connection.execute("SELECT * FROM vocabulary")]
    groups = defaultdict(list)
    for row in rows:
        groups[(row["user_id"], _canonical(row["lemma"] or row["word"]))].append(row)

    exposures = defaultdict(list)
    if {"documents", "word_occurrences"} <= tables:
        for row in connection.execute(
            "SELECT d.user_id, w.lemma, d.created_at FROM word_occurrences w "
            "JOIN documents d ON d.id = w.document_id"
        ):
            exposures[(row["user_id"], _canonical(row["lemma"]))].append(row["created_at"])

    for (user_id, lemma), entries in groups.items():
        keeper = min(entries, key=lambda row: row["id"])
        newest = max(entries, key=lambda row: (_time(row["updated_at"]), row["id"]))
        assessed = [row for row in entries if row["status"] is not None]
        state = max(assessed or entries, key=lambda row: (_time(row["updated_at"]), row["id"]))
        first_seen = _first([row["first_seen_at"] for row in entries] + exposures[(user_id, lemma)])
        if first_seen is None:
            first_seen = _first([row["created_at"] for row in entries if row["seen_count"] > 0])
        # Delete colliding rows before renaming the keeper under the unique index.
        for row in entries:
            if row["id"] != keeper["id"]:
                connection.execute("DELETE FROM vocabulary WHERE id = ?", (row["id"],))
        connection.execute(
            "UPDATE vocabulary SET word = ?, lemma = ?, status = ?, mastery_score = ?, "
            "seen_count = ?, lookup_count = ?, created_at = ?, updated_at = ?, "
            "first_seen_at = ?, last_seen_at = ? WHERE id = ?",
            (newest["word"], lemma, state["status"], state["mastery_score"],
             sum(row["seen_count"] for row in entries), sum(row["lookup_count"] for row in entries),
             _first(row["created_at"] for row in entries), _last(row["updated_at"] for row in entries),
             first_seen, _last(row["last_seen_at"] for row in entries), keeper["id"]),
        )


def _upgrade_vocabulary_identity(connection: sqlite3.Connection, tables: set[str]) -> None:
    if "vocabulary" in tables:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(vocabulary)")}
        if "first_seen_at" not in columns:
            connection.execute("ALTER TABLE vocabulary ADD COLUMN first_seen_at DATETIME")
        _merge_vocabulary(connection, tables)
    if "word_occurrences" in tables:
        for row in connection.execute("SELECT id, lemma FROM word_occurrences").fetchall():
            connection.execute("UPDATE word_occurrences SET lemma = ? WHERE id = ?",
                               (_canonical(row["lemma"]), row["id"]))
    if "learning_events" in tables:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(learning_events)")}
        if "lemma" not in columns:
            connection.execute("ALTER TABLE learning_events ADD COLUMN lemma VARCHAR(200)")
        for row in connection.execute("SELECT * FROM learning_events").fetchall():
            event_type = row["event_type"]
            details = json.loads(row["details"]) if row["details"] else {}
            if event_type == "VIEW_RELATED" and isinstance(details, dict) and "recommended_words" in details:
                details["legacy_event_type"] = event_type
                event_type = "RECOMMENDATION_GENERATED"
            connection.execute(
                "UPDATE learning_events SET lemma = ?, event_type = ?, details = ? WHERE id = ?",
                (_canonical(row["word"]), event_type, json.dumps(details, ensure_ascii=False), row["id"]),
            )
        connection.execute("CREATE INDEX IF NOT EXISTS ix_learning_events_lemma ON learning_events (lemma)")


def _backfill_mastery(connection: sqlite3.Connection, tables: set[str]) -> None:
    """Replay explicit assessments only, preserving already stored scores.

    Some old LOOKUP events came from passive page views. Without an explicit
    intentional flag those events cannot safely be treated as learning signals.
    No events are created, and an entry without eligible events remains unscored.
    """
    if not {"vocabulary", "learning_events"} <= tables:
        return
    # Freeze migration rules here: later service rule changes must not rewrite
    # the meaning of an upgrade that was already shipped.
    deltas = {"MARK_KNOWN": .15, "MARK_UNKNOWN": -.20, "MARK_UNCERTAIN": -.08, "LOOKUP": -.03}
    entries = connection.execute("SELECT id, user_id, lemma FROM vocabulary WHERE mastery_score IS NULL").fetchall()
    for entry in entries:
        score = None
        events = connection.execute(
            "SELECT id, event_type, details, created_at FROM learning_events WHERE user_id = ? AND lemma = ?",
            (entry["user_id"], entry["lemma"]),
        ).fetchall()
        for event in sorted(events, key=lambda row: (_time(row["created_at"]), row["id"])):
            event_type = event["event_type"]
            if event_type not in deltas:
                continue
            details = json.loads(event["details"]) if event["details"] else {}
            if not isinstance(details, dict):
                continue
            if event_type == "LOOKUP" and details.get("intentional") is not True:
                continue
            before = score
            score = round(min(1.0, max(0.0, (.5 if score is None else score) + deltas[event_type])), 6)
            details.update({"mastery_before": before, "mastery_after": score,
                            "rule_version": "heuristic_v1", "mastery_backfilled": True})
            connection.execute("UPDATE learning_events SET details = ? WHERE id = ?",
                               (json.dumps(details, ensure_ascii=False), event["id"]))
        if score is not None:
            connection.execute("UPDATE vocabulary SET mastery_score = ? WHERE id = ?", (score, entry["id"]))


def _restore_latest_assessment(connection: sqlite3.Connection, tables: set[str]) -> None:
    """Restore merged state from assessments, never from a later passive update.

    The first migration chose between legacy rows by updated_at. That field also
    changes when a word is observed or queried, so it cannot date an assessment.
    Leave entries without explicit mark evidence, counters, scores and events as
    they are; a score describes accumulated signals rather than the latest mark.
    """
    if not {"vocabulary", "learning_events"} <= tables:
        return
    latest = {}
    for event in connection.execute(
        "SELECT id, user_id, word, lemma, event_type, created_at FROM learning_events "
        "WHERE event_type IN ('MARK_KNOWN', 'MARK_UNKNOWN', 'MARK_UNCERTAIN')"
    ):
        key = (event["user_id"], _canonical(event["lemma"] or event["word"]))
        order = (_time(event["created_at"]), event["id"])
        if key not in latest or order > latest[key][0]:
            latest[key] = (order, event["event_type"].removeprefix("MARK_"))
    for (user_id, lemma), (_, status) in latest.items():
        connection.execute(
            "UPDATE vocabulary SET status = ? WHERE user_id = ? AND lemma = ?",
            (status, user_id, lemma),
        )


def _stabilize_vocabulary_identity(connection: sqlite3.Connection, tables: set[str]) -> None:
    """Reconcile keys split by the old single-pass dictionary normalization.

    Canonical keys now are fixed points or deterministic cycle representatives.
    A row already occupying a target key therefore belongs to the same merge
    group, so the existing delete-before-rename merge cannot swap unique keys.
    Reuse the count/date preservation and then restore explicit assessment order.
    No mastery replay or synthetic learning events are needed for an identity fix.
    """
    _upgrade_vocabulary_identity(connection, tables)
    _restore_latest_assessment(connection, tables)


def _create_feedback_tracking(connection: sqlite3.Connection, tables: set[str]) -> None:
    """Add durable recommendation state and request receipts in this transaction.

    An empty installation gets these tables from Base.metadata.create_all after
    migrations. Existing installations must add them before ORM reads begin.
    Keep individual execute calls: executescript would commit the enclosing
    migration transaction before running its DDL.
    """
    if "users" not in tables:
        return
    connection.execute(
        "CREATE TABLE IF NOT EXISTS recommendation_feedback ("
        "id INTEGER NOT NULL PRIMARY KEY, "
        "user_id INTEGER NOT NULL REFERENCES users(id), "
        "batch_id INTEGER NOT NULL REFERENCES learning_events(id), "
        "word VARCHAR(200) NOT NULL, shown BOOLEAN NOT NULL, viewed BOOLEAN NOT NULL, "
        "status VARCHAR(20), useful BOOLEAN, added_unknown BOOLEAN NOT NULL, "
        "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, "
        "UNIQUE (user_id, batch_id, word), "
        "CHECK (status IS NULL OR status IN ('KNOWN','UNKNOWN')))"
    )
    connection.execute("CREATE INDEX IF NOT EXISTS ix_recommendation_feedback_user_id "
                       "ON recommendation_feedback (user_id)")
    connection.execute("CREATE INDEX IF NOT EXISTS ix_recommendation_feedback_batch_id "
                       "ON recommendation_feedback (batch_id)")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS feedback_receipts ("
        "id INTEGER NOT NULL PRIMARY KEY, "
        "user_id INTEGER NOT NULL REFERENCES users(id), "
        "request_id VARCHAR(36) NOT NULL, "
        "batch_id INTEGER NOT NULL REFERENCES learning_events(id), "
        "word VARCHAR(200) NOT NULL, action VARCHAR(20) NOT NULL, "
        "created_at DATETIME NOT NULL, UNIQUE (user_id, request_id))"
    )
    connection.execute("CREATE INDEX IF NOT EXISTS ix_feedback_receipts_user_id "
                       "ON feedback_receipts (user_id)")


def _create_personal_relations(connection: sqlite3.Connection, tables: set[str]) -> None:
    """Reconstruct personal edges only from recorded, explicit item feedback.

    Keep these rules frozen with this migration. Generation and exposure alone
    do not demonstrate a useful association, and cannot create an edge.
    """
    if "users" not in tables:
        return
    connection.execute(
        "CREATE TABLE IF NOT EXISTS vocabulary_relations ("
        "id INTEGER NOT NULL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), "
        "source_word VARCHAR(200) NOT NULL, target_word VARCHAR(200) NOT NULL, "
        "relation_type VARCHAR(30) NOT NULL, weight FLOAT NOT NULL, "
        "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, "
        "last_event_id INTEGER NOT NULL REFERENCES learning_events(id), "
        "UNIQUE (user_id, source_word, target_word, relation_type), "
        "CHECK (weight >= 0 AND weight <= 1), "
        "CHECK (relation_type IN ('WORD_FAMILY','SYNONYM','ANTONYM','CONCEPT','COLLOCATION','DOMAIN_RELATED')))"
    )
    connection.execute("CREATE INDEX IF NOT EXISTS ix_vocabulary_relations_user_id "
                       "ON vocabulary_relations (user_id)")
    if "learning_events" not in tables:
        return
    deltas = {"VIEW_RELATED": .05, "MARK_UNKNOWN": .08, "MARK_KNOWN": -.02,
              "RECOMMENDATION_USEFUL": .15, "RECOMMENDATION_NOT_USEFUL": -.20}
    relation_types = {"WORD_FAMILY", "SYNONYM", "ANTONYM", "CONCEPT", "COLLOCATION", "DOMAIN_RELATED"}
    for event in connection.execute(
        "SELECT e.* FROM learning_events e JOIN users u ON u.id = e.user_id ORDER BY e.id"
    ).fetchall():
        if event["event_type"] not in deltas:
            continue
        try:
            details = json.loads(event["details"])
        except (TypeError, ValueError):
            continue
        if not isinstance(details, dict) or details.get("source") != "recommendation_feedback":
            continue
        source, target, relation = (details.get(key) for key in ("source_lemma", "target_lemma", "relation"))
        if not all(isinstance(value, str) for value in (source, target, relation)):
            continue
        relation = relation.strip().upper()
        if relation not in relation_types:
            continue
        try:
            source, target = normalize_word(source), normalize_word(target)
            _time(event["created_at"])
        except (ValueError, TypeError, AttributeError):
            continue
        if source == target or max(len(source), len(target)) > 200:
            continue
        key = (event["user_id"], source, target, relation)
        existing = connection.execute(
            "SELECT id, weight, last_event_id FROM vocabulary_relations "
            "WHERE user_id = ? AND source_word = ? AND target_word = ? AND relation_type = ?", key,
        ).fetchone()
        if existing is not None and existing["last_event_id"] >= event["id"]:
            continue
        weight = round(min(1.0, max(0.0, (existing["weight"] if existing else .5)
                                   + deltas[event["event_type"]])), 6)
        if existing is None:
            connection.execute(
                "INSERT INTO vocabulary_relations (user_id, source_word, target_word, relation_type, "
                "weight, created_at, updated_at, last_event_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (*key, weight, event["created_at"], event["created_at"], event["id"]),
            )
        else:
            connection.execute(
                "UPDATE vocabulary_relations SET weight = ?, updated_at = ?, last_event_id = ? WHERE id = ?",
                (weight, event["created_at"], event["id"], existing["id"]),
            )


def _create_review_schedules(connection: sqlite3.Connection, tables: set[str]) -> None:
    """Seed an upgrade-time schedule without inventing assessments or scores."""
    if not {"users", "vocabulary"} <= tables:
        return
    connection.execute(
        "CREATE TABLE IF NOT EXISTS review_schedules ("
        "vocabulary_id INTEGER NOT NULL PRIMARY KEY REFERENCES vocabulary(id), "
        "user_id INTEGER NOT NULL REFERENCES users(id), due_at DATETIME NOT NULL, "
        "known_streak INTEGER NOT NULL, CHECK (known_streak >= 0 AND known_streak <= 5))"
    )
    connection.execute("CREATE INDEX IF NOT EXISTS ix_review_schedules_user_id ON review_schedules (user_id)")
    connection.execute("CREATE INDEX IF NOT EXISTS ix_review_schedules_due_at ON review_schedules (due_at)")
    # No replay of historical review events: all streaks begin at zero. An
    # already present schedule survives a repeated/partially installed upgrade.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    connection.execute(
        "INSERT OR IGNORE INTO review_schedules (vocabulary_id, user_id, due_at, known_streak) "
        "SELECT id, user_id, CASE WHEN status = 'KNOWN' THEN ? ELSE ? END, 0 "
        "FROM vocabulary WHERE status IN ('KNOWN', 'UNKNOWN', 'UNCERTAIN')",
        ((now + timedelta(days=3)).isoformat(" "), now.isoformat(" ")),
    )


def _create_text_imports(connection: sqlite3.Connection, tables: set[str]) -> None:
    """Add exact plain-text positions and durable import receipts, without replay."""
    if "word_occurrences" in tables:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(word_occurrences)")}
        for name in ("text_start", "text_end"):
            if name not in columns:
                connection.execute(f"ALTER TABLE word_occurrences ADD COLUMN {name} INTEGER")
    if {"users", "documents"} <= tables:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS text_import_receipts ("
            "id INTEGER NOT NULL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), "
            "request_id VARCHAR(36) NOT NULL, payload_hash VARCHAR(64) NOT NULL, "
            "document_id INTEGER NOT NULL REFERENCES documents(id), created_at DATETIME NOT NULL, "
            "UNIQUE (user_id, request_id))"
        )
        connection.execute("CREATE INDEX IF NOT EXISTS ix_text_import_receipts_user_id "
                           "ON text_import_receipts (user_id)")


def _create_article_library(connection: sqlite3.Connection, tables: set[str]) -> None:
    """Keep original documents and learning records; deletion only hides material."""
    if "documents" not in tables:
        return
    columns = {row[1] for row in connection.execute("PRAGMA table_info(documents)")}
    for name, definition in (("tags", "JSON NOT NULL DEFAULT '[]'"),
                             ("bookmark_occurrence_id", "INTEGER"), ("deleted_at", "DATETIME")):
        if name not in columns:
            connection.execute(f"ALTER TABLE documents ADD COLUMN {name} {definition}")
    connection.execute("CREATE INDEX IF NOT EXISTS ix_documents_deleted_at ON documents (deleted_at)")


def _create_pdf_reading(connection: sqlite3.Connection, tables: set[str]) -> None:
    """Add page spans only; original materials and learning signals stay intact."""
    if "documents" in tables:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(documents)")}
        if "pages" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN pages JSON NOT NULL DEFAULT '[]'")


# Append a new numbered migration here; do not change previously shipped versions.
def _create_restore_receipts(connection: sqlite3.Connection, tables: set[str]) -> None:
    if "users" in tables:
        connection.execute("CREATE TABLE IF NOT EXISTS restore_receipts ("
                           "token VARCHAR(32) NOT NULL PRIMARY KEY, result JSON NOT NULL, created_at DATETIME NOT NULL)")


MIGRATIONS = [(VERSION, _upgrade_vocabulary_identity), ("002_mastery_heuristic", _backfill_mastery),
              ("003_latest_explicit_assessment", _restore_latest_assessment),
              ("004_stable_vocabulary_identity", _stabilize_vocabulary_identity),
              ("005_recommendation_feedback", _create_feedback_tracking),
              ("006_personal_vocabulary_relations", _create_personal_relations),
              ("007_spaced_review_schedules", _create_review_schedules),
              ("008_plain_text_import", _create_text_imports),
              ("009_article_library", _create_article_library),
              ("010_pdf_reading", _create_pdf_reading),
              ("011_restore_receipts", _create_restore_receipts)]


def migrate_database(engine: Engine) -> Path | None:
    """Run before ORM selects; return the backup path when an upgrade occurred."""
    if engine.dialect.name != "sqlite":
        raise RuntimeError("This application's schema migration currently requires SQLite")
    raw = engine.raw_connection()
    connection = raw.driver_connection
    original_row_factory = connection.row_factory
    backup_path = None
    try:
        connection.row_factory = sqlite3.Row
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")} if "schema_migrations" in tables else set()
        pending = [(version, upgrade) for version, upgrade in MIGRATIONS if version not in applied]
        if not pending:
            return None
        if "vocabulary" in tables or "learning_events" in tables:
            backup_path = _backup(connection, pending[-1][0])
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                           "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
        for version, upgrade in pending:
            upgrade(connection, tables)
            connection.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                               (version, datetime.now(timezone.utc).isoformat()))
        connection.commit()
        return backup_path
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.row_factory = original_row_factory
        raw.close()
