from collections import Counter
from io import BytesIO
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.agents.vocabulary import VocabularyAgent
from backend.config import Settings
from backend.database import get_session
from backend.main import app
from backend.models import Base, User
from backend.ocr.service import OCRResult, OCRUnavailable, OCRWord
from backend.services import documents
from backend.services.llm import Candidate, LLMService
from backend.services.vocabulary import normalize_word


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add(User(id=1))
        session.commit()

    def session_override():
        with factory() as session:
            yield session

    monkeypatch.setattr("backend.main.initialize_database", lambda: None)
    settings = Settings(data_dir=tmp_path, llm_provider="offline", _env_file=None)
    monkeypatch.setattr(documents, "get_settings", lambda: settings)
    monkeypatch.setattr("backend.services.llm.get_settings", lambda: settings)
    app.dependency_overrides[get_session] = session_override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    engine.dispose()


def demo(client):
    result = client.post("/api/documents/demo")
    assert result.status_code == 200, result.text
    return result.json()


def occurrence(doc, word="Generalization"):
    return next(word_info for word_info in doc["words"] if word_info["word"] == word)


def test_import_counts_occurrences_once_and_unassessed_is_not_uncertain(client):
    doc = demo(client)
    expected = Counter(word["lemma"] for word in doc["words"])
    vocab = client.get("/api/vocabulary").json()
    assert {word["lemma"]: word["seen_count"] for word in vocab} == expected
    assert all(word["status"] is None and word["mastery_score"] is None for word in vocab)
    assert expected["generalization"] >= 2
    assert client.get(doc["image_url"]).headers["content-type"] == "image/png"
    assert client.get("/api/documents").json()[0]["word_count"] == sum(expected.values())
    assert demo(client)["id"] == doc["id"]
    client.get(f"/api/documents/{doc['id']}")
    assert {word["lemma"]: word["seen_count"] for word in client.get("/api/vocabulary").json()} == expected
    assert "+00:00" in vocab[0]["created_at"]


def test_marking_updates_all_occurrences_and_history_without_adding_exposure(client):
    doc = demo(client)
    for status in ("UNKNOWN", "KNOWN", "UNCERTAIN"):
        result = client.put("/api/vocabulary/Generalization", json={"status": status, "document_id": doc["id"]})
        assert result.status_code == 200
        assert result.json()["status"] == status
    loaded = client.get(f"/api/documents/{doc['id']}").json()
    assert all(w["status"] == "UNCERTAIN" for w in loaded["words"] if w["lemma"] == "generalization")
    events = client.get("/api/events").json()
    assert [event["event_type"] for event in events] == ["MARK_UNCERTAIN", "MARK_KNOWN", "MARK_UNKNOWN"]
    assert events[0]["details"]["previous_status"] == "KNOWN"
    entry = client.put("/api/vocabulary/unseen%20data", json={"status": "UNKNOWN"}).json()
    assert entry["seen_count"] == 0 and entry["last_seen_at"] is None


def test_lookups_and_recommendations_are_personalized_and_recorded(client):
    doc = demo(client)
    selected = occurrence(doc)
    analysis = client.post("/api/words/analyze", json={"occurrence_id": selected["id"]}).json()
    assert analysis["source"] == "offline"
    assert analysis["meaning_in_context"] == "泛化能力"
    assert analysis["importance"] == .8
    assert analysis["warning"]
    client.put("/api/vocabulary/overfitting", json={"status": "KNOWN"})
    recommendations = client.post("/api/words/recommend", json={"occurrence_id": selected["id"]}).json()
    assert 0 < len(recommendations["items"]) <= 3
    assert all(item["word"] not in {"generalization", "overfitting"} for item in recommendations["items"])
    assert all(item["relation"] == "concept" and 0 <= item["priority"] == item["score"] <= 1
               for item in recommendations["items"])
    events = client.get("/api/events").json()
    assert events[0]["event_type"] == "RECOMMENDATION_GENERATED"
    assert len(events[0]["details"]["recommended_words"]) <= 3
    vocab = client.get("/api/vocabulary").json()
    word = next(item for item in vocab if item["lemma"] == "generalization")
    assert word["lookup_count"] == 1
    assert word["status"] is None


def test_sentence_context_crosses_ocr_lines(client):
    doc = demo(client)
    sentence = occurrence(doc, "empirical")["sentence"]
    assert "new samples from the same distribution." in sentence
    assert "Overfitting" not in sentence


@pytest.mark.parametrize("path,payload,code", [
    ("/api/words/analyze", {"occurrence_id": 99999}, 404),
    ("/api/words/recommend", {"occurrence_id": 0}, 422),
    ("/api/words/recommend", {}, 422),
])
def test_bad_occurrences(client, path, payload, code):
    assert client.post(path, json=payload).status_code == code


def test_invalid_marks_and_missing_document(client):
    assert client.put("/api/vocabulary/model", json={"status": "MAYBE"}).status_code == 422
    assert client.put("/api/vocabulary/model", json={"status": "KNOWN", "document_id": 44}).status_code == 404
    assert client.put("/api/vocabulary/123", json={"status": "KNOWN"}).status_code == 422
    assert client.get("/api/documents/999").status_code == 404
    assert client.get("/api/events?limit=0").status_code == 422


def image_bytes(size=(100, 80), image_format="PNG"):
    result = BytesIO()
    Image.new("RGB", size, "white").save(result, format=image_format)
    return result.getvalue()


def test_upload_validation_rejects_bad_extension_content_and_pixel_budget(client, monkeypatch, tmp_path):
    assert client.post("/api/documents", files={"file": ("a.pdf", b"abc")}).status_code == 415
    assert client.post("/api/documents", files={"file": ("a.png", b"abc")}).status_code == 422
    assert client.post("/api/documents", files={"file": ("a.png", image_bytes(image_format="GIF"))}).status_code == 415
    settings = Settings(data_dir=tmp_path, max_image_pixels=10, _env_file=None)
    monkeypatch.setattr(documents, "get_settings", lambda: settings)
    assert client.post("/api/documents", files={"file": ("a.png", image_bytes())}).status_code == 413
    settings.max_upload_bytes = 20
    assert client.post("/api/documents", files={"file": ("a.png", b"x" * 21)}).status_code == 413


def test_ocr_failure_cleans_up_upload_without_persisting(client, monkeypatch):
    class FailingOCR:
        def recognize(self, path):
            raise OCRUnavailable("Install OCR models")
    monkeypatch.setattr(documents, "get_ocr_service", lambda: FailingOCR())
    response = client.post("/api/documents", files={"file": ("a.png", image_bytes())})
    assert response.status_code == 503
    assert client.get("/api/documents").json() == []
    assert client.get("/api/vocabulary").json() == []
    assert not list(documents.get_settings().storage_dir.rglob("*.png"))


@pytest.mark.parametrize("words", [[], [OCRWord("123", [1, 2, 40, 15], 0, .96)]])
def test_empty_or_unusable_ocr_cleans_up_file_and_database(client, monkeypatch, words):
    class EmptyOCR:
        def recognize(self, path):
            return OCRResult("123" if words else "", 100, 80, words)

    monkeypatch.setattr(documents, "get_ocr_service", lambda: EmptyOCR())
    response = client.post("/api/documents", files={"file": ("empty.png", image_bytes())})
    assert response.status_code == 422
    assert client.get("/api/documents").json() == []
    assert client.get("/api/vocabulary").json() == []
    assert client.get("/api/events").json() == []
    assert not list(documents.get_settings().storage_dir.rglob("*.png"))


def test_exif_rotated_jpeg_is_normalized_before_ocr_and_serving(client, monkeypatch):
    uploaded = BytesIO()
    original = Image.new("RGB", (100, 80), "white")
    exif = Image.Exif()
    exif[274] = 6  # A viewer must rotate this image 90 degrees clockwise.
    original.save(uploaded, format="JPEG", exif=exif)
    inspected = []

    class InspectingOCR:
        def recognize(self, path):
            with Image.open(path) as normalized:
                inspected.append((normalized.format, normalized.size, normalized.getexif().get(274)))
                width, height = normalized.size
            return OCRResult("Model", width, height, [OCRWord("Model", [1, 2, 40, 15], 0, .96)])

    monkeypatch.setattr(documents, "get_ocr_service", lambda: InspectingOCR())
    response = client.post("/api/documents", files={"file": ("rotated.jpg", uploaded.getvalue())})
    assert response.status_code == 201, response.text
    assert inspected == [("PNG", (80, 100), None)]
    doc = response.json()
    assert (doc["width"], doc["height"]) == (80, 100)
    assert doc["words"][0]["bbox"] == [1, 2, 40, 15]
    served = client.get(doc["image_url"])
    assert served.headers["content-type"] == "image/png"
    with Image.open(BytesIO(served.content)) as image:
        assert image.size == (80, 100)
        assert image.getexif().get(274) is None


def test_real_upload_route_with_ocr_contract_and_repeat_import(client, monkeypatch):
    class TestOCR:
        def recognize(self, path):
            with Image.open(path) as image:
                width, height = image.size
            return OCRResult("Model model.", width, height, [
                OCRWord("Model", [1, 2, 40, 15], 0, .96),
                OCRWord("model", [42, 2, 80, 15], 0, .95)])
    monkeypatch.setattr(documents, "get_ocr_service", lambda: TestOCR())
    for route in ("/api/documents", "/api/documents/upload"):
        response = client.post(route, files={"file": ("page.png", image_bytes())})
        assert response.status_code == 201, response.text
        assert response.json()["words"][0]["bbox"] == [1, 2, 40, 15]
    assert client.get("/api/vocabulary").json()[0]["seen_count"] == 4


def candidate(word, semantic=.9, context=.9):
    return Candidate(word=word, meaning="meaning", reason="reason", semantic_relevance=semantic,
                     context_relevance=context, learning_value=.9)


def test_agent_budget_known_duplicate_current_family_and_relevance_filters():
    agent = VocabularyAgent(None, LLMService(Settings(llm_provider="offline", _env_file=None)))
    result = agent.rank([candidate(word) for word in ["Generalization", "generalize", "overfitting", "models",
                         "model", "bias", "variance", "feature", "dataset"]] + [candidate("unrelated", context=.1)],
                        "generalization", "A machine learning model", [
                            {"lemma": "overfitting", "status": "KNOWN"},
                            {"lemma": "bias", "mastery_score": .9}], [])
    words = [item.word for item in result]
    assert len(words) == 3
    assert len(words) == len(set(words))
    assert not set(words) & {"generalization", "generalize", "overfitting", "models", "bias", "unrelated"}


def test_agent_history_and_unknown_feedback_change_ranking():
    agent = VocabularyAgent(None)
    candidates = [candidate("regularization"), candidate("overfitting")]
    initial = agent.rank(candidates, "generalization", "", [], [])
    assert initial[0].word == "overfitting"
    personalized = agent.rank(candidates, "generalization", "", [{"lemma": "regularization", "status": "UNKNOWN"}], [
        {"word": "generalization", "event_type": "VIEW_RELATED", "details": {"recommended_words": ["overfitting"]}}])
    assert personalized[0].word == "regularization"
    assert "不认识" in personalized[0].reason


def test_offline_does_not_invent_non_ml_meaning_or_recommendations():
    service = LLMService(Settings(llm_provider="offline", _env_file=None))
    analysis = service.analyze_word("distribution", "distribution", "The distribution of food begins today.", "")
    assert analysis.meaning_in_context == "暂无可靠的上下文释义"
    assert analysis.importance is None
    assert service.recommend_related_words("distribution", "Food distribution", "", [], []).candidates == []
    assert normalize_word("generalization") == "generalization"
    assert normalize_word("Models") == "model"


@pytest.mark.parametrize("word,expected", [
    ("generalize", "generalize"), ("Generalized", "generalize"),
    ("generalizing", "generalize"), ("generalizes", "generalize"),
    ("generalization", "generalization"), ("overfitting", "overfitting"),
    ("training", "training"), ("learning", "learning"), ("regularization", "regularization"),
    ("distribution", "distribution"), ("Unseen   Data", "unseen data"),
    ("distribution shift", "distribution shift"), ("models", "model"),
    ("hypotheses", "hypothesis"), ("studies", "study"), ("running", "run"),
])
def test_dictionary_normalization_preserves_concepts_and_phrases(word, expected):
    assert normalize_word(word) == expected


def test_inflection_marks_share_one_vocabulary_and_preserve_event_surfaces(client):
    forms = ("Generalized", "generalizing", "generalizes", "generalize")
    identifiers = set()
    for word in forms:
        result = client.put(f"/api/vocabulary/{word}", json={"status": "UNKNOWN"})
        assert result.status_code == 200
        identifiers.add(result.json()["id"])
        assert result.json()["lemma"] == "generalize"
    assert len(identifiers) == 1
    entry = client.get("/api/vocabulary").json()[0]
    assert entry["first_seen_at"] is None and entry["seen_count"] == 0
    events = client.get("/api/events").json()
    assert [event["word"] for event in events] == list(reversed(forms))
    assert {event["lemma"] for event in events} == {"generalize"}
    assert events[0]["details"]["previous_status"] == "UNKNOWN"


def test_first_observation_sets_timestamp_once_and_query_keeps_surface(client, monkeypatch):
    class InflectionOCR:
        def recognize(self, path):
            return OCRResult("Generalized models.", 100, 80, [
                OCRWord("Generalized", [1, 2, 80, 15], 0, .96)])

    monkeypatch.setattr(documents, "get_ocr_service", lambda: InflectionOCR())
    client.put("/api/vocabulary/generalizing", json={"status": "UNKNOWN"})
    first = client.post("/api/documents", files={"file": ("first.png", image_bytes())}).json()
    entry = next(row for row in client.get("/api/vocabulary").json() if row["lemma"] == "generalize")
    assert entry["first_seen_at"] == entry["last_seen_at"] and entry["seen_count"] == 1
    client.post("/api/documents", files={"file": ("second.png", image_bytes())})
    later = next(row for row in client.get("/api/vocabulary").json() if row["lemma"] == "generalize")
    assert later["first_seen_at"] == entry["first_seen_at"] and later["seen_count"] == 2
    client.post("/api/words/analyze", json={"occurrence_id": first["words"][0]["id"]})
    event = client.get("/api/events").json()[0]
    assert event["event_type"] == "LOOKUP" and event["lemma"] == "generalize"
    assert event["word"] == "Generalized"


def test_word_history_normalizes_forms_and_does_not_mutate_learning_data(client):
    client.put("/api/vocabulary/Generalized", json={"status": "UNKNOWN"})
    client.put("/api/vocabulary/generalizing", json={"status": "KNOWN"})
    vocabulary_before = client.get("/api/vocabulary").json()
    events_before = client.get("/api/events").json()
    expected = None
    for word in ("generalize", "Generalized", "generalizing", "generalizes"):
        response = client.get(f"/api/vocabulary/{word}/history")
        assert response.status_code == 200, response.text
        history = response.json()
        assert history == (expected or history)
        expected = history
        assert history["vocabulary"] == vocabulary_before[0]
        assert history["last_mark"]["event_type"] == "MARK_KNOWN"
        assert [event["word"] for event in history["events"]] == ["generalizing", "Generalized"]
        assert history["vocabulary"]["first_seen_at"] is None
        assert history["vocabulary"]["seen_count"] == 0
    assert client.get("/api/vocabulary").json() == vocabulary_before
    assert client.get("/api/events").json() == events_before
    assert client.get("/api/vocabulary/absent/history").status_code == 404
    assert client.get("/api/vocabulary/123/history").status_code == 422
    assert client.get("/api/vocabulary").json() == vocabulary_before


def test_word_history_unassessed_and_last_mark_beyond_event_limit(client):
    doc = demo(client)
    selected = occurrence(doc)
    history = client.get("/api/vocabulary/generalization/history").json()
    assert history["last_mark"] is None and history["events"] == []
    assert history["vocabulary"]["status"] is None
    assert history["vocabulary"]["first_seen_at"] is not None
    client.put("/api/vocabulary/Generalization", json={"status": "UNCERTAIN", "document_id": doc["id"]})
    for _ in range(21):
        assert client.post("/api/words/analyze", json={"occurrence_id": selected["id"]}).status_code == 200
    history = client.get("/api/vocabulary/generalization/history").json()
    assert len(history["events"]) == 20
    assert all(event["event_type"] == "LOOKUP" for event in history["events"])
    assert history["last_mark"]["event_type"] == "MARK_UNCERTAIN"
    assert history["last_mark"]["word"] == "Generalization"
    assert history["vocabulary"]["lookup_count"] == 21
    assert history["vocabulary"]["status"] == "UNCERTAIN"
    assert history["vocabulary"]["mastery_score"] == 0
    assert [event["id"] for event in history["events"]] == sorted(
        [event["id"] for event in history["events"]], reverse=True)


def test_remote_llm_structured_response_and_failure_fallback(monkeypatch):
    settings = Settings(llm_provider="openai_compatible", llm_model="test", llm_api_key="secret-value", _env_file=None)
    service = LLMService(settings)
    real_client = httpx.Client
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"word":"generalization", "lemma":"generalization", "meaning_in_context":"泛化", "simple_explanation":"对新数据的表现", "part_of_speech":"noun", "importance":0.9}'}}]})

    monkeypatch.setattr("backend.services.llm.httpx.Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    result = service.analyze_word("generalization", "generalization", "A machine learning model", "")
    assert result.source == "llm" and result.warning is None
    assert requests[0].headers["authorization"] == "Bearer secret-value"
    assert result.importance == .9
    prompt = json.loads(json.loads(requests[0].content)["messages"][1]["content"])
    assert prompt["current_word"] == "generalization"
    assert prompt["current_sentence"] == "A machine learning model"
    assert prompt["document_context"] == ""

    def invalid_handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "not JSON"}}]})

    monkeypatch.setattr("backend.services.llm.httpx.Client", lambda **kwargs: real_client(transport=httpx.MockTransport(invalid_handler), **kwargs))
    fallback = service.analyze_word("generalization", "generalization", "machine learning", "")
    assert fallback.source == "offline" and fallback.warning
    related = service.recommend_related_words("generalization", "machine learning", "", [], [])
    assert related.source == "offline" and related.warning
    assert "secret-value" not in fallback.model_dump_json()


def test_remote_llm_timeout_falls_back(monkeypatch):
    service = LLMService(Settings(llm_provider="openai_compatible", llm_model="test", llm_api_key="key", _env_file=None))
    def timeout(*args):
        raise httpx.ReadTimeout("Timed out")
    monkeypatch.setattr(service, "_complete", timeout)
    result = service.analyze_word("generalization", "generalization", "machine learning", "")
    assert result.source == "offline" and result.warning


def test_remote_recommendation_sends_context_and_personal_history(monkeypatch):
    service = LLMService(Settings(llm_provider="openai_compatible", llm_model="test-model",
                                 llm_base_url="https://provider.example/v1/",
                                 llm_api_key="secret-value", _env_file=None))
    real_client = httpx.Client
    requests = []
    returned = candidate("regularization").model_dump()

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {
            "content": json.dumps({"candidates": [returned]})}}]})

    monkeypatch.setattr("backend.services.llm.httpx.Client",
                        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    vocabulary = [{"lemma": "overfitting", "status": "KNOWN", "mastery_score": None},
                  {"lemma": "regularization", "status": "UNKNOWN", "mastery_score": None},
                  {"lemma": "bias", "status": "UNCERTAIN", "mastery_score": .4},
                  {"lemma": "model", "status": None, "mastery_score": None}]
    history = [{"word": "overfitting", "event_type": "MARK_KNOWN", "details": {}},
               {"word": "generalization", "event_type": "VIEW_RELATED",
                "details": {"recommended_words": ["overfitting"]}}]
    sentence = "Generalization matters for a machine learning model."
    article = sentence + " Regularization may improve performance."
    result = service.recommend_related_words("generalization", sentence, article, vocabulary, history)

    assert result.source == "llm" and result.warning is None
    assert [item.model_dump() for item in result.candidates] == [returned]
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://provider.example/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer secret-value"
    payload = json.loads(request.content)
    assert payload["model"] == "test-model"
    assert payload["response_format"] == {"type": "json_object"}
    assert [message["role"] for message in payload["messages"]] == ["system", "user"]
    assert json.loads(payload["messages"][1]["content"]) == {
        "current_word": "generalization", "lemma": "generalization",
        "current_sentence": sentence, "document_context": article,
        "known_words": ["overfitting"], "unknown_words": ["regularization"],
        "uncertain_words": ["bias"],
        "current_word_history": {"vocabulary": None, "last_mark": None, "events": [history[1]]},
        "recent_learning_events": history}
    assert "secret-value" not in request.content.decode()


@pytest.mark.parametrize("changes,count", [
    ({"semantic_relevance": 1.01}, 1),
    ({"context_relevance": -.01}, 1),
    ({"learning_value": "not-a-score"}, 1),
    ({"learning_value": float("inf")}, 1),
    ({"relation": "invented_relation"}, 1),
    ({}, 13),
])
def test_remote_recommendation_rejects_invalid_scores_and_oversized_batches(monkeypatch, changes, count):
    service = LLMService(Settings(llm_provider="openai_compatible", llm_model="test",
                                 llm_api_key="secret-value", _env_file=None))
    real_client = httpx.Client
    invalid = candidate("invalid-candidate").model_dump() | changes

    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {
            "content": json.dumps({"candidates": [invalid] * count})}}]})

    monkeypatch.setattr("backend.services.llm.httpx.Client",
                        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    result = service.recommend_related_words("generalization", "machine learning", "", [], [])
    assert result.source == "offline" and result.warning
    assert result.candidates
    assert all(item.word != "invalid-candidate" for item in result.candidates)
    assert "secret-value" not in result.warning


def test_both_agent_calls_receive_current_word_history_outside_recent_global_window(client, monkeypatch):
    doc = demo(client)
    selected = occurrence(doc)
    client.put("/api/vocabulary/Generalization", json={"status": "UNCERTAIN"})
    for _ in range(21):
        client.post("/api/words/analyze", json={"occurrence_id": selected["id"]})
    for _ in range(101):
        client.put("/api/vocabulary/overfitting", json={"status": "KNOWN"})
    client.put("/api/vocabulary/regularization", json={"status": "UNKNOWN"})
    recent = client.get("/api/events?limit=100").json()
    assert not any(event["lemma"] == selected["lemma"] for event in recent)
    inputs = []

    def complete(self, instruction, data, schema):
        inputs.append(data)
        if "meaning_in_context" in schema.model_fields:
            return schema.model_validate({"word": data["current_word"], "lemma": data["lemma"],
                "meaning_in_context": "泛化能力", "simple_explanation": "在新数据上的表现", 
                "part_of_speech": "noun", "importance": .9})
        return schema.model_validate({"candidates": [candidate("regularization").model_dump()]})

    monkeypatch.setattr("backend.services.llm.get_settings", lambda: Settings(
        llm_provider="openai_compatible", llm_model="test", llm_api_key="test", _env_file=None))
    monkeypatch.setattr(LLMService, "_complete", complete)
    for endpoint in ("analyze", "recommend"):
        response = client.post(f"/api/words/{endpoint}", json={"occurrence_id": selected["id"]})
        assert response.status_code == 200, response.text
        assert response.json()["source"] == "llm"
    assert len(inputs) == 2
    for data in inputs:
        assert data["current_word"] == "Generalization"
        assert data["lemma"] == "generalization"
        assert data["current_sentence"] == selected["sentence"]
        assert data["document_context"] == doc["ocr_text"]
        assert data["known_words"] == ["overfitting"]
        assert data["unknown_words"] == ["regularization"]
        assert data["uncertain_words"] == ["generalization"]
        assert len(data["recent_learning_events"]) == 100
        history = data["current_word_history"]
        assert history["last_mark"]["event_type"] == "MARK_UNCERTAIN"
        assert history["last_mark"]["word"] == "Generalization"
        assert history["vocabulary"]["status"] == "UNCERTAIN"
        assert history["vocabulary"]["mastery_score"] == 0
        assert len(history["events"]) == 20
        assert all(event["event_type"] == "LOOKUP" for event in history["events"])


@pytest.mark.parametrize("importance", [-.01, 1.01, float("inf"), float("nan"), "very important"])
def test_remote_explanation_rejects_invalid_importance_and_falls_back(monkeypatch, importance):
    service = LLMService(Settings(llm_provider="openai_compatible", llm_model="test",
                                 llm_api_key="secret-value", _env_file=None))
    real_client = httpx.Client
    invalid = {"word": "generalization", "lemma": "generalization", "meaning_in_context": "模型结果",
               "simple_explanation": "model output", "part_of_speech": "noun", "importance": importance}

    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(invalid)}}]})

    monkeypatch.setattr("backend.services.llm.httpx.Client",
                        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    result = service.analyze_word("generalization", "generalization", "machine learning", "")
    assert result.source == "offline" and result.warning
    assert result.importance == .8
    assert result.meaning_in_context == "泛化能力"


def test_mastery_changes_priority_and_lookup_uses_normalized_lemma():
    agent = VocabularyAgent(None)
    candidates = [candidate("overfitting"), candidate("regularization")]
    initial = agent.rank(candidates, "generalization", "", [], [])
    assert initial[0].word == "overfitting"
    ranked = agent.rank(candidates, "generalization", "", [
        {"lemma": "overfitting", "status": "UNKNOWN", "mastery_score": .8},
        {"lemma": "regularization", "status": "UNKNOWN", "mastery_score": .2}], [])
    assert ranked[0].word == "regularization"
    assert ranked[0].priority > ranked[1].priority
    assert all(item.priority == item.score and item.relation == "concept" for item in ranked)
    models = [candidate("model")]
    plain = agent.rank(models, "generalization", "", [], [])[0]
    queried = agent.rank(models, "generalization", "", [], [
        {"word": "Models", "lemma": "model", "event_type": "LOOKUP"}])[0]
    assert queried.priority == pytest.approx(plain.priority + .025)
