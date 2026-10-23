"""Fast reusable facts must preserve fresh personalization and feedback safety."""
from concurrent.futures import Future, ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
from threading import Barrier, BoundedSemaphore, Event, Lock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, text

from backend import public_limits
from backend.config import Settings
from backend.models import LearningEvent
from backend.schemas import Relation
from backend.services import word_expansion_cache as cache
from backend.services.llm import GeneratedCandidates, LLMService
from backend.tests.test_word_expansion import (candidate, expansion_app, issue, learning_snapshot,
                                               mock_candidates)  # noqa: F401


@pytest.fixture
def config(tmp_path):
    return Settings(data_dir=tmp_path, llm_provider="openai_compatible", llm_model="test-model",
                    llm_base_url="https://model.example/v1", llm_api_key="fake-test-key", _env_file=None)


def generated(word="creation"):
    return GeneratedCandidates([candidate(word)], "llm")


def fetch(config, generate, *, refresh=False, user=1, word="Create", lemma="create",
          relation=Relation.WORD_FAMILY):
    return cache.candidates(config, user, word, lemma, relation, generate, refresh=refresh)


def cached_path(config, user=1):
    return cache.identity(config, user, "Create", "create", Relation.WORD_FAMILY)[1]


def test_success_survives_a_fresh_python_process_and_never_saves_a_batch_or_secret(config):
    first, reused = fetch(config, generated)
    assert reused is False and first.candidates[0].word == "creation"
    path = cached_path(config)
    record = json.loads(path.read_text(encoding="utf-8"))
    assert set(record) == {"identity", "created_at", "batch"}
    assert set(record["batch"]) == {"candidates"}
    assert "fake-test-key" not in path.read_text(encoding="utf-8")
    script = """
import sys
from pathlib import Path
from backend.config import Settings
from backend.schemas import Relation
from backend.services.word_expansion_cache import candidates
config = Settings(data_dir=Path(sys.argv[1]), llm_provider='openai_compatible',
    llm_model='test-model', llm_base_url='https://model.example/v1',
    llm_api_key='fake-test-key', _env_file=None)
def never_generate():
    raise AssertionError('Restart must use the disk cache')
value, cached = candidates(config, 1, 'Create', 'create', Relation.WORD_FAMILY, never_generate)
assert cached and value.candidates[0].word == 'creation'
print('cache restored')
"""
    process = subprocess.run([sys.executable, "-c", script, str(config.storage_dir)],
                             cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stderr
    assert process.stdout.strip() == "cache restored"
    assert not list(path.parent.glob("*.tmp"))


@pytest.mark.parametrize("change", [
    {"user": 2}, {"word": "Creating"}, {"lemma": "make"}, {"relation": Relation.SYNONYM},
    {"setting": ("llm_provider", "another-provider")}, {"setting": ("llm_model", "another-model")},
    {"setting": ("llm_base_url", "https://another.example/v1")},
    {"setting": ("llm_api_key", "another-key")}, {"setting": ("llm_json_mode", False)},
    {"version": 2},
])
def test_user_word_relation_and_provider_identity_are_isolated(config, monkeypatch, change):
    fetch(config, generated)
    original = cached_path(config)
    options = dict(change)
    if "setting" in options:
        name, value = options.pop("setting")
        config = config.model_copy(update={name: value})
    if "version" in options:
        monkeypatch.setattr(cache, "PROMPT_VERSION", options.pop("version"))
    calls = []

    def run():
        calls.append(True)
        return generated()

    fetch(config, run, **options)
    assert calls == [True] and original.exists()


def test_equivalent_word_spacing_case_and_endpoint_slash_reuse_identity(config):
    fetch(config, generated)
    alternate = config.model_copy(update={"llm_base_url": config.llm_base_url + "/"})
    result, reused = fetch(alternate, lambda: pytest.fail("Unexpected generation"), word="  CREATE ")
    assert reused and result.candidates[0].word == "creation"


@pytest.mark.parametrize("corrupt", [
    "json", "oversize", "expired", "future", "nan", "identity", "relation", "english", "self",
    "duplicate", "empty", "shape", "control", "too-many",
])
def test_corrupt_or_stale_cache_is_validated_before_reuse(config, corrupt):
    fetch(config, generated)
    path = cached_path(config)
    record = json.loads(path.read_text(encoding="utf-8"))
    if corrupt == "json":
        raw = "not json"
    elif corrupt == "oversize":
        raw = " " * (cache.MAX_CACHE_BYTES + 1)
    else:
        if corrupt == "expired":
            record["created_at"] -= cache.CACHE_TTL_SECONDS
        elif corrupt == "future":
            record["created_at"] += cache.CACHE_TTL_SECONDS
        elif corrupt == "nan":
            record["created_at"] = float("nan")
        elif corrupt == "identity":
            record["identity"]["user"] = 99
        elif corrupt == "relation":
            record["batch"]["candidates"][0]["relation"] = "synonym"
        elif corrupt == "english":
            record["batch"]["candidates"][0]["meaning"] = "English only"
        elif corrupt == "self":
            record["batch"]["candidates"][0]["word"] = "created"
        elif corrupt == "duplicate":
            record["batch"]["candidates"] *= 2
        elif corrupt == "empty":
            record["batch"]["candidates"] = []
        elif corrupt == "shape":
            record["batch"] = None
        elif corrupt == "control":
            record["batch"]["candidates"][0]["reason"] += "\x00"
        elif corrupt == "too-many":
            record["batch"]["candidates"] *= 13
        raw = json.dumps(record, ensure_ascii=False)
    path.write_text(raw, encoding="utf-8")
    result, reused = fetch(config, lambda: generated("creative"))
    assert not reused and result.candidates[0].word == "creative"


@pytest.mark.parametrize("kind", ["empty", "offline", "invalid", "exception"])
def test_failures_offline_empty_and_invalid_candidates_are_not_cached(config, kind):
    calls = []

    def run():
        calls.append(True)
        if kind == "exception":
            raise RuntimeError("test failure")
        if kind == "offline":
            return GeneratedCandidates([], "offline", "暂不可用")
        return GeneratedCandidates([] if kind == "empty" else [candidate("create")], "llm")

    for _ in range(2):
        if kind == "exception":
            with pytest.raises(RuntimeError):
                fetch(config, run)
        else:
            _, reused = fetch(config, run)
            assert reused is False
    assert len(calls) == 2 and not cached_path(config).exists() and not cache._inflight


def test_force_refresh_replaces_facts_and_a_failed_refresh_preserves_last_success(config):
    fetch(config, generated)
    result, reused = fetch(config, lambda: generated("creative"), refresh=True)
    assert not reused and result.candidates[0].word == "creative"
    failed, reused = fetch(config, lambda: GeneratedCandidates([], "offline", "暂不可用"), refresh=True)
    assert failed.source == "offline" and not reused
    result, reused = fetch(config, lambda: pytest.fail("Unexpected generation"))
    assert reused and result.candidates[0].word == "creative"


def test_disk_write_failure_does_not_discard_valid_result_or_leave_temporary_files(config, monkeypatch):
    def fail(*_):
        raise OSError("test disk failure")

    monkeypatch.setattr(cache.os, "replace", fail)
    result, reused = fetch(config, generated)
    assert not reused and result.candidates[0].word == "creation"
    assert not cached_path(config).exists()
    assert not list(config.storage_dir.rglob("*.tmp"))


@pytest.mark.parametrize("force,failure", [(False, False), (True, False), (False, True)])
def test_same_key_concurrent_requests_share_exactly_one_attempt_and_registry_cleans_up(config, monkeypatch,
                                                                                       force, failure):
    ready, followers, release = Event(), Event(), Event()
    waiting, calls = [], []
    lock = Lock()

    class ObservedFuture(Future):
        def result(self, timeout=None):
            with lock:
                waiting.append(True)
                if len(waiting) == 5:
                    followers.set()
            return super().result(timeout=timeout)

    monkeypatch.setattr(cache, "Future", ObservedFuture)

    def generate():
        calls.append(True)
        ready.set()
        assert release.wait(10)
        if failure:
            raise RuntimeError("shared failure")
        return generated()

    def run():
        try:
            return fetch(config, generate, refresh=force)
        except RuntimeError:
            return "failed"

    with ThreadPoolExecutor(max_workers=6) as executor:
        jobs = [executor.submit(run) for _ in range(6)]
        try:
            assert ready.wait(10) and followers.wait(10)
        finally:
            release.set()
        values = [job.result(timeout=10) for job in jobs]
    assert len(calls) == 1 and not cache._inflight
    if failure:
        assert values == ["failed"] * 6
        assert fetch(config, generated)[1] is False
    else:
        assert sum(value[1] for value in values) == 5
        values[0][0].candidates[0].meaning = "改动其中一个响应"
        assert all(value[0].candidates[0].meaning != "改动其中一个响应" for value in values[1:])


def test_unrelated_words_do_not_block_each_other(config):
    started = Barrier(2)

    def run(word):
        def generate():
            started.wait(timeout=10)
            return generated()
        return fetch(config, generate, word=word, lemma=word)

    with ThreadPoolExecutor(max_workers=2) as executor:
        jobs = [executor.submit(run, word) for word in ("create", "read")]
        assert all(job.result(timeout=15)[1] is False for job in jobs)


@pytest.mark.parametrize("relation", list(Relation))
def test_compact_adapter_sends_only_language_fields_and_no_scores(monkeypatch, config, relation):
    real_client = httpx.Client
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"candidates": [{"word": "creation", "meaning": "创造；创作",
                                                   "reason": "后缀 -ion 将动词转成名词。"}]})}}]})

    monkeypatch.setattr("backend.services.llm.httpx.Client",
                        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    result = LLMService(config).expand_vocabulary("Create", "create", relation)
    assert result.source == "llm" and result.candidates[0].relation == relation
    assert result.candidates[0].semantic_relevance == .9
    payload = json.loads(requests[0].content)
    assert json.loads(payload["messages"][1]["content"]) == {
        "current_word": "Create", "lemma": "create", "requested_relation": relation.value}
    schema = json.loads(payload["messages"][0]["content"].split("JSON schema: ", 1)[1])
    assert schema["properties"]["candidates"]["maxItems"] == 5
    assert set(schema["$defs"]["ExpansionCandidate"]["properties"]) == {"word", "meaning", "reason"}
    assert "fake-test-key" not in requests[0].content.decode()


@pytest.mark.parametrize("response", [
    {"candidates": [{"word": "creation", "meaning": "English only", "reason": "构词说明"}]},
    {"candidates": [{"word": "creation", "meaning": "创造", "reason": "English only"}]},
    {"candidates": [{"word": "creation", "meaning": "创造", "reason": "构词说明", "score": .9}]},
    {"candidates": [{"word": "creation", "meaning": "创造", "reason": "构词说明"}] * 6},
])
def test_compact_adapter_rejects_invalid_outputs(monkeypatch, config, response):
    real_client = httpx.Client
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"choices": [{
        "finish_reason": "stop", "message": {"content": json.dumps(response)}}]}))
    monkeypatch.setattr("backend.services.llm.httpx.Client",
                        lambda **kwargs: real_client(transport=transport, **kwargs))
    result = LLMService(config).expand_vocabulary("create", "create", Relation.WORD_FAMILY)
    assert result.source == "offline" and result.warning and result.candidates == []


def test_cache_hit_issues_fresh_batch_and_ranks_against_new_knowledge_and_feedback(expansion_app, monkeypatch):
    client, factory, _ = expansion_app
    calls = mock_candidates(monkeypatch, [candidate(word) for word in
                                         ("creation", "creative", "creativity", "creator", "creatively")])
    before = learning_snapshot(factory)
    first = issue(client).json()
    assert learning_snapshot(factory) == before
    target = first["items"][0]["word"]
    endpoint = f"/api/recommendations/{first['batch_id']}/feedback"
    marked = client.post(endpoint, json={"word": target, "action": "KNOWN", "request_id": str(uuid4())})
    assert marked.status_code == 200, marked.text
    feedback_target = first["items"][1]["word"]
    assert client.post(endpoint, json={"word": feedback_target, "action": "NOT_USEFUL",
                                       "request_id": str(uuid4())}).status_code == 200
    after_mark = learning_snapshot(factory)
    second = issue(client).json()
    assert second["cached"] is True and first["cached"] is False and len(calls) == 1
    assert second["batch_id"] != first["batch_id"] and len(second["items"]) == 3
    assert target not in {row["word"] for row in second["items"]}
    assert feedback_target not in {row["word"] for row in second["items"]}
    assert any("此前推荐过" in row["reason"] for row in second["items"])
    assert learning_snapshot(factory) == after_mark
    with factory() as session:
        original = session.get(LearningEvent, first["batch_id"])
        assert original.details["items"] == first["items"]
        # The cache contains raw explanations, never personalized suffixes.
        path = cached_path(LLMService().settings)
        assert "此前推荐过" not in path.read_text(encoding="utf-8")


def test_expansion_cache_rechecks_source_ownership_after_read(expansion_app, monkeypatch):
    client, factory, _ = expansion_app
    calls = mock_candidates(monkeypatch, [candidate("creation")])
    assert issue(client).status_code == 200
    original_read = cache._read

    def after_delete(*args):
        value = original_read(*args)
        with factory() as session:
            session.execute(text("DELETE FROM vocabulary WHERE user_id=1 AND lemma='create'"))
            session.commit()
        return value

    monkeypatch.setattr(cache, "_read", after_delete)
    response = issue(client)
    assert response.status_code == 404 and len(calls) == 1
    assert len(client.get("/api/events").json()) == 1


@pytest.mark.parametrize("value", [0, 1, "true", "false", None, [], {}])
def test_refresh_requires_a_real_json_boolean(expansion_app, monkeypatch, value):
    client, _, _ = expansion_app
    calls = mock_candidates(monkeypatch, [candidate("creation")])
    response = client.post("/api/vocabulary/create/recommend", json={"relation": "word_family", "refresh": value})
    assert response.status_code == 422 and calls == []


def test_public_cache_hit_skips_quota_and_provider_slots_but_force_still_obeys_limits(expansion_app, monkeypatch):
    client, factory, settings = expansion_app
    settings.app_mode = "public"
    settings.public_llm_user_daily_limit = 1
    calls = mock_candidates(monkeypatch, [candidate("creation")])
    headers = {"Authorization": "Bearer " + "a" * 43}
    first = issue(client, headers=headers)
    assert first.status_code == 200, first.text
    with factory() as session:
        before = list(session.execute(select(public_limits.PublicUsage.bucket, public_limits.PublicUsage.count)))
    assert len(before) == 2 and all(row.count == 1 for row in before)
    monkeypatch.setattr(public_limits, "_llm_slots", BoundedSemaphore(0))
    reused = issue(client, headers=headers)
    assert reused.status_code == 200 and reused.json()["cached"] is True
    rejected = client.post("/api/vocabulary/create/recommend", json={"relation": "word_family", "refresh": True},
                           headers=headers)
    assert rejected.status_code == 429 and len(calls) == 1
    monkeypatch.setattr(public_limits, "_llm_slots", BoundedSemaphore(2))
    rejected = client.post("/api/vocabulary/create/recommend", json={"relation": "word_family", "refresh": True},
                           headers=headers)
    assert rejected.status_code == 429 and len(calls) == 1
    with factory() as session:
        assert list(session.execute(select(public_limits.PublicUsage.bucket, public_limits.PublicUsage.count))) == before
    assert issue(client, headers={"Authorization": "Bearer forged"}).status_code == 401
    assert issue(client, word="absent", headers=headers).status_code == 404


def test_explicit_refresh_and_users_do_not_reuse_other_users_cache(expansion_app, monkeypatch):
    client, _, settings = expansion_app
    calls = mock_candidates(monkeypatch, [candidate("creation")])
    first = issue(client).json()
    assert issue(client).json()["cached"] is True and len(calls) == 1
    refreshed = client.post("/api/vocabulary/create/recommend", json={"relation": "word_family", "refresh": True})
    assert refreshed.status_code == 200 and not refreshed.json()["cached"] and len(calls) == 2
    assert first["batch_id"] != refreshed.json()["batch_id"]
    settings.app_mode = "public"
    second = issue(client, headers={"Authorization": "Bearer " + "a" * 43})
    assert second.status_code == 200 and not second.json()["cached"] and len(calls) == 3
    assert second.json()["batch_id"] != first["batch_id"]


def test_concurrent_public_requests_issue_individual_batches_with_one_provider_budget(expansion_app, monkeypatch):
    client, factory, settings = expansion_app
    settings.app_mode = "public"
    settings.public_llm_user_daily_limit = 1
    ready, followers, release = Event(), Event(), Event()
    calls, waiting = [], []
    lock = Lock()

    class ObservedFuture(Future):
        def result(self, timeout=None):
            with lock:
                waiting.append(True)
                if len(waiting) == 3:
                    followers.set()
            return super().result(timeout=timeout)

    monkeypatch.setattr(cache, "Future", ObservedFuture)

    def expand(self, word, lemma, relation):
        calls.append(True)
        ready.set()
        assert release.wait(10)
        return GeneratedCandidates([candidate(value) for value in
                                    ("creation", "creative", "creativity", "creator", "creatively")], "llm")

    monkeypatch.setattr(LLMService, "expand_vocabulary", expand)
    headers = {"Authorization": "Bearer " + "a" * 43}
    before = learning_snapshot(factory)
    with ThreadPoolExecutor(max_workers=4) as executor:
        jobs = [executor.submit(issue, client, headers=headers) for _ in range(4)]
        try:
            assert ready.wait(10) and followers.wait(10)
        finally:
            release.set()
        responses = [job.result(timeout=15) for job in jobs]
    assert all(response.status_code == 200 for response in responses), [r.text for r in responses]
    values = [response.json() for response in responses]
    assert len(calls) == 1 and len({value["batch_id"] for value in values}) == 4
    assert sum(value["cached"] for value in values) == 3
    assert learning_snapshot(factory) == before
    with factory() as session:
        batches = list(session.scalars(select(LearningEvent)))
        assert len(batches) == 4 and all(batch.user_id == 2 for batch in batches)
        usage = list(session.execute(select(public_limits.PublicUsage.count)))
        assert len(usage) == 2 and all(row.count == 1 for row in usage)
    for value in values:
        saved = next(batch for batch in batches if batch.id == value["batch_id"])
        assert saved.details["items"] == value["items"]
