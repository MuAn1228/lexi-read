"""Sentence assistance is explicit, scoped, cached, and never a word assessment."""
from concurrent.futures import ThreadPoolExecutor
import json

from fastapi import HTTPException
import httpx
import pytest
from sqlalchemy import select

from backend.models import Document, WordOccurrence
from backend.public_limits import PublicUsage
from backend.services import sentences
from backend.tests.test_article_library import snapshot
from backend.tests.test_text_import import payload, post, text_app  # noqa: F401


@pytest.fixture
def assistance(text_app, monkeypatch):
    client, factory, settings = text_app
    monkeypatch.setattr(sentences, "get_settings", lambda: settings)
    settings.llm_provider = "openai_compatible"
    settings.llm_api_key = "test-key"
    settings.llm_model = "test-model"
    document = post(client, payload("A model learns from data. A reader learns with curiosity."))
    calls = []

    def complete(_self, instruction, data, schema):
        calls.append(data)
        return schema.model_validate({"sentence": data["current_sentence"],
                                      "translation": "模型从数据中学习。", "structure": "主语 + 谓语 + 介词短语。",
                                      "clauses": [{"text": data["current_sentence"], "role": "主句",
                                                   "explanation": "说明学习行为。"}],
                                      "key_points": ["from 引出学习的来源。"]})

    monkeypatch.setattr(sentences.LLMService, "_complete", complete)
    return client, factory, settings, document, calls


def run(factory, occurrence_id, user=1):
    with factory() as session:
        return sentences.analyze_sentence(session, occurrence_id, user)


def test_translation_and_structure_are_cached_across_words_without_learning_writes(assistance):
    _, factory, settings, document, calls = assistance
    before = snapshot(factory)
    first = run(factory, document["words"][0]["id"])
    second = run(factory, document["words"][1]["id"])
    assert first.source == "llm" and not first.cached
    assert first.translation == "模型从数据中学习。" and first.structure and first.clauses and first.key_points
    assert second.cached and second.sentence == first.sentence
    assert len(calls) == 1 and snapshot(factory) == before
    assert len(list((settings.storage_dir / "sentence-cache").glob("*.json"))) == 1


def test_offline_and_unconfigured_models_provide_no_fabricated_translation(assistance):
    _, factory, settings, document, calls = assistance
    before = snapshot(factory)
    for provider, key in (("offline", "test-key"), ("openai_compatible", "")):
        settings.llm_provider, settings.llm_api_key = provider, key
        result = run(factory, document["words"][0]["id"])
        assert result.source == "unavailable" and result.warning and result.translation is None
        assert not result.cached and not result.clauses
    assert not calls and snapshot(factory) == before


@pytest.mark.parametrize("bad", ["wrong-sentence", "invented-clause", "empty-translation", "surrogate", "extra"])
def test_invalid_model_results_are_not_cached_or_counted(assistance, monkeypatch, bad):
    _, factory, settings, document, _ = assistance
    before = snapshot(factory)

    def invalid(_self, instruction, data, schema):
        value = {"sentence": data["current_sentence"], "translation": "译文", "structure": "结构",
                 "clauses": [{"text": data["current_sentence"], "role": "主句", "explanation": "说明"}],
                 "key_points": ["语法"]}
        if bad == "wrong-sentence":
            value["sentence"] = "A completely different sentence."
        elif bad == "invented-clause":
            value["clauses"][0]["text"] = "invented source text"
        elif bad == "empty-translation":
            value["translation"] = " "
        elif bad == "surrogate":
            value["key_points"] = ["bad\ud800text"]
        else:
            value["unknown"] = "field"
        return schema.model_validate(value)

    monkeypatch.setattr(sentences.LLMService, "_complete", invalid)
    result = run(factory, document["words"][0]["id"])
    assert result.source == "unavailable" and result.translation is None and result.warning
    assert not list((settings.storage_dir / "sentence-cache").glob("*.json"))
    assert snapshot(factory) == before


def test_timeout_is_retryable_and_never_saved_as_success(assistance, monkeypatch):
    _, factory, settings, document, _ = assistance
    before = snapshot(factory)
    calls = []

    def timeout(*args, **kwargs):
        calls.append(1)
        raise httpx.ReadTimeout("private provider exception")

    monkeypatch.setattr(sentences.LLMService, "_complete", timeout)
    for _ in range(2):
        result = run(factory, document["words"][0]["id"])
        assert result.source == "unavailable" and "private" not in result.warning
    assert len(calls) == 2 and snapshot(factory) == before
    assert not list((settings.storage_dir / "sentence-cache").glob("*.json"))


def test_document_and_model_identity_invalidate_cached_sentence(assistance):
    _, factory, settings, document, calls = assistance
    occurrence = document["words"][0]["id"]
    run(factory, occurrence)
    settings.llm_model = "another-model"
    assert not run(factory, occurrence).cached
    with factory() as session:
        doc = session.get(Document, document["id"])
        doc.ocr_text += " Extra restored content."
        session.commit()
    assert not run(factory, occurrence).cached
    assert len(calls) == 3


def test_owner_and_recycle_state_checked_before_any_cache_hit(assistance):
    client, factory, _, document, calls = assistance
    occurrence = document["words"][0]["id"]
    run(factory, occurrence)
    with pytest.raises(HTTPException) as foreign:
        run(factory, occurrence, 2)
    assert foreign.value.status_code == 404
    assert client.delete(f"/api/documents/{document['id']}").status_code == 200
    with pytest.raises(HTTPException) as trashed:
        run(factory, occurrence)
    assert trashed.value.status_code == 404 and len(calls) == 1


def test_delete_while_model_runs_does_not_save_or_return_old_result(assistance, monkeypatch):
    client, factory, settings, document, _ = assistance
    original = sentences.LLMService._complete

    def delayed(*args, **kwargs):
        value = original(*args, **kwargs)
        assert client.delete(f"/api/documents/{document['id']}").status_code == 200
        return value

    monkeypatch.setattr(sentences.LLMService, "_complete", delayed)
    before = snapshot(factory)
    with pytest.raises(HTTPException) as deleted:
        run(factory, document["words"][0]["id"])
    assert deleted.value.status_code == 404 and snapshot(factory) == before
    assert not list((settings.storage_dir / "sentence-cache").glob("*.json"))


def test_concurrent_requests_generate_once_and_preserve_all_learning_tables(assistance):
    _, factory, _, document, calls = assistance
    before = snapshot(factory)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(factory, document["words"][0]["id"]), range(2)))
    assert sum(result.cached for result in results) == 1 and len(calls) == 1
    assert snapshot(factory) == before


def test_cache_hit_does_not_consume_public_model_budget(assistance):
    _, factory, settings, document, calls = assistance
    settings.app_mode = "public"
    settings.public_llm_user_daily_limit = 1
    run(factory, document["words"][0]["id"])
    with factory() as session:
        first = {row.bucket: row.count for row in session.scalars(select(PublicUsage))}
    assert len(first) == 2 and set(first.values()) == {1}
    assert run(factory, document["words"][1]["id"]).cached
    with factory() as session:
        assert {row.bucket: row.count for row in session.scalars(select(PublicUsage))} == first
    assert len(calls) == 1
    with pytest.raises(HTTPException) as limited:
        run(factory, document["words"][-1]["id"])
    assert limited.value.status_code == 429 and len(calls) == 1


def test_corrupted_cache_is_revalidated_and_replaced(assistance):
    _, factory, settings, document, calls = assistance
    occurrence = document["words"][0]["id"]
    run(factory, occurrence)
    path, = (settings.storage_dir / "sentence-cache").glob("*.json")
    path.write_text(json.dumps({"translation": "incomplete"}), encoding="utf-8")
    assert not run(factory, occurrence).cached
    assert run(factory, occurrence).cached and len(calls) == 2


def test_repeated_sentences_in_different_neighborhoods_do_not_share_cache(assistance):
    client, factory, settings, _, calls = assistance
    text = ("Financial records. " * 160 + "She sat by the bank. "
            + "River wildlife. " * 220 + "She sat by the bank. " + "Water and boats. " * 80)
    document = post(client, payload(text))
    banks = [word for word in document["words"] if word["word"] == "bank"]
    first = run(factory, banks[0]["id"])
    second = run(factory, banks[1]["id"])
    assert first.source == second.source == "llm" and not first.cached and not second.cached
    assert calls[0]["article_context"] != calls[1]["article_context"]
    same_sentence = next(word for word in document["words"] if word["word"] == "She"
                         and word["text_start"] == text.rfind("She sat"))
    assert run(factory, same_sentence["id"]).cached and len(calls) == 2


def test_repagination_invalidates_sentence_context_without_changing_text(assistance):
    _, factory, _, document, calls = assistance
    with factory() as session:
        doc = session.get(Document, document["id"])
        doc.source = "pdf"
        doc.pages = [{"number": 1, "text_start": 0, "text_end": len(doc.ocr_text)}]
        session.commit()
    occurrence = document["words"][0]["id"]
    assert not run(factory, occurrence).cached
    with factory() as session:
        doc = session.get(Document, document["id"])
        split = doc.ocr_text.index("A reader")
        doc.pages = [{"number": 1, "text_start": 0, "text_end": split},
                     {"number": 2, "text_start": split, "text_end": len(doc.ocr_text)}]
        session.commit()
    assert not run(factory, occurrence).cached and len(calls) == 2


def test_cache_io_failure_still_returns_valid_result(assistance, monkeypatch):
    _, factory, _, document, _ = assistance
    before = snapshot(factory)

    def fail(*args):
        raise OSError("test cache unavailable")

    monkeypatch.setattr(sentences, "_save", fail)
    assert run(factory, document["words"][0]["id"]).source == "llm"
    assert snapshot(factory) == before


def test_sentence_limit_and_missing_occurrence_fail_before_generation(assistance):
    _, factory, _, document, calls = assistance
    with factory() as session:
        word = session.get(WordOccurrence, document["words"][0]["id"])
        word.sentence = "word " * 801
        session.commit()
    before = snapshot(factory)
    for occurrence, expected in ((document["words"][0]["id"], 422), (999999, 404)):
        with pytest.raises(HTTPException) as invalid:
            run(factory, occurrence)
        assert invalid.value.status_code == expected
    assert not calls and snapshot(factory) == before


def test_http_contract_checks_owner_and_returns_structured_result(assistance):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.sentence_routes import router
    from backend.database import get_session

    _, factory, _, document, calls = assistance
    application = FastAPI()
    application.include_router(router)

    def own_session():
        with factory() as session:
            yield session

    application.dependency_overrides[get_session] = own_session
    before = snapshot(factory)
    with TestClient(application) as client:
        result = client.post("/api/sentences/analyze", json={"occurrence_id": document["words"][0]["id"]})
        assert result.status_code == 200
        assert result.json()["source"] == "llm" and result.json()["translation"]
        assert client.post("/api/sentences/analyze", json={"occurrence_id": 999999}).status_code == 404
        assert client.post("/api/sentences/analyze", json={"occurrence_id": 0}).status_code == 422
    assert len(calls) == 1 and snapshot(factory) == before
