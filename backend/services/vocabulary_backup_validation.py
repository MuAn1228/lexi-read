"""Validate untrusted collection and undo data before restoring a backup.

Receipts describe historical state. Later learning is allowed to change today's
word/schedule; references must nevertheless remain owned by the receipt's user.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
import re
import sqlite3
from uuid import UUID

from backend.services.vocabulary_groups import MAX_GROUPS, normalize_group_name


_STATES = {"KNOWN", "UNKNOWN", "UNCERTAIN"}
_SNAPSHOT_KEYS = {"id", "lemma", "changed", "mark_event_id", "before", "after"}
_STATE_KEYS = {"status", "mastery_score", "updated_at", "seen_count", "lookup_count",
               "first_seen_at", "last_seen_at", "last_event_id", "schedule"}


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("备份中的词库分组或批量操作记录无效。")


def _integer(value, *, positive: bool = True) -> bool:
    return type(value) is int and (1 if positive else 0) <= value <= 2**63 - 1


def _date(value, *, nullable: bool = False, snapshot: bool = False) -> datetime | None:
    if value is None and nullable:
        return None
    _require(isinstance(value, str) and len(value) <= 64)
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError("备份中的词库操作时间无效。") from None
    try:
        if snapshot:
            # _state always writes an explicit UTC offset. SQLite row timestamps,
            # by contrast, can be naive UTC and are accepted for receipt columns.
            _require(parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)
    except (OverflowError, ValueError):
        raise ValueError("备份中的词库操作时间无效。") from None


def _event_reference(value, events, user_id: int, lemma: str) -> None:
    _require(_integer(value) and value in events)
    event = events[value]
    _require(event["user_id"] == user_id and event["lemma"] == lemma)


def _state(value, events, user_id: int, lemma: str) -> None:
    _require(isinstance(value, dict) and set(value) == _STATE_KEYS)
    _require(value["status"] is None or isinstance(value["status"], str) and value["status"] in _STATES)
    score = value["mastery_score"]
    _require(score is None or type(score) in (int, float) and math.isfinite(score) and 0 <= score <= 1)
    _require(_integer(value["seen_count"], positive=False) and _integer(value["lookup_count"], positive=False))
    _date(value["updated_at"], snapshot=True)
    _date(value["first_seen_at"], nullable=True, snapshot=True)
    _date(value["last_seen_at"], nullable=True, snapshot=True)
    if value["last_event_id"] is not None:
        _event_reference(value["last_event_id"], events, user_id, lemma)
    schedule = value["schedule"]
    if schedule is not None:
        _require(isinstance(schedule, dict) and set(schedule) == {"due_at", "known_streak"})
        _date(schedule["due_at"], snapshot=True)
        _require(type(schedule["known_streak"]) is int and 0 <= schedule["known_streak"] <= 5)


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        _require(key not in value)
        value[key] = item
    return value


def validate_vocabulary_tools(connection: sqlite3.Connection) -> None:
    """Raise ValueError on invalid data; never execute incoming DDL or mutate it."""
    users = {row[0] for row in connection.execute("SELECT id FROM users")}
    words = {row["id"]: row for row in connection.execute("SELECT id,user_id,lemma FROM vocabulary")}
    events = {row["id"]: row for row in connection.execute("SELECT id,user_id,lemma,event_type,details FROM learning_events")}
    groups, names, counts = {}, set(), Counter()
    for group in connection.execute("SELECT * FROM vocabulary_groups"):
        group_id, user_id = group["id"], group["user_id"]
        _require(_integer(group_id) and group_id not in groups and _integer(user_id) and user_id in users)
        name = normalize_group_name(group["name"])
        _require(name == group["name"] and group["name_key"] == name.casefold())
        _require((user_id, group["name_key"]) not in names)
        names.add((user_id, group["name_key"]))
        counts[user_id] += 1
        _require(counts[user_id] <= MAX_GROUPS)
        _date(group["created_at"])
        groups[group_id] = user_id
    memberships = set()
    for member in connection.execute("SELECT * FROM vocabulary_group_members"):
        group_id, word_id = member["group_id"], member["vocabulary_id"]
        _require(_integer(group_id) and _integer(word_id) and group_id in groups and word_id in words)
        _require(groups[group_id] == words[word_id]["user_id"] and (group_id, word_id) not in memberships)
        memberships.add((group_id, word_id))

    operation_ids, request_ids = set(), set()
    for operation in connection.execute("SELECT * FROM vocabulary_operations"):
        operation_id, user_id = operation["id"], operation["user_id"]
        _require(_integer(operation_id) and operation_id not in operation_ids and _integer(user_id) and user_id in users)
        operation_ids.add(operation_id)
        key = operation["request_id"]
        try:
            _require(isinstance(key, str) and str(UUID(key)) == key)
        except (ValueError, TypeError, AttributeError):
            raise ValueError("备份中的词库操作编号无效。") from None
        _require((user_id, key) not in request_ids)
        request_ids.add((user_id, key))
        _require(isinstance(operation["payload_hash"], str)
                 and re.fullmatch(r"[a-f0-9]{64}", operation["payload_hash"]) is not None)
        created_at = _date(operation["created_at"])
        _require(type(operation["undone"]) is int and operation["undone"] in (0, 1))
        undone_at = _date(operation["undone_at"], nullable=True)
        _require((operation["undone"] == 0 and undone_at is None)
                 or (operation["undone"] == 1 and undone_at is not None and undone_at > created_at))
        raw = operation["snapshots"]
        _require(isinstance(raw, str) and len(raw) <= 2 * 1024 * 1024)
        try:
            snapshots = json.loads(raw, object_pairs_hook=_unique_object)
        except (ValueError, TypeError, RecursionError):
            raise ValueError("备份中的批量操作快照无效。") from None
        _require(isinstance(snapshots, list) and 1 <= len(snapshots) <= 200)
        ids, target_statuses, versions = set(), set(), []
        for snapshot in snapshots:
            _require(isinstance(snapshot, dict) and set(snapshot) == _SNAPSHOT_KEYS)
            word_id, lemma = snapshot["id"], snapshot["lemma"]
            _require(_integer(word_id) and word_id not in ids and word_id in words)
            ids.add(word_id)
            _require(words[word_id]["user_id"] == user_id and words[word_id]["lemma"] == lemma)
            _require(isinstance(lemma, str) and 1 <= len(lemma) <= 200 and type(snapshot["changed"]) is bool)
            before, after = snapshot["before"], snapshot["after"]
            _state(before, events, user_id, lemma)
            _state(after, events, user_id, lemma)
            _require(after["status"] in _STATES)
            target_statuses.add(after["status"])
            versions.append((word_id, _date(before["updated_at"], snapshot=True).isoformat()))
            if not snapshot["changed"]:
                _require(before == after and snapshot["mark_event_id"] is None)
                continue
            _require(before["status"] != after["status"])
            _require(after["mastery_score"] is not None)
            _event_reference(snapshot["mark_event_id"], events, user_id, lemma)
            _require(after["last_event_id"] == snapshot["mark_event_id"])
            _require(_date(after["updated_at"], snapshot=True) == created_at
                     and created_at > _date(before["updated_at"], snapshot=True))
            for field in ("seen_count", "lookup_count", "first_seen_at", "last_seen_at"):
                _require(before[field] == after[field])
            schedule = after["schedule"]
            _require(schedule is not None and schedule["known_streak"] == 0)
            try:
                due_at = created_at + timedelta(days=3 if after["status"] == "KNOWN" else 0)
            except OverflowError:
                raise ValueError("备份中的批量操作排期无效。") from None
            _require(_date(schedule["due_at"], snapshot=True) == due_at)
            original = events[snapshot["mark_event_id"]]
            _require(original["event_type"] == ("VOCAB_MARK_REVERTED" if operation["undone"] else f"MARK_{after['status']}"))
            details = json.loads(original["details"])
            _require(isinstance(details, dict) and details.get("origin") == "vocabulary_bulk"
                     and details.get("operation_id") == key)
            if operation["undone"]:
                _require(details.get("original_event_type") == f"MARK_{after['status']}"
                         and details.get("reverted_by_operation") == key)
                _require(_date(details.get("reverted_at"), snapshot=True) == undone_at)
        _require(len(target_statuses) == 1)
        canonical = {"status": next(iter(target_statuses)), "items": sorted(versions)}
        payload_hash = sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        _require(payload_hash == operation["payload_hash"])
