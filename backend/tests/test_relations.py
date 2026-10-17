"""Personal graph changes are explicit, retry-safe, scoped, and transactional."""
from uuid import uuid4

import pytest
from sqlalchemy import select

from backend.models import Base, LearningEvent, VocabularyRelation
from backend.repositories.vocabulary import VocabularyRepository
from backend.schemas import FeedbackRequest, Relation
from backend.services.feedback import FeedbackService
from backend.services.relations import RelationService
from backend.tests.test_feedback import batch, feedback, feedback_app


def issued_batch(factory, *, user_id=1, relation="concept", words=("alpha",)):
    """Persist the same immutable item snapshot consumed by feedback requests."""
    items = [{"word": word, "meaning": "test meaning", "reason": "test reason",
              "relation": relation, "priority": .8, "score": .8} for word in words]
    with factory() as session:
        event = LearningEvent(user_id=user_id, word="Generalization", lemma="generalization",
                              event_type="RECOMMENDATION_GENERATED", details={"items": items})
        session.add(event)
        session.commit()
        return {"batch_id": event.id, "items": items}


def graph(client):
    response = client.get("/api/relations")
    assert response.status_code == 200, response.text
    return response.json()


def database_snapshot(factory):
    with factory() as session:
        return {table.name: [dict(row) for row in session.execute(
            select(table).order_by(*table.primary_key.columns)).mappings()]
                for table in Base.metadata.sorted_tables}


def test_generation_display_and_unrelated_vocabulary_marks_do_not_create_edges(feedback_app):
    client, _ = feedback_app
    issued, _, _ = batch(client)
    before = graph(client)
    assert before["items"] == [] and before["total"] == 0
    assert before["metrics"]["generated_items"] == len(issued["items"])
    assert before["metrics"]["shown_items"] == 0
    assert feedback(client, issued, "SHOWN").status_code == 200
    assert feedback(client, issued, "SHOWN").status_code == 200
    assert client.put("/api/vocabulary/alpha", json={"status": "UNKNOWN"}).status_code == 200
    after = graph(client)
    assert after["items"] == [] and after["total"] == 0
    assert after["metrics"]["shown_items"] == 1
    assert after["metrics"]["added_unknown_items"] == 0


@pytest.mark.parametrize("action, event_type, expected_weight", [
    ("VIEWED", "VIEW_RELATED", .55),
    ("UNKNOWN", "MARK_UNKNOWN", .58),
    ("KNOWN", "MARK_KNOWN", .48),
    ("USEFUL", "RECOMMENDATION_USEFUL", .65),
    ("NOT_USEFUL", "RECOMMENDATION_NOT_USEFUL", .30),
])
def test_explicit_feedback_creates_edge_with_rule_weight_and_event_time(
        feedback_app, action, event_type, expected_weight):
    client, factory = feedback_app
    issued = issued_batch(factory)
    request_id = str(uuid4())
    assert feedback(client, issued, action, request_id=request_id).status_code == 200
    result = graph(client)
    assert result["total"] == len(result["items"]) == 1
    edge = result["items"][0]
    assert (edge["source_word"], edge["target_word"], edge["relation_type"]) == (
        "generalization", "alpha", "CONCEPT")
    assert edge["weight"] == pytest.approx(expected_weight)
    with factory() as session:
        row = session.scalar(select(VocabularyRelation))
        event = session.get(LearningEvent, row.last_event_id)
        assert event.event_type == event_type
        assert event.details["batch_id"] == issued["batch_id"]
        assert row.created_at == row.updated_at == event.created_at
    # Both transport retries and a new request for the current choice are no-ops.
    assert feedback(client, issued, action, request_id=request_id).status_code == 200
    assert feedback(client, issued, action).status_code == 200
    assert graph(client) == result


@pytest.mark.parametrize("first, second, expected_weight", [
    ("UNKNOWN", "KNOWN", .56),
    ("USEFUL", "NOT_USEFUL", .45),
])
def test_changed_choice_updates_existing_edge_and_delayed_retry_cannot_replay_it(
        feedback_app, first, second, expected_weight):
    client, factory = feedback_app
    issued = issued_batch(factory)
    first_id = str(uuid4())
    assert feedback(client, issued, first, request_id=first_id).status_code == 200
    original_edge = graph(client)["items"][0]
    latest = feedback(client, issued, second)
    assert latest.status_code == 200
    updated = graph(client)
    edge = updated["items"][0]
    assert updated["total"] == 1 and edge["id"] == original_edge["id"]
    assert edge["created_at"] == original_edge["created_at"]
    assert edge["weight"] == pytest.approx(expected_weight)
    snapshot = database_snapshot(factory)
    delayed = feedback(client, issued, first, request_id=first_id)
    assert delayed.status_code == 200 and delayed.json() == latest.json()
    assert database_snapshot(factory) == snapshot
    assert graph(client) == updated


def test_all_six_relation_types_coexist_for_the_same_pair(feedback_app):
    client, factory = feedback_app
    expected = {"WORD_FAMILY", "SYNONYM", "ANTONYM", "CONCEPT", "COLLOCATION", "DOMAIN_RELATED"}
    assert {relation.value.upper() for relation in Relation} == expected
    for relation in Relation:
        issued = issued_batch(factory, relation=relation.value)
        assert feedback(client, issued, "VIEWED").status_code == 200
    result = graph(client)
    assert result["total"] == len(result["items"]) == 6
    assert {row["relation_type"] for row in result["items"]} == expected
    assert all(row["weight"] == .55 for row in result["items"])
    assert {(row["source_word"], row["target_word"]) for row in result["items"]} == {
        ("generalization", "alpha")}


def test_repeated_feedback_across_batches_clamps_weight_at_both_bounds(feedback_app):
    client, factory = feedback_app
    for action, expected in [("USEFUL", .65), ("USEFUL", .80), ("USEFUL", .95),
                             ("USEFUL", 1.), ("USEFUL", 1.), ("NOT_USEFUL", .80),
                             ("NOT_USEFUL", .60), ("NOT_USEFUL", .40),
                             ("NOT_USEFUL", .20), ("NOT_USEFUL", 0.), ("NOT_USEFUL", 0.)]:
        issued = issued_batch(factory)
        assert feedback(client, issued, action).status_code == 200
        result = graph(client)
        assert result["total"] == 1
        assert result["items"][0]["weight"] == pytest.approx(expected)


def test_graph_and_metrics_are_isolated_by_user_even_for_same_pair_and_uuid(feedback_app):
    client, factory = feedback_app
    own, other = issued_batch(factory), issued_batch(factory, user_id=2)
    request_id = str(uuid4())
    assert feedback(client, own, "USEFUL", request_id=request_id).status_code == 200
    before = graph(client)
    with factory() as session:
        result = FeedbackService(VocabularyRepository(session, user_id=2)).apply(
            other["batch_id"], FeedbackRequest(word="alpha", action="NOT_USEFUL", request_id=request_id))
        assert result.useful is False
    assert graph(client) == before
    assert before["items"][0]["weight"] == .65
    assert before["metrics"]["generated_items"] == 1
    assert before["metrics"]["useful_items"] == 1
    assert before["metrics"]["not_useful_items"] == 0
    assert feedback(client, other, "VIEWED").status_code == 404
    with factory() as session:
        other_graph = RelationService(session, user_id=2).read()
        assert other_graph.total == 1
        assert other_graph.items[0].weight == .3
        assert other_graph.metrics.generated_items == 1
        assert other_graph.metrics.useful_items == 0
        assert other_graph.metrics.not_useful_items == 1


def test_metrics_count_batch_items_and_latest_utility_exclude_legacy_and_get_is_readonly(feedback_app):
    client, factory = feedback_app
    first = issued_batch(factory, words=("alpha", "beta"))
    second = issued_batch(factory, words=("alpha", "beta"))
    for issued, action, word in [
        (first, "VIEWED", "alpha"), (first, "VIEWED", "alpha"),
        (first, "UNKNOWN", "alpha"), (first, "KNOWN", "alpha"),
        (first, "USEFUL", "alpha"), (first, "NOT_USEFUL", "alpha"),
        (first, "NOT_USEFUL", "alpha"), (first, "SHOWN", "beta"),
        (second, "VIEWED", "alpha"), (second, "UNKNOWN", "alpha"),
        (second, "USEFUL", "alpha"),
    ]:
        assert feedback(client, issued, action, word=word).status_code == 200
    with factory() as session:
        # Legacy generated events have no immutable item snapshot. Historical
        # clicks without per-item state must not inflate conversion metrics.
        session.add_all([
            LearningEvent(user_id=1, word="generalization", lemma="generalization",
                          event_type="RECOMMENDATION_GENERATED", details={"recommended_words": ["alpha", "beta"]}),
            LearningEvent(user_id=1, word="alpha", lemma="alpha", event_type="VIEW_RELATED"),
            LearningEvent(user_id=1, word="alpha", lemma="alpha", event_type="RECOMMENDATION_USEFUL"),
        ])
        session.commit()
    before = database_snapshot(factory)
    result = graph(client)
    assert result["metrics"] == {
        "generated_items": 4, "shown_items": 3, "viewed_items": 2,
        "added_unknown_items": 2, "useful_items": 1, "not_useful_items": 1,
    }
    assert result["total"] == 1
    assert graph(client) == result
    assert database_snapshot(factory) == before


@pytest.mark.parametrize("existing_edge", [False, True])
def test_failure_after_flushed_graph_write_rolls_back_graph_feedback_and_vocabulary(
        feedback_app, monkeypatch, existing_edge):
    client, factory = feedback_app
    issued = issued_batch(factory)
    if existing_edge:
        assert feedback(client, issued, "VIEWED").status_code == 200
    before = database_snapshot(factory)
    original = RelationService.apply_event
    writes = []

    def fail_after_graph_flush(self, event):
        row = original(self, event)
        if row is not None:
            self.session.flush()
            writes.append((row.id, row.weight))
            raise RuntimeError("Injected failure after graph, feedback, and vocabulary were flushed")
        return row

    request_id = str(uuid4())
    monkeypatch.setattr(RelationService, "apply_event", fail_after_graph_flush)
    assert feedback(client, issued, "KNOWN", request_id=request_id).status_code == 500
    assert len(writes) == 1
    assert database_snapshot(factory) == before
    monkeypatch.setattr(RelationService, "apply_event", original)
    retry = feedback(client, issued, "KNOWN", request_id=request_id)
    assert retry.status_code == 200
    assert retry.json()["vocabulary"]["mastery_score"] == .65
    result = graph(client)
    assert result["total"] == 1
    assert result["items"][0]["weight"] == pytest.approx(.53 if existing_edge else .48)


def test_relation_read_does_not_autoflush_pending_changes(feedback_app):
    _, factory = feedback_app
    with factory() as session:
        pending = LearningEvent(user_id=1, word="alpha", lemma="alpha", event_type="LOOKUP")
        session.add(pending)
        result = RelationService(session).read()
        assert result.total == 0
        assert pending.id is None
        assert pending in session.new
