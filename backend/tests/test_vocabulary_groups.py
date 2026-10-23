"""SQLite/API checks for personal groups; all data lives under pytest tmp_path."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier

from fastapi import FastAPI, Header, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.orm import sessionmaker

from backend.api.vocabulary_groups import router
from backend.auth import get_current_user_id
from backend.database import get_session
from backend.main import invalid_request
from backend.models import Base, LearningEvent, ReviewSchedule, User, Vocabulary
from backend.models.vocabulary_groups import VocabularyGroup, VocabularyGroupMember
from backend.services.vocabulary_groups import GroupMembersRequest, create_group, update_members


@pytest.fixture
def groups_app(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'groups.sqlite3'}",
                           connect_args={"check_same_thread": False, "timeout": 15})

    @event.listens_for(engine, "connect")
    def configure(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime(2026, 10, 23, tzinfo=timezone.utc)
    with factory() as session:
        session.add_all([User(id=1), User(id=2)])
        session.flush()
        session.add_all([
            Vocabulary(id=1, user_id=1, word="model", lemma="model", status="UNKNOWN", mastery_score=0.2),
            Vocabulary(id=2, user_id=1, word="read", lemma="read", status="KNOWN", mastery_score=0.8),
            Vocabulary(id=3, user_id=2, word="model", lemma="model", status="UNCERTAIN", mastery_score=0.4),
            LearningEvent(user_id=1, word="model", lemma="model", event_type="MARK_UNKNOWN", created_at=now),
        ])
        session.flush()
        session.add(ReviewSchedule(vocabulary_id=1, user_id=1, due_at=now, known_streak=0))
        session.commit()

    def session_override():
        with factory() as session:
            yield session

    def user_override(x_test_user: int = Header(default=1)):
        return x_test_user

    app = FastAPI()
    app.add_exception_handler(RequestValidationError, invalid_request)
    app.include_router(router)
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_current_user_id] = user_override
    try:
        with TestClient(app) as client:
            yield client, factory
    finally:
        engine.dispose()


def learning_snapshot(factory):
    with factory() as session:
        return {table: [dict(row) for row in session.execute(text(f"SELECT * FROM {table} ORDER BY 1")).mappings()]
                for table in ("vocabulary", "learning_events", "review_schedules", "review_receipts",
                              "feedback_receipts", "recommendation_feedback", "vocabulary_relations")}


def create(client, name="Reading", user=1):
    response = client.post("/api/vocabulary-groups", json={"name": name}, headers={"X-Test-User": str(user)})
    assert response.status_code in {200, 201}, response.text
    return response.json()


def members(client, group, ids, action="add", user=1):
    return client.post(f"/api/vocabulary-groups/{group['id']}/members",
                       json={"vocabulary_ids": ids, "action": action}, headers={"X-Test-User": str(user)})


def test_create_normalizes_and_retry_returns_existing_current_members(groups_app):
    client, factory = groups_app
    before = learning_snapshot(factory)
    response = client.post("/api/vocabulary-groups", json={"name": "  Ｒｅａｄｉｎｇ  "})
    assert response.status_code == 201
    group = response.json()
    assert group == {"id": group["id"], "name": "Reading", "vocabulary_ids": []}
    assert members(client, group, [2, 1, 2]).json()["vocabulary_ids"] == [1, 2]
    retry = client.post("/api/vocabulary-groups", json={"name": " reading "})
    assert retry.status_code == 200
    assert retry.json() == {**group, "vocabulary_ids": [1, 2]}
    assert create(client, "Straße")["id"] == create(client, "STRASSE")["id"]
    assert create(client, "Café")["id"] == create(client, "Cafe\u0301")["id"]
    assert learning_snapshot(factory) == before


def test_memberships_are_idempotent_and_deleting_collection_preserves_learning(groups_app):
    client, factory = groups_app
    before = learning_snapshot(factory)
    group, second = create(client), create(client, "Exam")
    for _ in range(2):
        assert members(client, group, [1, 2, 1]).json()["vocabulary_ids"] == [1, 2]
    assert members(client, second, [2]).status_code == 200
    for _ in range(2):
        assert members(client, group, [2, 2], "remove").json()["vocabulary_ids"] == [1]
    assert members(client, group, [2], "remove").status_code == 200  # Owned, already absent.
    assert client.get("/api/vocabulary-groups").json()["groups"] == [
        {**group, "vocabulary_ids": [1]}, {**second, "vocabulary_ids": [2]}]
    response = client.delete(f"/api/vocabulary-groups/{group['id']}")
    assert response.status_code == 204 and not response.content
    assert client.get("/api/vocabulary-groups").json()["groups"] == [{**second, "vocabulary_ids": [2]}]
    assert learning_snapshot(factory) == before
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(VocabularyGroupMember)) == 1


def test_user_scoping_and_invalid_batches_are_atomic_for_add_and_remove(groups_app):
    client, factory = groups_app
    own, other = create(client), create(client, user=2)
    assert own["id"] != other["id"]
    assert members(client, own, [1]).status_code == 200
    assert members(client, other, [3], user=2).status_code == 200
    before = learning_snapshot(factory)
    for ids in ([2, 3], [2, 999], [1, 3]):
        for action in ("add", "remove"):
            response = members(client, own, ids, action)
            assert response.status_code == 404
            assert client.get("/api/vocabulary-groups").json()["groups"] == [{**own, "vocabulary_ids": [1]}]
    for path, verb, payload in (
        (f"/{other['id']}", "put", {"name": "Steal"}),
        (f"/{other['id']}", "delete", None),
        (f"/{other['id']}/members", "post", {"vocabulary_ids": [1], "action": "add"}),
        ("/99999", "delete", None),
    ):
        args = {} if payload is None else {"json": payload}
        assert getattr(client, verb)("/api/vocabulary-groups" + path, **args).status_code == 404
    assert client.get("/api/vocabulary-groups", headers={"X-Test-User": "2"}).json()["groups"] == [
        {**other, "vocabulary_ids": [3]}]
    assert learning_snapshot(factory) == before


def test_rename_conflicts_and_case_only_rename_without_changing_memberships(groups_app):
    client, factory = groups_app
    own, other = create(client), create(client, "Exam")
    members(client, own, [1])
    before = learning_snapshot(factory)
    url = f"/api/vocabulary-groups/{own['id']}"
    assert client.put(url, json={"name": "ｅｘａｍ"}).status_code == 409
    renamed = client.put(url, json={"name": " READING "})
    assert renamed.status_code == 200
    assert renamed.json() == {**own, "name": "READING", "vocabulary_ids": [1]}
    assert create(client, "reading")["id"] == own["id"]
    create(client, "Private", user=2)
    assert client.put(url, json={"name": "Private"}).status_code == 200
    assert client.put(f"/api/vocabulary-groups/{other['id']}", json={"name": "private"}).status_code == 409
    assert learning_snapshot(factory) == before


def test_deleted_group_id_is_not_reused_by_a_later_collection(groups_app):
    client, _ = groups_app
    deleted = create(client)
    assert client.delete(f"/api/vocabulary-groups/{deleted['id']}").status_code == 204
    current = create(client)
    assert current["id"] > deleted["id"]
    assert members(client, deleted, [1]).status_code == 404
    assert client.get("/api/vocabulary-groups").json()["groups"] == [current]


@pytest.mark.parametrize("payload", [
    {}, {"name": None}, {"name": 3}, {"name": True}, {"name": ""}, {"name": "　 "},
    {"name": "x" * 31}, {"name": "line\nbreak"}, {"name": "Reading\n"}, {"name": "bad\tname"},
    {"name": "bad\x7fname"}, {"name": "bad\u0085name"}, {"name": "bad\u2028name"},
    {"name": "bad\u2029name"}, {"name": "bad\u200bname"}, {"name": "bad\ud800name"},
    {"name": "bad\ue000name"}, {"name": "Name", "user_id": 2},
])
def test_invalid_names_rejected_without_mutation(groups_app, payload):
    client, factory = groups_app
    group = create(client)
    before = learning_snapshot(factory)
    body = json.dumps(payload)
    for method, path in (("post", ""), ("put", f"/{group['id']}")):
        response = getattr(client, method)("/api/vocabulary-groups" + path, content=body,
                                           headers={"Content-Type": "application/json"})
        assert response.status_code == 422, response.text
    assert client.get("/api/vocabulary-groups").json()["groups"] == [group]
    assert learning_snapshot(factory) == before


@pytest.mark.parametrize("payload", [
    {}, {"vocabulary_ids": []}, {"vocabulary_ids": [True], "action": "add"},
    {"vocabulary_ids": ["1"], "action": "add"}, {"vocabulary_ids": [1.0], "action": "add"},
    {"vocabulary_ids": [None], "action": "add"}, {"vocabulary_ids": [-1], "action": "add"},
    {"vocabulary_ids": [0], "action": "add"}, {"vocabulary_ids": [2**63], "action": "add"},
    {"vocabulary_ids": [1] * 201, "action": "add"}, {"vocabulary_ids": [1], "action": "set"},
    {"vocabulary_ids": [1], "action": "add", "user_id": 2},
])
def test_invalid_members_rejected_without_partial_mutation(groups_app, payload):
    client, factory = groups_app
    group = create(client)
    before = learning_snapshot(factory)
    response = client.post(f"/api/vocabulary-groups/{group['id']}/members", json=payload)
    assert response.status_code == 422
    assert client.get("/api/vocabulary-groups").json()["groups"] == [group]
    assert learning_snapshot(factory) == before


@pytest.mark.parametrize("group_id", ["0", "-1", "9223372036854775808", "abc"])
def test_invalid_group_path_ids(groups_app, group_id):
    client, _ = groups_app
    assert client.put(f"/api/vocabulary-groups/{group_id}", json={"name": "New"}).status_code == 422
    assert client.delete(f"/api/vocabulary-groups/{group_id}").status_code == 422
    assert client.post(f"/api/vocabulary-groups/{group_id}/members", json={
        "vocabulary_ids": [1], "action": "add"}).status_code == 422


def test_group_limit_is_per_user_and_duplicate_retry_still_allowed_at_limit(groups_app):
    client, factory = groups_app
    before = learning_snapshot(factory)
    for index in range(50):
        create(client, f"Collection {index}")
    response = client.post("/api/vocabulary-groups", json={"name": "Too many"})
    assert response.status_code == 409
    assert client.post("/api/vocabulary-groups", json={"name": "collection 0"}).status_code == 200
    assert create(client, "Other user", user=2)["name"] == "Other user"
    assert learning_snapshot(factory) == before


def test_concurrent_duplicate_create_and_member_add_are_idempotent(groups_app):
    _, factory = groups_app
    barrier = Barrier(2)

    def create_concurrently():
        with factory() as session:
            barrier.wait()
            return create_group(session, 1, "Same")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: create_concurrently(), range(2)))
    assert sorted(created for _, created in results) == [False, True]
    assert results[0][0].id == results[1][0].id
    group_id = results[0][0].id
    barrier = Barrier(2)

    def add_concurrently():
        with factory() as session:
            barrier.wait()
            return update_members(session, group_id, 1, GroupMembersRequest(vocabulary_ids=[1, 2, 1], action="add"))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: add_concurrently(), range(2)))
    assert all(result.vocabulary_ids == [1, 2] for result in results)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(VocabularyGroup)) == 1
        assert session.scalar(select(func.count()).select_from(VocabularyGroupMember)) == 2


def test_concurrent_last_slot_allows_one_group(groups_app):
    _, factory = groups_app
    with factory() as session:
        session.add_all(VocabularyGroup(user_id=1, name=f"Group {index}", name_key=f"group {index}")
                        for index in range(49))
        session.commit()
    barrier = Barrier(2)

    def create_last(index):
        with factory() as session:
            barrier.wait()
            try:
                return create_group(session, 1, f"Last {index}")[0].name
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create_last, range(2)))
    assert sum(result == 409 for result in results) == 1
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(VocabularyGroup)) == 50
