"""Read-only dictionary assistance, explicit AI work, and persistent safe caches."""
from concurrent.futures import ThreadPoolExecutor
import json

import httpx
import pytest

from backend.services import dictionary
from backend.services.dictionary import DetailedMeaning, Generation
from backend.tests.test_article_library import snapshot
from backend.tests.test_text_import import text_app  # noqa: F401


def lesson(word="bank", lemma="bank"):
    return {"word": word, "lemma": lemma, "summary": "可指银行，也可指河岸，需根据用法区分。",
            "senses": [
                {"part_of_speech": "noun 名词", "meaning": "银行", "explanation": "指提供存款、贷款等金融服务的机构，是可数名词。",
                 "examples": [{"english": "She opened an account at the bank.", "chinese": "她在这家银行开了账户。"}]},
                {"part_of_speech": "noun 名词", "meaning": "河岸", "explanation": "指河流两侧的岸边，常与 river 连用。",
                 "examples": [{"english": "We sat on the river bank.", "chinese": "我们坐在河岸上。"}]}],
            "collocations": [{"phrase": "bank account", "meaning": "银行账户",
                              "example": "I need a bank account.", "translation": "我需要一个银行账户。"}],
            "usage_notes": ["表示银行或河岸时都是可数名词。", "具体词义要根据句子中的搭配判断。"],
            "confusables": [{"word": "shore", "distinction": "shore 通常指海或湖边，bank 常指河岸。"}]}


@pytest.fixture
def assistance(text_app, monkeypatch):
    client, factory, settings = text_app
    settings.llm_provider = "openai_compatible"
    settings.llm_api_key = "dictionary-test-secret-never-persist"
    settings.llm_model = "test-model"
    monkeypatch.setattr(dictionary, "get_settings", lambda: settings)
    monkeypatch.setattr(dictionary, "lookup", lambda word, lemma: {
        "headword": word, "phonetic": "bæŋk", "chinese": ["n. 银行", "n. 堤, 岸"],
        "english": ["a financial institution", "sloping land by a river"], "forms": [],
        "source": "ecdict", "source_url": "https://github.com/skywind3000/ECDICT",
    })
    calls = []

    def complete(_self, instruction, data, schema):
        calls.append(data)
        assert "GENERAL dictionary" in instruction
        return schema.model_validate({"recognized": True, "detail": lesson(data["requested_word"], data["lemma"])})

    monkeypatch.setattr(dictionary.LLMService, "_complete", complete)
    return client, factory, settings, calls


def test_get_only_reads_offline_post_generates_once_and_restart_reads_cache(assistance, monkeypatch):
    client, factory, settings, calls = assistance
    before = snapshot(factory)
    first = client.get("/api/dictionary/Bank")
    assert first.status_code == 200 and first.headers["cache-control"] == "private, no-store"
    assert first.json()["dictionary"]["chinese"] == ["n. 银行", "n. 堤, 岸"]
    assert first.json()["detail"] is None and first.json()["can_generate"] and not calls
    assert not (settings.storage_dir / "dictionary-explanations").exists()
    generated = client.post("/api/dictionary/bank/explain")
    assert generated.status_code == 200 and generated.json()["detail"] == lesson()
    assert not generated.json()["cached"]
    assert generated.headers["cache-control"] == "private, no-store"
    assert client.post("/api/dictionary/bank/explain").json()["cached"]
    monkeypatch.setattr(dictionary.LLMService, "_complete", lambda *args: pytest.fail("GET/cache hit must not use model"))
    assert client.get("/api/dictionary/bank").json()["detail"] == lesson()
    assert len(calls) == 1 and snapshot(factory) == before
    cache = next((settings.storage_dir / "dictionary-explanations").glob("*.json"))
    assert settings.llm_api_key not in cache.read_text(encoding="utf-8")


def test_requests_do_not_flush_pending_learning_work(assistance):
    _, factory, _, _ = assistance
    from backend.models import User
    before = snapshot(factory)
    with factory() as session:
        pending = User(id=98765)
        session.add(pending)
        dictionary.explain_dictionary("bank", 1, session)
        assert pending in session.new
        with factory() as other:
            assert other.get(User, 98765) is None
        session.rollback()
    assert snapshot(factory) == before


@pytest.mark.parametrize("bad", ["word", "lemma", "blank", "english_only", "surrogate", "extra", "too_many_senses", "no_examples"])
def test_bad_model_output_keeps_dictionary_and_is_not_cached(assistance, monkeypatch, bad):
    client, factory, settings, _ = assistance
    before = snapshot(factory)

    def invalid(_self, instruction, data, schema):
        entry = lesson()
        if bad in ("word", "lemma"):
            entry[bad] = "different"
        elif bad == "blank":
            entry["summary"] = " "
        elif bad == "english_only":
            entry["senses"][0]["explanation"] = "This has no Chinese meaning."
        elif bad == "surrogate":
            entry["usage_notes"] = ["\ud800"]
        elif bad == "extra":
            entry["untrusted"] = "raw response"
        elif bad == "too_many_senses":
            entry["senses"] *= 5
        elif bad == "no_examples":
            entry["senses"][0]["examples"] = []
        return schema.model_validate({"recognized": True, "detail": entry})

    monkeypatch.setattr(dictionary.LLMService, "_complete", invalid)
    response = client.post("/api/dictionary/bank/explain")
    assert response.status_code == 200
    assert response.json()["detail"] is None and response.json()["dictionary"]
    assert "重试" in response.json()["warning"]
    assert not list(settings.storage_dir.glob("dictionary-explanations/*.json"))
    assert snapshot(factory) == before


def test_failure_does_not_expose_raw_upstream_or_key_and_can_retry(assistance, monkeypatch, caplog):
    client, _, settings, calls = assistance
    complete = dictionary.LLMService._complete

    def fail(*args):
        raise httpx.ReadTimeout("private remote error " + settings.llm_api_key)

    monkeypatch.setattr(dictionary.LLMService, "_complete", fail)
    response = client.post("/api/dictionary/bank/explain")
    assert response.status_code == 200 and response.json()["detail"] is None
    assert settings.llm_api_key not in response.text + caplog.text
    assert "private remote error" not in response.text + caplog.text
    monkeypatch.setattr(dictionary.LLMService, "_complete", complete)
    assert client.post("/api/dictionary/bank/explain").json()["detail"]
    assert len(calls) == 1


def test_unrecognized_word_never_gets_fabricated_lesson(assistance, monkeypatch):
    client, _, settings, _ = assistance
    monkeypatch.setattr(dictionary, "lookup", lambda *args: None)
    monkeypatch.setattr(dictionary.LLMService, "_complete", lambda *args: Generation(recognized=False, detail=None))
    response = client.post("/api/dictionary/zzzzqwer/explain").json()
    assert response["detail"] is None and response["dictionary"] is None
    assert "可靠释义" in response["warning"]
    assert not list(settings.storage_dir.glob("dictionary-explanations/*.json"))


def test_successful_ai_summary_is_available_without_offline_coverage(assistance, monkeypatch):
    client, _, _, _ = assistance
    monkeypatch.setattr(dictionary, "lookup", lambda *args: None)
    assert client.post("/api/dictionary/bank/explain").json()["warning"] is None
    assert dictionary.cached_summary("bank", 1) == lesson()["summary"]
    assert client.get("/api/dictionary/bank").json()["warning"] is None
    assert dictionary.cached_summary("bank", 2) is None


def test_offline_mode_and_missing_assets_are_explicit(assistance, monkeypatch):
    client, _, settings, calls = assistance
    settings.llm_provider = "offline"
    response = client.post("/api/dictionary/bank/explain").json()
    assert response["dictionary"] and not response["can_generate"] and response["detail"] is None
    assert "未启用" in response["warning"] and not calls

    def unavailable(*args):
        raise dictionary.OfflineDictionaryUnavailable("private path")

    monkeypatch.setattr(dictionary, "lookup", unavailable)
    response = client.get("/api/dictionary/bank").json()
    assert response["dictionary"] is None and "离线词典暂时不可用" in response["warning"]
    assert "private path" not in response["warning"]


@pytest.mark.parametrize("target", ["123", "中文", "a%3Cb", "a" * 151])
def test_invalid_target_is_rejected_before_ai(assistance, target):
    client, _, _, calls = assistance
    for suffix, method in (("", client.get), ("/explain", client.post)):
        assert method("/api/dictionary/" + target + suffix).status_code == 422
    assert not calls


def test_cache_identity_is_user_model_endpoint_and_word_scoped(assistance):
    _, factory, settings, calls = assistance
    with factory() as session:
        assert not dictionary.explain_dictionary("bank", 1, session).cached
        assert dictionary.read_dictionary("bank", 1).cached
        assert not dictionary.read_dictionary("bank", 2).cached
        assert not dictionary.read_dictionary("light", 1).cached
        settings.llm_model = "new-model"
        assert not dictionary.read_dictionary("bank", 1).cached
        dictionary.explain_dictionary("bank", 1, session)
        settings.llm_base_url = "https://another.example/v1"
        assert not dictionary.read_dictionary("bank", 1).cached
    assert len(calls) == 2


def test_legacy_unsupported_words_still_serialize_without_meanings(assistance):
    from datetime import datetime, timezone
    from backend.schemas import VocabularyOut
    now = datetime.now(timezone.utc)
    for word in ("word00", "中文", "", "a" * 151):
        assert dictionary.cached_summary(word, 1) is None
        entry = VocabularyOut(id=1, user_id=1, word=word, lemma=word, status=None,
                              mastery_score=None, seen_count=0, lookup_count=0,
                              created_at=now, updated_at=now, first_seen_at=None, last_seen_at=None)
        assert entry.model_dump(mode="json")["meaning_summary"] is None


@pytest.mark.parametrize("corruption", ["broken", "identity", "wrong_word", "oversize"])
def test_corrupt_cache_is_ignored_and_rebuilt(assistance, corruption):
    client, _, settings, calls = assistance
    client.post("/api/dictionary/bank/explain")
    path = next(settings.storage_dir.glob("dictionary-explanations/*.json"))
    data = json.loads(path.read_text(encoding="utf-8"))
    if corruption == "identity":
        data["identity"]["user"] = 2
    elif corruption == "wrong_word":
        data["detail"]["word"] = "light"
    path.write_text("{bad" if corruption == "broken" else "x" * 128_001 if corruption == "oversize" else json.dumps(data), encoding="utf-8")
    assert client.get("/api/dictionary/bank").json()["detail"] is None
    assert client.post("/api/dictionary/bank/explain").json()["detail"] == lesson()
    assert len(calls) == 2


def test_simultaneous_generation_creates_one_lesson(assistance):
    _, factory, _, calls = assistance

    def run(_):
        with factory() as session:
            return dictionary.explain_dictionary("bank", 1, session)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, range(4)))
    assert len(calls) == 1 and sum(not result.cached for result in results) == 1


def test_public_auth_budget_and_cache_hits(assistance, monkeypatch):
    client, factory, settings, calls = assistance
    settings.app_mode = "public"
    settings.public_llm_daily_limit = settings.public_llm_user_daily_limit = 1
    from backend.auth import token_digest
    from backend.models import GuestCredential
    from backend.public_limits import PublicUsage
    from sqlalchemy import select
    token = "a" * 43
    with factory() as session:
        session.add(GuestCredential(token_hash=token_digest(token), user_id=2))
        session.commit()
    assert client.get("/api/dictionary/bank").status_code == 401
    assert client.post("/api/dictionary/bank/explain").status_code == 401
    headers = {"Authorization": "Bearer " + token}
    assert client.get("/api/dictionary/bank", headers=headers).status_code == 200
    with factory() as session:
        assert not session.scalars(select(PublicUsage)).all()
    assert client.post("/api/dictionary/bank/explain", headers=headers).status_code == 200
    assert client.post("/api/dictionary/bank/explain", headers=headers).json()["cached"]
    assert client.post("/api/dictionary/light/explain", headers=headers).status_code == 429
    assert len(calls) == 1
