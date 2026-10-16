"""Small, versioned SQLite upgrades that preserve the installed MVP database.

Backups use SQLite's online backup API (including committed WAL data), not a
filesystem copy. Schema additions and canonical-lemma merging share a transaction.
"""
from collections import defaultdict
from datetime import datetime, timezone
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


# Append a new numbered migration here; do not change previously shipped versions.
MIGRATIONS = [(VERSION, _upgrade_vocabulary_identity), ("002_mastery_heuristic", _backfill_mastery),
              ("003_latest_explicit_assessment", _restore_latest_assessment)]


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
