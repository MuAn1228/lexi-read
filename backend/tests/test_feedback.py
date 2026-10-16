"""Recommendation feedback is bounded by issued snapshots and user intent."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from backend.agents.vocabulary import VocabularyAgent
from backend.config import Settings
from backend.database import get_session
from backend.main import app
from backend.models import Base, FeedbackReceipt, LearningEvent, RecommendationFeedback, User
from backend.repositories.vocabulary import VocabularyRepository
from backend.schemas import FeedbackRequest
from backend.services import documents
from backend.services.feedback import FeedbackService
from backend.services.llm import Candidate, GeneratedCandidates, LLMService
from backend.services.vocabulary import VocabularyService


@pytest.fixture
def feedback_app(tmp_path, monkeypatch):
    # A file database exercises real transactions/locks, including two writers.
    engine = create_engine(f"sqlite:///{tmp_path / 'feedback.sqlite3'}",
                           connect_args={"check_same_thread": False, "timeout": 10})

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add_all([User(id=1), User(id=2)])
        session.commit()

    def session_override():
        with factory() as session:
            yield session

    settings = Settings(data_dir=tmp_path, llm_provider="offline", _env_file=None)
    monkeypatch.setattr("backend.main.initialize_database", lambda: None)
    monkeypatch.setattr(documents, "get_settings", lambda: settings)
    monkeypatch.setattr("backend.services.llm.get_settings", lambda: settings)
    app.dependency_overrides[get_session] = session_override
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client, factory
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def batch(client, word="Generalization"):
    doc_response = client.post("/api/documents/demo")
    assert doc_response.status_code == 200, doc_response.text
    doc = doc_response.json()
    occurrence = next(row for row in doc["words"] if row["word"] == word)
    response = client.post("/api/words/recommend", json={"occurrence_id": occurrence["id"]})
    assert response.status_code == 200, response.text
    return response.json(), doc, occurrence


def feedback(client, recommendation, action, word=None, request_id=None):
    return client.post(f"/api/recommendations/{recommendation['batch_id']}/feedback", json={
        "word": word or recommendation["items"][0]["word"],
        "action": action, "request_id": request_id or str(uuid4()),
    })


def events(client):
    return client.get("/api/events?limit=1000").json()


def test_generated_shown_and_viewed_are_distinct_and_snapshot_is_immutable(feedback_app):
    client, factory = feedback_app
    issued, doc, _ = batch(client)
    snapshot = deepcopy(events(client)[0])
    assert snapshot["id"] == issued["batch_id"]
    assert snapshot["event_type"] == "RECOMMENDATION_GENERATED"
    assert snapshot["details"]["items"] == issued["items"]
    assert len(events(client)) == 1
    vocabulary_before = client.get("/api/vocabulary").json()

    shown = feedback(client, issued, "SHOWN")
    assert shown.status_code == 200, shown.text
    assert shown.json()["shown"] and not shown.json()["viewed"]
    viewed = feedback(client, issued, "VIEWED")
    assert viewed.status_code == 200, viewed.text
    assert viewed.json()["viewed"]
    feedback(client, issued, "SHOWN")
    feedback(client, issued, "VIEWED")
    recorded = events(client)
    assert Counter(row["event_type"] for row in recorded) == {
        "RECOMMENDATION_GENERATED": 1, "RECOMMENDATION_SHOWN": 1, "VIEW_RELATED": 1,
    }
    assert next(row for row in recorded if row["id"] == issued["batch_id"]) == snapshot
    for row in recorded[:2]:
        assert row["document_id"] == doc["id"]
        assert row["details"]["source_lemma"] == "generalization"
        assert row["details"]["target_lemma"] == issued["items"][0]["word"]
        assert row["details"]["batch_id"] == issued["batch_id"]
        assert row["details"]["relation"] == issued["items"][0]["relation"]
        assert row["details"]["priority"] == issued["items"][0]["priority"]
    assert client.get("/api/vocabulary").json() == vocabulary_before
    with factory() as session:
        assert len(list(session.scalars(select(RecommendationFeedback)))) == 1
        assert len(list(session.scalars(select(FeedbackReceipt)))) == 4


@pytest.mark.parametrize("action", ["KNOWN", "UNKNOWN"])
def test_recommended_mark_updates_vocabulary_mastery_and_future_filter(feedback_app, action):
    client, factory = feedback_app
    issued, doc, selected = batch(client)
    word = issued["items"][0]["word"]
    response = feedback(client, issued, action)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == result["vocabulary"]["status"] == action
    assert result["vocabulary"]["mastery_score"] == (.65 if action == "KNOWN" else .3)
    mark = events(client)[0]
    assert mark["event_type"] == f"MARK_{action}"
    assert mark["document_id"] == doc["id"]
    assert mark["lemma"] == word
    assert mark["details"]["source"] == "recommendation_feedback"
    assert mark["details"]["mastery_before"] is None
    with factory() as session:
        state = session.scalar(select(RecommendationFeedback))
        assert state.added_unknown == (action == "UNKNOWN")
    if action == "KNOWN":
        next_batch = client.post("/api/words/recommend", json={"occurrence_id": selected["id"]}).json()
        assert word not in {item["word"] for item in next_batch["items"]}


def test_rejects_forged_words_invalid_payloads_and_other_users_batch_without_writes(feedback_app):
    client, factory = feedback_app
    issued, _, _ = batch(client)
    with factory() as session:
        other = LearningEvent(user_id=2, word="model", lemma="model",
                              event_type="RECOMMENDATION_GENERATED",
                              details={"items": issued["items"]})
        legacy = LearningEvent(user_id=1, word="model", lemma="model",
                               event_type="RECOMMENDATION_GENERATED",
                               details={"recommended_words": [issued["items"][0]["word"]]})
        session.add_all([other, legacy])
        session.commit()
        other_id, legacy_id = other.id, legacy.id
    baseline = events(client)
    vocabulary_before = client.get("/api/vocabulary").json()
    assert feedback(client, issued, "KNOWN", word="forgedword").status_code == 422
    assert feedback(client, {**issued, "batch_id": other_id}, "KNOWN").status_code == 404
    assert feedback(client, {**issued, "batch_id": 999999}, "KNOWN").status_code == 404
    assert feedback(client, {**issued, "batch_id": legacy_id}, "KNOWN").status_code == 422
    for action, word, request_id in [("MAYBE", "model", str(uuid4())),
                                      ("KNOWN", "123", str(uuid4())),
                                      ("KNOWN", "model", "bad-uuid")]:
        assert feedback(client, issued, action, word, request_id).status_code == 422
    assert events(client) == baseline
    assert client.get("/api/vocabulary").json() == vocabulary_before
    with factory() as session:
        assert list(session.scalars(select(RecommendationFeedback))) == []
        assert list(session.scalars(select(FeedbackReceipt))) == []


def test_delayed_exact_retry_cannot_override_later_choice_or_repeat_mastery(feedback_app):
    client, _ = feedback_app
    issued, _, _ = batch(client)
    known_id = str(uuid4())
    known = feedback(client, issued, "KNOWN", request_id=known_id)
    assert known.status_code == 200
    unknown = feedback(client, issued, "UNKNOWN")
    assert unknown.status_code == 200
    assert unknown.json()["vocabulary"]["mastery_score"] == .45
    baseline = events(client)
    late_retry = feedback(client, issued, "KNOWN", request_id=known_id)
    assert late_retry.status_code == 200
    assert late_retry.json() == unknown.json()
    assert events(client) == baseline
    # A newly issued request for the already-current choice is also a no-op.
    duplicate_choice = feedback(client, issued, "UNKNOWN")
    assert duplicate_choice.json() == unknown.json()
    assert events(client) == baseline


@pytest.mark.parametrize("changed_field", ["word", "action", "batch_id"])
def test_reusing_request_id_with_different_payload_conflicts(feedback_app, changed_field):
    client, _ = feedback_app
    issued, _, _ = batch(client)
    second, _, _ = batch(client)
    request_id = str(uuid4())
    assert feedback(client, issued, "SHOWN", request_id=request_id).status_code == 200
    changed_batch, action, word = issued, "SHOWN", issued["items"][0]["word"]
    if changed_field == "word":
        word = issued["items"][1]["word"]
    elif changed_field == "action":
        action = "VIEWED"
    else:
        changed_batch = second
    baseline = events(client)
    assert feedback(client, changed_batch, action, word, request_id).status_code == 409
    assert events(client) == baseline


def test_fresh_choice_reapplies_after_external_mark_but_exact_retry_does_not(feedback_app):
    client, _ = feedback_app
    issued, _, _ = batch(client)
    word, first_request = issued["items"][0]["word"], str(uuid4())
    assert feedback(client, issued, "KNOWN", request_id=first_request).status_code == 200
    external = client.put(f"/api/vocabulary/{word}", json={"status": "UNKNOWN"}).json()
    assert external["mastery_score"] == .45
    baseline = events(client)
    retry = feedback(client, issued, "KNOWN", request_id=first_request)
    assert retry.json()["vocabulary"] == external
    assert events(client) == baseline
    renewed = feedback(client, issued, "KNOWN")
    assert renewed.status_code == 200
    assert renewed.json()["vocabulary"]["status"] == "KNOWN"
    assert renewed.json()["vocabulary"]["mastery_score"] == .6
    assert len(events(client)) == len(baseline) + 1


def test_failure_rolls_back_vocabulary_score_events_state_and_receipt(feedback_app, monkeypatch):
    client, factory = feedback_app
    issued, _, _ = batch(client)
    word, request_id = issued["items"][0]["word"], str(uuid4())
    client.put(f"/api/vocabulary/{word}", json={"status": "UNKNOWN"})
    vocabulary_before = client.get("/api/vocabulary").json()
    events_before = events(client)
    original_mark = VocabularyService.mark

    def fail_after_flush(self, *args, **kwargs):
        original_mark(self, *args, **kwargs)
        self.repo.session.flush()
        raise RuntimeError("Injected failure after writing vocabulary and events")

    monkeypatch.setattr(VocabularyService, "mark", fail_after_flush)
    assert feedback(client, issued, "KNOWN", request_id=request_id).status_code == 500
    assert client.get("/api/vocabulary").json() == vocabulary_before
    assert events(client) == events_before
    with factory() as session:
        assert list(session.scalars(select(RecommendationFeedback))) == []
        assert list(session.scalars(select(FeedbackReceipt))) == []
    monkeypatch.setattr(VocabularyService, "mark", original_mark)
    retry = feedback(client, issued, "KNOWN", request_id=request_id)
    assert retry.status_code == 200
    assert retry.json()["vocabulary"]["mastery_score"] == .45


def test_simultaneous_exact_retries_commit_one_mark_and_one_receipt(feedback_app):
    client, factory = feedback_app
    issued, _, _ = batch(client)
    request_id = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as executor:
        requests = [executor.submit(feedback, client, issued, "KNOWN", request_id=request_id)
                    for _ in range(2)]
        responses = [request.result() for request in requests]
    assert all(response.status_code == 200 for response in responses)
    assert responses[0].json() == responses[1].json()
    assert responses[0].json()["vocabulary"]["mastery_score"] == .65
    assert Counter(row["event_type"] for row in events(client)) == {
        "RECOMMENDATION_GENERATED": 1, "RECOMMENDATION_SHOWN": 1, "MARK_KNOWN": 1,
    }
    with factory() as session:
        assert len(list(session.scalars(select(FeedbackReceipt)))) == 1


def test_request_ids_and_feedback_state_are_scoped_to_user(feedback_app):
    client, factory = feedback_app
    issued, _, _ = batch(client)
    request_id = str(uuid4())
    assert feedback(client, issued, "KNOWN", request_id=request_id).status_code == 200
    with factory() as session:
        other_batch = LearningEvent(user_id=2, word="model", lemma="model",
                                    event_type="RECOMMENDATION_GENERATED",
                                    details={"items": issued["items"]})
        session.add(other_batch)
        session.commit()
        other_id = other_batch.id
    with factory() as session:
        result = FeedbackService(VocabularyRepository(session, user_id=2)).apply(other_id,
            FeedbackRequest(word=issued["items"][0]["word"], action="UNKNOWN", request_id=request_id))
        assert result.vocabulary.user_id == 2
        assert result.vocabulary.status == "UNKNOWN"
        assert result.vocabulary.mastery_score == .3
    assert feedback(client, issued, "KNOWN", request_id=request_id).json()["vocabulary"]["status"] == "KNOWN"


def equal_candidate(word):
    return Candidate(word=word, meaning="test meaning", reason="test reason",
                     semantic_relevance=.8, context_relevance=.8, learning_value=.8)


def test_latest_pair_utility_changes_ranking_after_recent_event_window(feedback_app, monkeypatch):
    client, factory = feedback_app
    monkeypatch.setattr(LLMService, "recommend_related_words", lambda *args, **kwargs:
                        GeneratedCandidates([equal_candidate("alpha"), equal_candidate("beta")], "offline"))
    issued, _, occurrence = batch(client)
    assert issued["items"][0]["word"] == "alpha"
    assert feedback(client, issued, "NOT_USEFUL", word="beta").status_code == 200
    useful_id = str(uuid4())
    assert feedback(client, issued, "USEFUL", word="beta", request_id=useful_id).status_code == 200
    with factory() as session:
        session.add_all([LearningEvent(user_id=1, word="unrelated", lemma="unrelated", event_type="LOOKUP")
                         for _ in range(110)])
        # Neither another source nor another user's opposite choice overrides ours.
        session.add_all([
            LearningEvent(user_id=1, word="beta", lemma="beta", event_type="RECOMMENDATION_NOT_USEFUL",
                          details={"source_lemma": "empirical"}),
            LearningEvent(user_id=2, word="beta", lemma="beta", event_type="RECOMMENDATION_NOT_USEFUL",
                          details={"source_lemma": "generalization"}),
        ])
        session.commit()
    next_batch = client.post("/api/words/recommend", json={"occurrence_id": occurrence["id"]}).json()
    assert next_batch["items"][0]["word"] == "beta"
    assert "已提高优先级" in next_batch["items"][0]["reason"]
    assert feedback(client, next_batch, "NOT_USEFUL", word="beta").status_code == 200
    # A stale retry from an older batch remains harmless to the latest pair choice.
    assert feedback(client, issued, "USEFUL", word="beta", request_id=useful_id).status_code == 200
    latest = client.post("/api/words/recommend", json={"occurrence_id": occurrence["id"]}).json()
    assert latest["items"][0]["word"] == "alpha"
    beta = next(item for item in latest["items"] if item["word"] == "beta")
    assert "已降低优先级" in beta["reason"]


def test_actual_item_view_participates_in_repeat_recommendation_penalty():
    agent = VocabularyAgent(None)
    candidates = [equal_candidate("alpha"), equal_candidate("beta")]
    baseline = agent.rank(candidates, "generalization", "", [], [])
    ranked = agent.rank(candidates, "generalization", "", [], [{
        "word": "alpha", "lemma": "alpha", "event_type": "VIEW_RELATED",
        "details": {"source_lemma": "generalization", "target_lemma": "alpha"},
    }])
    assert baseline[0].word == "alpha" and ranked[0].word == "beta"
    assert ranked[1].priority == pytest.approx(baseline[0].priority - .06)
