"""Reject malformed or cross-user data before a backup can reach undo code."""
from copy import deepcopy
import json
import sqlite3
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from backend.models import LearningEvent, Vocabulary
from backend.repositories.vocabulary import VocabularyRepository
from backend.schemas import Status
from backend.services.vocabulary import VocabularyService
from backend.services.vocabulary_backup_validation import validate_vocabulary_tools
from backend.services.vocabulary_operations import VocabularyBatchService
from backend.tests.test_vocabulary_groups import create, groups_app, members  # noqa: F401


@pytest.fixture
def tools_db(groups_app):
    client, factory = groups_app
    group = create(client)
    assert members(client, group, [1, 2]).status_code == 200
    with factory() as session:
        entries = session.scalars(select(Vocabulary).where(Vocabulary.user_id == 1).order_by(Vocabulary.id)).all()
        result = VocabularyBatchService(VocabularyRepository(session, 1)).apply(
            uuid4(), Status.KNOWN, [(entry.id, entry.updated_at) for entry in entries])
    raw = factory.kw["bind"].raw_connection()
    connection = raw.driver_connection
    previous_factory = connection.row_factory
    connection.row_factory = sqlite3.Row
    try:
        validate_vocabulary_tools(connection)
        yield connection, factory, result["operation_id"]
    finally:
        connection.rollback()
        connection.row_factory = previous_factory
        raw.close()


def receipt(connection):
    return json.loads(connection.execute("SELECT snapshots FROM vocabulary_operations").fetchone()[0])


def replace_snapshots(connection, snapshots):
    connection.execute("UPDATE vocabulary_operations SET snapshots=?", (json.dumps(snapshots),))
    connection.commit()


def test_valid_history_and_undone_receipts_remain_valid_after_further_learning(tools_db):
    connection, factory, operation_id = tools_db
    with factory() as session:
        VocabularyBatchService(VocabularyRepository(session, 1)).undo(UUID(operation_id))
    validate_vocabulary_tools(connection)
    with factory() as session:
        VocabularyService(VocabularyRepository(session, 1)).mark("model", Status.UNCERTAIN)
    validate_vocabulary_tools(connection)
    # Other-user learning after the receipt is valid too.
    with factory() as session:
        VocabularyService(VocabularyRepository(session, 2)).mark("model", Status.KNOWN)
    validate_vocabulary_tools(connection)


@pytest.mark.parametrize("sql,params", [
    ("UPDATE vocabulary_groups SET name=?", (" Reading ",)),
    ("UPDATE vocabulary_groups SET name=?", ("bad\nname",)),
    ("UPDATE vocabulary_groups SET name=?", ("",)),
    ("UPDATE vocabulary_groups SET name=?", ("x" * 31,)),
    ("UPDATE vocabulary_groups SET name_key=?", ("wrong",)),
    ("UPDATE vocabulary_groups SET created_at=?", ("not a date",)),
    ("UPDATE vocabulary_group_members SET vocabulary_id=3 WHERE vocabulary_id=1", ()),
    ("UPDATE vocabulary_groups SET user_id=2", ()),
])
def test_group_name_and_membership_validation(tools_db, sql, params):
    connection, _, _ = tools_db
    connection.execute(sql, params)
    with pytest.raises(ValueError):
        validate_vocabulary_tools(connection)


def test_over_fifty_groups_rejected_even_if_backup_bypassed_api_limit(tools_db):
    connection, _, _ = tools_db
    connection.executemany("INSERT INTO vocabulary_groups(user_id,name,name_key,created_at) VALUES(1,?,?,?)",
                           [(f"Group {i}", f"group {i}", "2026-10-23 00:00:00") for i in range(50)])
    with pytest.raises(ValueError):
        validate_vocabulary_tools(connection)


@pytest.mark.parametrize("column,value", [
    ("request_id", "bad"), ("request_id", "A" * 36),
    ("payload_hash", "bad"), ("payload_hash", "F" * 64), ("payload_hash", "f" * 64),
    ("user_id", 2), ("created_at", "not a date"), ("undone_at", "2026-10-23 00:00:00"),
    ("undone", 1), ("undone", 3), ("snapshots", "null"), ("snapshots", "{}"),
    ("snapshots", "[]"), ("snapshots", "[null]"), ("snapshots", "not json"),
])
def test_invalid_receipt_fields_rejected(tools_db, column, value):
    connection, _, _ = tools_db
    connection.execute(f"UPDATE vocabulary_operations SET {column}=?", (value,))
    with pytest.raises(ValueError):
        validate_vocabulary_tools(connection)


@pytest.mark.parametrize("field,value", [
    ("id", True), ("id", 3), ("id", 99999), ("lemma", "other"),
    ("changed", 1), ("changed", False), ("mark_event_id", None),
    ("mark_event_id", True), ("mark_event_id", 99999),
])
def test_snapshot_identity_and_changed_flag_validation(tools_db, field, value):
    connection, _, _ = tools_db
    snapshots = receipt(connection)
    snapshots[0][field] = value
    replace_snapshots(connection, snapshots)
    with pytest.raises(ValueError):
        validate_vocabulary_tools(connection)


@pytest.mark.parametrize("state,field,value", [
    ("before", "status", "OTHER"), ("after", "status", None),
    ("before", "mastery_score", float("nan")), ("before", "mastery_score", float("inf")),
    ("after", "mastery_score", -0.1), ("after", "mastery_score", 1.1),
    ("after", "mastery_score", True), ("after", "mastery_score", None),
    ("before", "seen_count", -1), ("before", "lookup_count", True),
    ("after", "seen_count", 100), ("after", "lookup_count", 100),
    ("before", "updated_at", "invalid"), ("before", "updated_at", "2026-10-23T00:00:00"),
    ("before", "first_seen_at", 1), ("after", "last_seen_at", "bad"),
    ("after", "last_event_id", 99999), ("before", "last_event_id", True),
    ("after", "schedule", None), ("after", "schedule", {}),
    ("after", "schedule", {"due_at": "2026-10-23T00:00:00+00:00", "known_streak": 6}),
    ("after", "schedule", {"due_at": "invalid", "known_streak": 0}),
    ("before", "schedule", {"due_at": "2026-10-23T00:00:00+00:00", "known_streak": True}),
])
def test_snapshot_state_and_schedule_validation(tools_db, state, field, value):
    connection, _, _ = tools_db
    snapshots = receipt(connection)
    snapshots[0][state][field] = value
    replace_snapshots(connection, snapshots)
    with pytest.raises(ValueError):
        validate_vocabulary_tools(connection)


@pytest.mark.parametrize("mutation", ["extra", "missing", "duplicate", "too_many", "noop_mutated", "state_extra"])
def test_snapshot_shape_counts_and_noop_invariants(tools_db, mutation):
    connection, _, _ = tools_db
    snapshots = receipt(connection)
    if mutation == "extra":
        snapshots[0]["unexpected"] = True
    elif mutation == "missing":
        del snapshots[0]["changed"]
    elif mutation == "duplicate":
        snapshots.append(deepcopy(snapshots[0]))
    elif mutation == "too_many":
        snapshots = [deepcopy(snapshots[0]) for _ in range(201)]
    elif mutation == "noop_mutated":
        snapshots[1]["before"]["mastery_score"] = 0.1
    else:
        snapshots[0]["before"]["unexpected"] = True
    replace_snapshots(connection, snapshots)
    with pytest.raises(ValueError):
        validate_vocabulary_tools(connection)


def test_event_reference_must_belong_to_receipt_user_and_word(tools_db):
    connection, factory, _ = tools_db
    with factory() as session:
        event = LearningEvent(user_id=2, word="model", lemma="model", event_type="MARK_KNOWN", details={})
        session.add(event)
        session.commit()
        foreign_id = event.id
    snapshots = receipt(connection)
    snapshots[0]["before"]["last_event_id"] = foreign_id
    replace_snapshots(connection, snapshots)
    with pytest.raises(ValueError):
        validate_vocabulary_tools(connection)


def test_changed_mark_cannot_refer_to_another_users_event(tools_db):
    connection, factory, _ = tools_db
    with factory() as session:
        event = LearningEvent(user_id=2, word="model", lemma="model", event_type="MARK_KNOWN", details={})
        session.add(event)
        session.commit()
        foreign_id = event.id
    snapshots = receipt(connection)
    snapshots[0]["mark_event_id"] = snapshots[0]["after"]["last_event_id"] = foreign_id
    replace_snapshots(connection, snapshots)
    with pytest.raises(ValueError):
        validate_vocabulary_tools(connection)


def test_duplicate_json_keys_do_not_silently_shadow_valid_values(tools_db):
    connection, _, _ = tools_db
    raw = connection.execute("SELECT snapshots FROM vocabulary_operations").fetchone()[0]
    raw = raw.replace('"id": 1', '"id": 3, "id": 1', 1)
    connection.execute("UPDATE vocabulary_operations SET snapshots=?", (raw,))
    with pytest.raises(ValueError):
        validate_vocabulary_tools(connection)


def test_undone_time_and_original_event_metadata_are_checked(tools_db):
    connection, factory, operation_id = tools_db
    with factory() as session:
        VocabularyBatchService(VocabularyRepository(session, 1)).undo(UUID(operation_id))
    validate_vocabulary_tools(connection)
    connection.execute("UPDATE vocabulary_operations SET undone_at=created_at")
    with pytest.raises(ValueError):
        validate_vocabulary_tools(connection)


def test_original_mark_event_must_match_receipt(tools_db):
    connection, _, _ = tools_db
    event_id = receipt(connection)[0]["mark_event_id"]
    connection.execute("UPDATE learning_events SET details='{}' WHERE id=?", (event_id,))
    with pytest.raises(ValueError):
        validate_vocabulary_tools(connection)
