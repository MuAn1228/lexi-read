"""A reveal is scoped assistance, never a lookup or an automatic assessment."""
from concurrent.futures import ThreadPoolExecutor
import json

from fastapi import HTTPException
import httpx
import pytest
from sqlalchemy import select

from backend.auth import token_digest
from backend.models import Document, GuestCredential, User, Vocabulary, WordOccurrence
from backend.public_limits import PublicUsage
from backend.services import review_meaning
from backend.tests.test_article_library import snapshot
from backend.tests.test_text_import import payload, post, text_app  # noqa: F401


@pytest.fixture
def assistance(text_app, monkeypatch):
    client, factory, settings = text_app
    monkeypatch.setattr(review_meaning, "get_settings", lambda: settings)
    settings.llm_provider = "openai_compatible"
    settings.llm_api_key = "test-key"
    settings.llm_model = "test-model"
    document = post(client, payload("A model learns from training data. A reader learns with curiosity."))
    occurrence = next(word for word in document["words"] if word["word"] == "model")
    with factory() as session:
        entry = session.scalar(select(Vocabulary).where(Vocabulary.lemma == occurrence["lemma"]))
        entry.status = "UNKNOWN"
        session.commit()
        vocabulary_id = entry.id
    calls = []

    def complete(_self, instruction, data, schema):
        calls.append(data)
        return schema.model_validate({"word": data["current_word"], "lemma": data["lemma"],
                                      "meaning_in_context": "模型", "simple_explanation": "在这里指从数据中学习规律的系统。",
                                      "part_of_speech": "noun"})

    monkeypatch.setattr(review_meaning.LLMService, "_complete", complete)
    return client, factory, settings, document, vocabulary_id, occurrence["id"], calls


def run(factory, vocabulary_id, occurrence_id, user=1):
    with factory() as session:
        return review_meaning.reveal_review_meaning(session, vocabulary_id, occurrence_id, user)


def body(vocabulary_id, occurrence_id):
    return {"vocabulary_id": vocabulary_id, "occurrence_id": occurrence_id}


def test_reveal_is_explicit_cached_and_preserves_every_learning_table(assistance):
    client, factory, settings, _, vocabulary_id, occurrence_id, calls = assistance
    before = snapshot(factory)
    assert client.get("/api/review").status_code == 200
    assert not calls
    first = client.post("/api/review/meaning", json=body(vocabulary_id, occurrence_id))
    second = client.post("/api/review/meaning", json=body(vocabulary_id, occurrence_id))
    assert first.status_code == second.status_code == 200
    assert first.json() == {"word": "model", "lemma": "model", "meaning_in_context": "模型",
                            "simple_explanation": "在这里指从数据中学习规律的系统。", "part_of_speech": "noun",
                            "source": "llm", "cached": False, "warning": None}
    assert second.json() == dict(first.json(), cached=True)
    assert len(calls) == 1 and snapshot(factory) == before
    assert len(list((settings.storage_dir / "review-meaning-cache").glob("*.json"))) == 1


def test_valid_reveal_does_not_flush_or_commit_unrelated_pending_work(assistance):
    _, factory, _, _, vocabulary_id, occurrence_id, _ = assistance
    before = snapshot(factory)
    with factory() as session:
        pending = User(id=12345)
        session.add(pending)
        assert review_meaning.reveal_review_meaning(session, vocabulary_id, occurrence_id, 1).source == "llm"
        assert pending in session.new
        with factory() as other:
            assert other.get(User, 12345) is None
        session.rollback()
    assert snapshot(factory) == before


@pytest.mark.parametrize("bad", ["word", "lemma", "empty", "surrogate", "control", "extra"])
def test_untrusted_model_output_is_rejected_and_not_cached(assistance, monkeypatch, bad):
    _, factory, settings, _, vocabulary_id, occurrence_id, _ = assistance
    before = snapshot(factory)

    def invalid(_self, instruction, data, schema):
        value = {"word": data["current_word"], "lemma": data["lemma"],
                 "meaning_in_context": "模型", "simple_explanation": "解释", "part_of_speech": "noun"}
        if bad in ("word", "lemma"):
            value[bad] = "different"
        elif bad == "empty":
            value["meaning_in_context"] = " "
        elif bad == "surrogate":
            value["simple_explanation"] = "bad\ud800value"
        elif bad == "control":
            value["part_of_speech"] = "bad\x00value"
        else:
            value["new_example"] = "Invented sentence"
        return schema.model_validate(value)

    monkeypatch.setattr(review_meaning.LLMService, "_complete", invalid)
    result = run(factory, vocabulary_id, occurrence_id)
    assert result.source == "offline" and "有限" in result.warning and "不是 AI" in result.warning
    assert not result.cached and snapshot(factory) == before
    assert not list((settings.storage_dir / "review-meaning-cache").glob("*.json"))


def test_timeout_retry_does_not_cache_or_expose_provider_exception(assistance, monkeypatch):
    _, factory, settings, _, vocabulary_id, occurrence_id, _ = assistance
    before = snapshot(factory)
    calls = []
    original = review_meaning.LLMService._complete

    def timeout(*args, **kwargs):
        calls.append(1)
        raise httpx.ReadTimeout("private secret endpoint and credentials")

    monkeypatch.setattr(review_meaning.LLMService, "_complete", timeout)
    for _ in range(2):
        result = run(factory, vocabulary_id, occurrence_id)
        assert result.source == "offline" and "private" not in result.warning
    assert len(calls) == 2 and snapshot(factory) == before
    assert not list((settings.storage_dir / "review-meaning-cache").glob("*.json"))
    monkeypatch.setattr(review_meaning.LLMService, "_complete", original)
    assert run(factory, vocabulary_id, occurrence_id).source == "llm"
    assert run(factory, vocabulary_id, occurrence_id).cached


@pytest.mark.parametrize("provider,key,model", [("offline", "key", "model"),
                                              ("openai_compatible", "", "model"),
                                              ("openai_compatible", "key", "")])
def test_offline_and_unconfigured_service_use_explicit_limited_glossary(assistance, provider, key, model):
    _, factory, settings, document, vocabulary_id, occurrence_id, calls = assistance
    settings.llm_provider, settings.llm_api_key, settings.llm_model = provider, key, model
    before = snapshot(factory)
    covered = run(factory, vocabulary_id, occurrence_id)
    assert covered.source == "offline" and covered.meaning_in_context == "模型"
    assert "不是 AI" in covered.warning and not covered.cached
    other = document["words"][-1]
    with factory() as session:
        unknown = session.scalar(select(Vocabulary).where(Vocabulary.lemma == other["lemma"]))
        unavailable = review_meaning.reveal_review_meaning(session, unknown.id, other["id"], 1)
    assert unavailable.source == "unavailable" and unavailable.meaning_in_context is None
    assert unavailable.simple_explanation is None and unavailable.part_of_speech is None and unavailable.warning
    assert not calls and snapshot(factory) == before


def test_non_ml_context_does_not_offer_domain_meaning_for_an_ordinary_word(assistance):
    client, factory, settings, _, _, _, calls = assistance
    settings.llm_provider = "offline"
    doc = post(client, payload("She is a fashion model wearing a blue coat."))
    target = next(word for word in doc["words"] if word["word"] == "model")
    with factory() as session:
        entry = session.scalar(select(Vocabulary).where(Vocabulary.lemma == "model"))
    result = run(factory, entry.id, target["id"])
    assert result.source == "unavailable" and result.meaning_in_context is None and not calls


def test_scope_mismatch_and_missing_context_fail_before_cache_or_model(assistance):
    _, factory, _, document, vocabulary_id, occurrence_id, calls = assistance
    before = snapshot(factory)
    for vocab, occurrence, user, expected in ((vocabulary_id, occurrence_id, 2, 404),
                                               (999999, occurrence_id, 1, 404),
                                               (vocabulary_id, 999999, 1, 404),
                                               (vocabulary_id, document["words"][-1]["id"], 1, 422)):
        with pytest.raises(HTTPException) as failure:
            run(factory, vocab, occurrence, user)
        assert failure.value.status_code == expected
    assert not calls and snapshot(factory) == before


def test_foreign_document_unreadable_even_when_lemma_matches_owned_vocabulary(assistance):
    _, factory, _, document, vocabulary_id, occurrence_id, calls = assistance
    with factory() as session:
        session.get(Document, document["id"]).user_id = 2
        session.commit()
    with pytest.raises(HTTPException) as failure:
        run(factory, vocabulary_id, occurrence_id)
    assert failure.value.status_code == 404 and not calls


def test_public_http_requires_identity_and_rejects_another_visitors_card(assistance):
    client, factory, settings, document, vocabulary_id, occurrence_id, calls = assistance
    settings.app_mode = "public"
    with factory() as session:
        session.add(User(id=3))
        session.flush()
        session.get(Document, document["id"]).user_id = 3
        session.get(Vocabulary, vocabulary_id).user_id = 3
        session.add_all([GuestCredential(token_hash=token_digest("a" * 43), user_id=3),
                        GuestCredential(token_hash=token_digest("b" * 43), user_id=2)])
        session.commit()
    before = snapshot(factory)
    payload_value = body(vocabulary_id, occurrence_id)
    assert client.post("/api/review/meaning", json=payload_value).status_code == 401
    assert client.post("/api/review/meaning", json=payload_value,
                       headers={"Authorization": "Bearer " + "b" * 43}).status_code == 404
    own = client.post("/api/review/meaning", json=payload_value,
                      headers={"Authorization": "Bearer " + "a" * 43})
    assert own.status_code == 200 and own.json()["source"] == "llm"
    assert len(calls) == 1 and snapshot(factory) == before


def test_reveal_uses_original_inflected_surface_not_lemma(assistance):
    client, factory, _, _, _, _, calls = assistance
    document = post(client, payload("Models use training data."))
    target = document["words"][0]
    with factory() as session:
        entry = session.scalar(select(Vocabulary).where(Vocabulary.lemma == target["lemma"]))
    result = run(factory, entry.id, target["id"])
    assert result.word == "Models" and result.lemma == "model"
    assert calls[-1]["current_word"] == "Models"
    assert calls[-1]["current_sentence"] == "Models use training data."


def test_trashed_context_cannot_return_existing_cache(assistance):
    client, factory, _, document, vocabulary_id, occurrence_id, calls = assistance
    run(factory, vocabulary_id, occurrence_id)
    before = snapshot(factory)
    assert client.delete(f"/api/documents/{document['id']}").status_code == 200
    with pytest.raises(HTTPException) as failure:
        run(factory, vocabulary_id, occurrence_id)
    assert failure.value.status_code == 404 and len(calls) == 1 and snapshot(factory) == before


@pytest.mark.parametrize("failed", [False, True])
def test_trash_during_model_generation_never_returns_result_or_fallback(assistance, monkeypatch, failed):
    client, factory, settings, document, vocabulary_id, occurrence_id, _ = assistance
    original = review_meaning.LLMService._complete

    def delayed(*args, **kwargs):
        assert client.delete(f"/api/documents/{document['id']}").status_code == 200
        if failed:
            raise httpx.ReadTimeout("test failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(review_meaning.LLMService, "_complete", delayed)
    before = snapshot(factory)
    with pytest.raises(HTTPException) as failure:
        run(factory, vocabulary_id, occurrence_id)
    assert failure.value.status_code == 404 and snapshot(factory) == before
    assert not list((settings.storage_dir / "review-meaning-cache").glob("*.json"))


def test_concurrent_reveals_generate_only_once_and_preserve_learning(assistance):
    _, factory, _, _, vocabulary_id, occurrence_id, calls = assistance
    before = snapshot(factory)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(factory, vocabulary_id, occurrence_id), range(2)))
    assert sum(value.cached for value in results) == 1 and len(calls) == 1
    assert snapshot(factory) == before


def test_public_cache_reuse_skips_budget_and_other_context_miss_is_limited(assistance):
    _, factory, settings, document, vocabulary_id, occurrence_id, calls = assistance
    settings.app_mode = "public"
    settings.public_llm_user_daily_limit = 1
    before = snapshot(factory)
    run(factory, vocabulary_id, occurrence_id)
    with factory() as session:
        usage = {row.bucket: row.count for row in session.scalars(select(PublicUsage))}
    assert len(usage) == 2 and set(usage.values()) == {1}
    assert run(factory, vocabulary_id, occurrence_id).cached
    with factory() as session:
        assert {row.bucket: row.count for row in session.scalars(select(PublicUsage))} == usage
        other = document["words"][-1]
        entry = session.scalar(select(Vocabulary).where(Vocabulary.lemma == other["lemma"]))
    with pytest.raises(HTTPException) as failure:
        run(factory, entry.id, other["id"])
    assert failure.value.status_code == 429 and len(calls) == 1 and snapshot(factory) == before


def test_document_and_provider_identity_prevent_stale_restored_cache(assistance):
    _, factory, settings, document, vocabulary_id, occurrence_id, calls = assistance
    run(factory, vocabulary_id, occurrence_id)
    settings.llm_model = "another-model"
    assert not run(factory, vocabulary_id, occurrence_id).cached
    settings.llm_base_url = "https://different.invalid/v1"
    assert not run(factory, vocabulary_id, occurrence_id).cached
    with factory() as session:
        session.get(Document, document["id"]).ocr_text += " More restored content."
        session.commit()
    assert not run(factory, vocabulary_id, occurrence_id).cached
    assert len(calls) == 4


def test_corrupt_cache_revalidates_and_cache_io_failure_retains_valid_explanation(assistance, monkeypatch):
    _, factory, settings, _, vocabulary_id, occurrence_id, calls = assistance
    run(factory, vocabulary_id, occurrence_id)
    path, = (settings.storage_dir / "review-meaning-cache").glob("*.json")
    path.write_text(json.dumps({"word": "different", "lemma": "model", "meaning_in_context": "模型",
                                "simple_explanation": "说明", "part_of_speech": "noun"}), encoding="utf-8")
    assert not run(factory, vocabulary_id, occurrence_id).cached and len(calls) == 2
    assert run(factory, vocabulary_id, occurrence_id).cached
    path.unlink()

    def fail(*args):
        raise OSError("test cache unavailable")

    monkeypatch.setattr(review_meaning, "_save", fail)
    assert run(factory, vocabulary_id, occurrence_id).source == "llm" and len(calls) == 3


@pytest.mark.parametrize("field,value", [("sentence", "word " * 801), ("sentence", " "),
                                       ("word", "a" * 151)])
def test_oversized_or_empty_source_fails_before_generation(assistance, field, value):
    _, factory, _, _, vocabulary_id, occurrence_id, calls = assistance
    with factory() as session:
        setattr(session.get(WordOccurrence, occurrence_id), field, value)
        session.commit()
    before = snapshot(factory)
    with pytest.raises(HTTPException) as failure:
        run(factory, vocabulary_id, occurrence_id)
    assert failure.value.status_code == 422 and not calls and snapshot(factory) == before


@pytest.mark.parametrize("invalid", [{}, {"vocabulary_id": 1}, {"occurrence_id": 1},
                                    {"vocabulary_id": 0, "occurrence_id": 1},
                                    {"vocabulary_id": True, "occurrence_id": 1},
                                    {"vocabulary_id": "1", "occurrence_id": 1},
                                    {"vocabulary_id": 1, "occurrence_id": -1},
                                    {"vocabulary_id": 1, "occurrence_id": 2**63},
                                    {"vocabulary_id": 1, "occurrence_id": 1, "sentence": "invented"}])
def test_http_request_is_strict_and_never_accepts_client_context(assistance, invalid):
    client, factory, _, _, _, _, calls = assistance
    before = snapshot(factory)
    assert client.post("/api/review/meaning", json=invalid).status_code == 422
    assert not calls and snapshot(factory) == before
