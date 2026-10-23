"""Focused word expansion reuses authenticated, immutable recommendation batches."""
from copy import deepcopy
import json
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import sessionmaker

from backend.agents.vocabulary import VocabularyAgent
from backend.auth import token_digest
from backend.config import Settings
from backend.database import get_session
from backend.main import app
from backend.models import Base, GuestCredential, LearningEvent, User, Vocabulary
from backend.schemas import Relation
from backend.services.llm import Candidate, GeneratedCandidates, LLMService


def candidate(word, relation=Relation.WORD_FAMILY, **changes):
    return Candidate(word=word, meaning="相关词的中文释义", reason="解释真实构词关系、具体分类或使用差异。",
                     relation=relation, semantic_relevance=.9, context_relevance=.9,
                     learning_value=.9, **changes)


@pytest.fixture
def expansion_app(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'expansion.sqlite3'}",
                           connect_args={"check_same_thread": False, "timeout": 10})

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add_all([User(id=value) for value in (1, 2, 3)])
        session.flush()
        session.add_all([
            Vocabulary(user_id=1, word="Create", lemma="create", status="UNCERTAIN"),
            Vocabulary(user_id=2, word="Create", lemma="create", status="UNKNOWN"),
            Vocabulary(user_id=3, word="private", lemma="private", status="KNOWN"),
            GuestCredential(token_hash=token_digest("a" * 43), user_id=2),
            GuestCredential(token_hash=token_digest("b" * 43), user_id=3),
        ])
        session.commit()
    settings = Settings(data_dir=tmp_path, llm_provider="offline", _env_file=None)
    for module in ("backend.auth", "backend.api.routes", "backend.public_limits",
                   "backend.services.documents", "backend.services.llm", "backend.services.dictionary"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    monkeypatch.setattr("backend.main.initialize_database", lambda: None)

    def override_session():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client, factory, settings
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def issue(client, relation="word_family", word="create", headers=None):
    return client.post(f"/api/vocabulary/{word}/recommend", json={"relation": relation}, headers=headers)


def mock_candidates(monkeypatch, values):
    captured = []

    def generate(self, word, sentence, article, vocabulary, history, **options):
        captured.append({"word": word, "sentence": sentence, "article": article,
                         "vocabulary": vocabulary, "history": history, **options})
        return GeneratedCandidates(values, "llm")

    monkeypatch.setattr(LLMService, "recommend_related_words", generate)
    return captured


def learning_snapshot(factory):
    # An issued batch is the only permitted learning write during generation.
    tables = ("vocabulary", "review_schedules", "word_occurrences", "recommendation_feedback",
              "feedback_receipts", "vocabulary_relations")
    with factory() as session:
        return {name: [dict(row) for row in session.execute(text(f"SELECT * FROM {name}")).mappings()]
                for name in tables}


@pytest.mark.parametrize("relation,target", [
    ("word_family", "creation"), ("synonym", "make"), ("domain_related", "design"),
    ("antonym", "destroy"), ("collocation", "create opportunities"), ("concept", "inspiration"),
])
def test_all_focuses_support_words_without_an_article_and_record_exact_snapshot(expansion_app, monkeypatch,
                                                                              relation, target):
    client, factory, _ = expansion_app
    calls = mock_candidates(monkeypatch, [candidate(target, Relation(relation))])
    before = learning_snapshot(factory)
    response = issue(client, relation, word="Creates")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["source"] == "llm" and [item["word"] for item in result["items"]] == [target]
    assert result["items"][0]["relation"] == relation
    assert learning_snapshot(factory) == before
    assert calls[0]["word"] == "Create" and calls[0]["sentence"] == calls[0]["article"] == ""
    assert calls[0]["relation"] == Relation(relation)
    assert calls[0]["learner_context"]["current_word_history"]["vocabulary"]["lemma"] == "create"
    recorded = client.get("/api/events").json()
    assert len(recorded) == 1 and recorded[0]["id"] == result["batch_id"]
    assert recorded[0]["document_id"] is None and recorded[0]["lemma"] == "create"
    assert recorded[0]["details"]["items"] == result["items"]
    assert recorded[0]["details"]["requested_relation"] == relation


def test_requested_family_can_return_derivations_and_all_filters_still_apply():
    agent = VocabularyAgent(None, LLMService(Settings(llm_provider="offline", _env_file=None)))
    values = [candidate("generalization"), candidate("generalize"), candidate("GENERALIZES"),
              candidate("generally"), candidate("general"), candidate("overfitting", Relation.CONCEPT),
              candidate("creativity"), candidate("invent")]
    knowledge = [{"lemma": "general", "status": "KNOWN"}, {"lemma": "creativity", "mastery_score": .9}]
    focused = agent.rank(values, "generalization", "", knowledge, [], Relation.WORD_FAMILY)
    assert len(focused) == 3
    assert {row.word for row in focused} == {"generalize", "generally", "invent"}
    assert len({row.word for row in focused}) == len(focused)
    assert all(row.relation == Relation.WORD_FAMILY for row in focused)
    legacy = agent.rank(values, "generalization", "", knowledge, [])
    assert not {"general", "generally", "generalize", "generalization"} & {row.word for row in legacy}


def test_same_lemma_inflections_and_wrong_relation_cannot_be_returned(expansion_app, monkeypatch):
    client, _, _ = expansion_app
    mock_candidates(monkeypatch, [candidate("create"), candidate("creates"), candidate("created"),
                                candidate("make", Relation.SYNONYM), candidate("creation")])
    response = issue(client)
    assert response.status_code == 200
    assert [item["word"] for item in response.json()["items"]] == ["creation"]


def test_focused_explanations_require_chinese_and_no_article_text_is_not_fabricated(expansion_app, monkeypatch):
    client, factory, _ = expansion_app
    with factory() as session:
        session.add(Vocabulary(user_id=1, word="creation", lemma="creation", status="UNKNOWN"))
        session.commit()
    invalid = candidate("creative").model_copy(update={"reason": "English-only explanation"})
    invalid_meaning = candidate("creativity").model_copy(update={"meaning": "English-only meaning"})
    mock_candidates(monkeypatch, [candidate("creation"), invalid, invalid_meaning])
    response = issue(client)
    assert response.status_code == 200
    assert [row["word"] for row in response.json()["items"]] == ["creation"]
    reason = response.json()["items"][0]["reason"]
    assert "不认识" in reason and "本文" not in reason and "当前语境" not in reason


@pytest.mark.parametrize("word,body,code", [
    ("absent", {"relation": "synonym"}, 404),
    ("private", {"relation": "synonym"}, 404),
    ("123", {"relation": "synonym"}, 422),
    ("create", {}, 422), ("create", {"relation": None}, 422),
    ("create", {"relation": "invented"}, 422),
])
def test_missing_foreign_and_invalid_requests_do_not_call_the_model(expansion_app, monkeypatch, word, body, code):
    client, factory, _ = expansion_app
    calls = mock_candidates(monkeypatch, [candidate("creation")])
    before = learning_snapshot(factory)
    response = client.post(f"/api/vocabulary/{word}/recommend", json=body)
    assert response.status_code == code, response.text
    assert calls == [] and learning_snapshot(factory) == before
    assert client.get("/api/events").json() == []


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer forged"}, {"X-User-Id": "2"}])
def test_public_expansion_requires_real_credentials(expansion_app, monkeypatch, headers):
    client, factory, settings = expansion_app
    settings.app_mode = "public"
    calls = mock_candidates(monkeypatch, [candidate("creation")])
    before = learning_snapshot(factory)
    response = issue(client, headers=headers)
    assert response.status_code == 401 and response.headers["www-authenticate"] == "Bearer"
    assert calls == [] and learning_snapshot(factory) == before


def test_public_words_history_and_feedback_are_isolated(expansion_app, monkeypatch):
    client, factory, settings = expansion_app
    settings.app_mode = "public"
    one, two = ({"Authorization": "Bearer " + letter * 43} for letter in ("a", "b"))
    calls = mock_candidates(monkeypatch, [candidate("creation")])
    result = issue(client, headers=one)
    assert result.status_code == 200, result.text
    assert {row["user_id"] for row in calls[0]["vocabulary"]} == {2}
    assert all(row["lemma"] != "private" for row in calls[0]["vocabulary"])
    assert issue(client, word="private", headers=one).status_code == 404
    assert issue(client, word="create", headers=two).status_code == 404
    issued = result.json()
    payload = {"word": "creation", "action": "UNKNOWN", "request_id": str(uuid4())}
    endpoint = f"/api/recommendations/{issued['batch_id']}/feedback"
    assert client.post(endpoint, json=payload, headers=two).status_code == 404
    assert client.post(endpoint, json=payload, headers=one).status_code == 200
    with factory() as session:
        targets = list(session.scalars(select(Vocabulary).where(Vocabulary.lemma == "creation")))
        assert len(targets) == 1 and targets[0].user_id == 2
        assert all(row.user_id == 2 for row in session.scalars(select(LearningEvent)))


def test_only_explicit_unknown_feedback_adds_word_and_retries_are_idempotent(expansion_app, monkeypatch):
    client, factory, _ = expansion_app
    mock_candidates(monkeypatch, [candidate("creation")])
    before = learning_snapshot(factory)
    issued = issue(client).json()
    assert learning_snapshot(factory) == before
    original = deepcopy(client.get("/api/events").json()[0])
    endpoint = f"/api/recommendations/{issued['batch_id']}/feedback"
    payload = {"word": "creation", "action": "VIEWED", "request_id": str(uuid4())}
    assert client.post(endpoint, json=payload).status_code == 200
    snapshot = learning_snapshot(factory)
    assert snapshot["vocabulary"] == before["vocabulary"]
    assert snapshot["review_schedules"] == before["review_schedules"]
    payload.update(action="UNKNOWN", request_id=str(uuid4()))
    response = client.post(endpoint, json=payload)
    assert response.status_code == 200
    marked = response.json()["vocabulary"]
    assert marked["status"] == "UNKNOWN" and marked["seen_count"] == marked["lookup_count"] == 0
    state = learning_snapshot(factory)
    assert len(state["review_schedules"]) == 1
    assert state["vocabulary_relations"][0]["relation_type"] == "WORD_FAMILY"
    assert state["vocabulary_relations"][0]["source_word"] == "create"
    assert client.post(endpoint, json=payload).status_code == 200
    assert learning_snapshot(factory) == state
    assert next(row for row in client.get("/api/events").json() if row["id"] == issued["batch_id"]) == original
    events = client.get("/api/events").json()
    assert sum(row["event_type"] == "MARK_UNKNOWN" for row in events) == 1
    assert all(row["document_id"] is None for row in events)
    payload.update(word="unoffered", request_id=str(uuid4()))
    assert client.post(endpoint, json=payload).status_code == 422


@pytest.mark.parametrize("relation", [Relation.WORD_FAMILY, Relation.SYNONYM, Relation.DOMAIN_RELATED,
                                     Relation.ANTONYM, Relation.COLLOCATION, Relation.CONCEPT])
def test_provider_receives_requested_relation_and_reliable_linguistic_instructions(monkeypatch, relation):
    settings = Settings(llm_provider="openai_compatible", llm_model="fake-model", llm_api_key="test-only-key",
                        llm_base_url="https://provider.example/v1", _env_file=None)
    real_client = httpx.Client
    received = []

    def handler(request):
        received.append(request)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"candidates": [candidate("creation", relation).model_dump()]})}}]})

    monkeypatch.setattr("backend.services.llm.httpx.Client",
                        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    result = LLMService(settings).recommend_related_words("create", "", "", [], [], relation=relation)
    assert result.source == "llm" and result.candidates[0].relation == relation
    assert len(received) == 1 and received[0].headers["authorization"] == "Bearer test-only-key"
    sent = json.loads(received[0].content)
    data = json.loads(sent["messages"][1]["content"])
    instruction = sent["messages"][0]["content"]
    assert data["requested_relation"] == relation.value
    assert data["current_sentence"] == data["document_context"] == ""
    assert "exactly equal to requested_relation" in instruction and "when uncertain" in instruction
    assert "Prefer conceptual connections" not in instruction
    assert "test-only-key" not in received[0].content.decode()
    if relation == Relation.WORD_FAMILY:
        assert "inflections are NOT derivations" in instruction and "without inventing etymology" in instruction
    if relation == Relation.SYNONYM:
        assert "difference in usage" in instruction and "substituted in every sentence" in instruction
    if relation == Relation.DOMAIN_RELATED:
        assert "concrete shared category" in instruction


@pytest.mark.parametrize("relation", [Relation.WORD_FAMILY, Relation.SYNONYM, Relation.DOMAIN_RELATED,
                                     Relation.ANTONYM, Relation.COLLOCATION])
def test_offline_and_provider_failure_never_relabel_concepts_as_requested_relations(monkeypatch, relation):
    offline = LLMService(Settings(llm_provider="offline", _env_file=None))
    result = offline.recommend_related_words("generalization", "machine learning", "", [], [], relation=relation)
    assert result.source == "offline" and result.candidates == [] and result.warning
    remote = LLMService(Settings(llm_provider="openai_compatible", llm_model="fake", llm_api_key="fake", _env_file=None))

    def invalid(*args):
        raise ValueError("do not expose provider internals")

    monkeypatch.setattr(remote, "_complete", invalid)
    result = remote.recommend_related_words("generalization", "machine learning", "", [], [], relation=relation)
    assert result.source == "offline" and result.candidates == [] and result.warning
    assert "provider internals" not in result.warning


def test_occurrence_recommendation_accepts_focus_and_legacy_body(expansion_app, monkeypatch):
    client, factory, _ = expansion_app
    document = client.post("/api/documents/demo").json()
    occurrence = next(row for row in document["words"] if row["word"] == "Generalization")
    calls = mock_candidates(monkeypatch, [candidate("generalize")])
    response = client.post("/api/words/recommend", json={"occurrence_id": occurrence["id"], "relation": "word_family"})
    assert response.status_code == 200 and response.json()["items"][0]["word"] == "generalize"
    assert calls[-1]["relation"] == Relation.WORD_FAMILY and calls[-1]["article"] == document["ocr_text"]
    with factory() as session:
        batch = session.get(LearningEvent, response.json()["batch_id"])
        assert batch.document_id == document["id"]
    response = client.post("/api/words/recommend", json={"occurrence_id": occurrence["id"]})
    assert response.status_code == 200 and response.json()["items"] == []
    assert "relation" not in calls[-1]


def test_slow_generation_rechecks_source_ownership_before_writing_batch(expansion_app, monkeypatch):
    client, factory, _ = expansion_app

    def after_delete(self, *args, **kwargs):
        with factory() as session:
            session.execute(text("DELETE FROM vocabulary WHERE user_id=1 AND lemma='create'"))
            session.commit()
        return GeneratedCandidates([candidate("creation")], "llm")

    monkeypatch.setattr(LLMService, "recommend_related_words", after_delete)
    response = issue(client)
    assert response.status_code == 404
    assert client.get("/api/events").json() == []


def test_slow_generation_filters_words_marked_known_during_provider_call(expansion_app, monkeypatch):
    client, factory, _ = expansion_app
    with factory() as session:
        session.add(Vocabulary(user_id=1, word="creation", lemma="creation", status="UNKNOWN"))
        session.commit()

    def after_mark(self, *args, **kwargs):
        with factory() as session:
            session.execute(text("UPDATE vocabulary SET status='KNOWN' WHERE user_id=1 AND lemma='creation'"))
            session.commit()
        return GeneratedCandidates([candidate("creation")], "llm")

    monkeypatch.setattr(LLMService, "recommend_related_words", after_mark)
    response = issue(client)
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
