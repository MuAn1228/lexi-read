"""Synthetic personal-note contracts; no live credential or network is used."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from threading import Event

import httpx
from pydantic import SecretStr
import pytest

from backend.config import Settings
from backend.models import User
from backend.services import dictionary, maimemo_annotations as annotations
from backend.services.maimemo_client import MaimemoError
from backend.tests.test_article_library import snapshot
from backend.tests.test_text_import import text_app  # noqa: F401

TOKEN = "synthetic-maimemo-personal-secret"


@pytest.fixture(autouse=True)
def never_live(monkeypatch):
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda *a, **k: pytest.fail("No live HTTP in annotation tests"))


def payloads(word="bank"):
    return {"vocabulary": {"voc": {"id": "word-id", "spelling": word}},
            "interpretations": {"interpretations": [
                {"id": "i1", "interpretation": "n. 银行；河岸", "tags": ["容易混淆"], "status": "PUBLISHED"},
                {"id": "deleted", "interpretation": "不显示", "tags": [], "status": "DELETED"},
                {"id": "draft", "interpretation": "我的草稿", "tags": [], "status": "UNPUBLISHED"}]},
            "phrases": {"phrases": [{"id": "p1", "phrase": "We sat by the river bank.",
                        "interpretation": "我们坐在河岸边。", "status": "PUBLISHED", "origin": "个人例句"}]},
            "notes": {"notes": [{"id": "n1", "note_type": "ASSOCIATION", "note": "结合河边场景记忆。", "status": "PUBLISHED"}]}}


def client_for(data=None, calls=None, **kwargs):
    data = payloads() if data is None else data
    calls = [] if calls is None else calls

    def handler(request):
        assert request.method == "GET"
        assert request.headers["Authorization"] == "Bearer " + TOKEN
        assert request.headers["Accept-Encoding"] == "identity"
        resource = request.url.path.rsplit("/", 1)[1]
        calls.append(resource)
        assert dict(request.url.params) == ({"spelling": "bank"} if resource == "vocabulary" else {"voc_id": "word-id"})
        value = data[resource]
        if isinstance(value, httpx.Response):
            return value
        return httpx.Response(200, json={"success": True, "data": value, "errors": []})

    return annotations.MaimemoAnnotationClient(TOKEN, transport=httpx.MockTransport(handler), request_interval=0, **kwargs)


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, maimemo_api_token=SecretStr(TOKEN), _env_file=None)


def result(word="bank"):
    return annotations.MaimemoAnnotations(word=word, status="available",
        fetched_at=datetime.now(timezone.utc).isoformat(),
        interpretations=[{"id": "i1", "text": "银行；河岸", "tags": []}])


def test_official_own_content_fields_and_deleted_filter():
    calls = []
    value = client_for(calls=calls).fetch("bank")
    assert calls == ["vocabulary", "interpretations", "phrases", "notes"]
    assert value.source == "maimemo_personal" and value.status == "available"
    assert [row.id for row in value.interpretations] == ["i1", "draft"]
    assert value.phrases[0].chinese == "我们坐在河岸边。" and value.notes[0].type == "ASSOCIATION"
    assert TOKEN not in value.model_dump_json() and not value.cached


def test_empty_is_personal_content_absence_not_a_missing_dictionary():
    data = {"vocabulary": payloads()["vocabulary"], **{key: {key: []} for key in ("interpretations", "phrases", "notes")}}
    value = client_for(data).fetch("bank")
    assert value.status == "empty" and "自己创建" in value.warning and "内置词典" in value.warning


@pytest.mark.parametrize("vocabulary", [None, httpx.Response(404)])
def test_no_word_does_not_read_other_resources(vocabulary):
    calls = []
    value = client_for({"vocabulary": {"voc": vocabulary} if vocabulary is None else vocabulary}, calls).fetch("bank")
    assert value.status == "not_found" and calls == ["vocabulary"]


def test_wrong_vocabulary_never_reads_unrelated_notes():
    with pytest.raises(MaimemoError, match="不一致"):
        client_for(payloads("shore")).fetch("bank")


@pytest.mark.parametrize("status", [401, 403, 429, 500, 302])
def test_provider_failures_never_expose_secrets(status):
    with pytest.raises(MaimemoError) as error:
        client_for({"vocabulary": httpx.Response(status, text=TOKEN, headers={"Location": "https://example.invalid/" + TOKEN})}).fetch("bank")
    assert TOKEN not in str(error.value) and "https://" not in str(error.value)


@pytest.mark.parametrize("body,headers", [
    (b"x" * (annotations.MAX_BYTES + 1), {}),
    (b"not json", {}),
    (b"{}", {"Content-Encoding": "gzip"}),
    (json.dumps({"success": False, "errors": [{"msg": TOKEN}]}).encode(), {}),
], ids=["too-large", "not-json", "encoded", "error-envelope"])
def test_response_bytes_encoding_and_error_envelopes_are_bounded(body, headers):
    response = httpx.Response(200, stream=httpx.ByteStream(body), headers=headers)
    with pytest.raises(MaimemoError) as error:
        client_for({"vocabulary": response}).fetch("bank")
    assert TOKEN not in str(error.value)


def test_shared_rate_limit_never_sleeps_past_annotation_deadline(monkeypatch):
    from backend.services import maimemo_client as memo
    monkeypatch.setattr(memo, "_last_request_at", 100.0)
    monkeypatch.setattr(annotations, "monotonic", lambda: 100.0)
    monkeypatch.setattr(memo, "sleep", lambda seconds: pytest.fail("Deadline must be checked before sleep"))
    client = annotations.MaimemoAnnotationClient(TOKEN, request_interval=1.6)
    with pytest.raises(MaimemoError, match="超时"):
        client._throttle(100.5)
    assert memo._last_request_at == 100.0
    assert memo._request_lock.acquire(blocking=False)
    memo._request_lock.release()


def test_fetch_deadline_stops_before_first_request(monkeypatch):
    calls = []
    monkeypatch.setattr(annotations, "MAX_FETCH_SECONDS", 0)
    with pytest.raises(MaimemoError, match="超时"):
        client_for(calls=calls).fetch("bank")
    assert not calls


@pytest.mark.parametrize("resource", ["interpretations", "phrases", "notes"])
def test_partial_failure_retains_successful_content(resource):
    data = payloads()
    data[resource] = httpx.Response(503, text=TOKEN)
    value = client_for(data).fetch("bank")
    assert value.status == "available" and value.warning and not getattr(value, resource)
    assert any(getattr(value, key) for key in ("interpretations", "phrases", "notes"))
    assert TOKEN not in value.model_dump_json()


@pytest.mark.parametrize("bad", ["rows", "control", "oversize", "status", "credential"])
def test_untrusted_content_is_bounded_and_not_used(bad):
    data = payloads()
    row = data["interpretations"]["interpretations"][0]
    if bad == "rows":
        data["interpretations"]["interpretations"] = [row] * 101
    elif bad == "control":
        row["interpretation"] = "bad\x00"
    elif bad == "oversize":
        row["interpretation"] = "x" * 10_001
    elif bad == "status":
        row["status"] = "BAD"
    else:
        row["interpretation"] = TOKEN
    if bad == "credential":
        with pytest.raises(MaimemoError):
            client_for(data).fetch("bank")
    else:
        value = client_for(data).fetch("bank")
        assert not value.interpretations and value.status == "available" and value.warning


def test_get_cache_only_fetch_once_and_user_token_isolation(settings, monkeypatch):
    calls = []
    monkeypatch.setattr(annotations.MaimemoAnnotationClient, "fetch", lambda self, word: calls.append(word) or result(word))
    assert annotations.read_annotations("bank", 1, settings=settings) is None
    assert not list(settings.storage_dir.glob("maimemo-annotations/*"))
    assert not annotations.fetch_annotations("bank", 1, settings=settings).cached
    assert annotations.fetch_annotations("bank", 1, settings=settings).cached
    assert calls == ["bank"]
    assert annotations.read_annotations("bank", 2, settings=settings) is None
    settings.maimemo_api_token = SecretStr("another-synthetic-credential")
    assert annotations.read_annotations("bank", 1, settings=settings) is None
    for path in (settings.storage_dir / "maimemo-annotations").glob("*.json"):
        assert TOKEN not in path.read_text(encoding="utf-8")


def test_same_word_concurrency_reuses_one_generated_cache(settings, monkeypatch):
    calls = []
    monkeypatch.setattr(annotations.MaimemoAnnotationClient, "fetch", lambda self, word: calls.append(word) or result(word))
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(lambda _: annotations.fetch_annotations("bank", 1, settings=settings), range(4)))
    assert calls == ["bank"] and all(value.status == "available" for value in values)
    assert sum(not value.cached for value in values) == 1


@pytest.mark.parametrize("bad", ["truncated", "wrong_user", "wrong_word", "oversize", "nan_expiry"])
def test_invalid_cache_never_returns_another_user_or_word(settings, monkeypatch, bad):
    monkeypatch.setattr(annotations.MaimemoAnnotationClient, "fetch", lambda self, word: result(word))
    annotations.fetch_annotations("bank", 1, settings=settings)
    path = next((settings.storage_dir / "maimemo-annotations").glob("*.json"))
    data = json.loads(path.read_text(encoding="utf-8"))
    if bad == "truncated":
        path.write_text("{", encoding="utf-8")
    elif bad == "oversize":
        path.write_text("x" * (annotations.MAX_CACHE_BYTES + 1), encoding="utf-8")
    else:
        if bad == "wrong_user":
            data["identity"]["user"] = 2
        elif bad == "wrong_word":
            data["result"]["word"] = "shore"
        else:
            data["expires_at"] = float("nan")
        path.write_text(json.dumps(data), encoding="utf-8")
    assert annotations.read_annotations("bank", 1, settings=settings) is None


def test_negative_cache_expiry_and_explicit_refresh_cooldown(settings, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(annotations, "time", lambda: clock[0])
    calls = []
    monkeypatch.setattr(annotations.MaimemoAnnotationClient, "fetch", lambda self, word:
                        calls.append(word) or annotations.MaimemoAnnotations(word=word, status="empty"))
    annotations.fetch_annotations("bank", 1, settings=settings)
    annotations.fetch_annotations("bank", 1, settings=settings, refresh=True)
    assert calls == ["bank"]
    clock[0] += 61
    annotations.fetch_annotations("bank", 1, settings=settings)
    assert calls == ["bank"]
    annotations.fetch_annotations("bank", 1, settings=settings, refresh=True)
    assert calls == ["bank", "bank"]
    clock[0] += annotations.EMPTY_TTL + 1
    assert annotations.read_annotations("bank", 1, settings=settings).stale
    annotations.fetch_annotations("bank", 1, settings=settings)
    assert len(calls) == 3


def test_failure_cache_retry_and_stale_previous_content(settings, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(annotations, "time", lambda: clock[0])
    calls = []
    def failure(*args):
        calls.append(1)
        raise RuntimeError(TOKEN)
    monkeypatch.setattr(annotations.MaimemoAnnotationClient, "fetch", failure)
    first = annotations.fetch_annotations("bank", 1, settings=settings)
    assert first.status == "unavailable" and TOKEN not in first.model_dump_json()
    annotations.fetch_annotations("bank", 1, settings=settings)
    assert len(calls) == 1
    clock[0] += 61
    monkeypatch.setattr(annotations.MaimemoAnnotationClient, "fetch", lambda self, word: result(word))
    assert annotations.fetch_annotations("bank", 1, settings=settings).status == "available"
    clock[0] += 61
    monkeypatch.setattr(annotations.MaimemoAnnotationClient, "fetch", failure)
    stale = annotations.fetch_annotations("bank", 1, settings=settings, refresh=True)
    assert stale.status == "available" and stale.stale and stale.interpretations
    assert annotations.read_annotations("bank", 1, settings=settings).stale


def test_public_or_unconfigured_does_not_read_personal_cache_or_network(settings, monkeypatch):
    monkeypatch.setattr(annotations.MaimemoAnnotationClient, "fetch", lambda self, word: result(word))
    annotations.fetch_annotations("bank", 1, settings=settings)
    monkeypatch.setattr(annotations.MaimemoAnnotationClient, "fetch", lambda *a: pytest.fail("Personal API disabled"))
    settings.app_mode = "public"
    assert annotations.fetch_annotations("bank", 2, settings=settings) is None
    assert annotations.read_annotations("bank", 1, settings=settings) is None
    settings.app_mode = "local"
    settings.maimemo_api_token = SecretStr("")
    assert annotations.fetch_annotations("bank", 1, settings=settings) is None


def test_simultaneous_different_word_does_not_queue_network(settings, monkeypatch):
    entered, finish = Event(), Event()
    def slow(self, word):
        entered.set()
        assert finish.wait(5)
        return result(word)
    monkeypatch.setattr(annotations.MaimemoAnnotationClient, "fetch", slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(annotations.fetch_annotations, "bank", 1, settings=settings)
        assert entered.wait(5)
        second = annotations.fetch_annotations("shore", 1, settings=settings)
        assert second.status == "unavailable" and "另一个词" in second.warning
        finish.set()
        assert pending.result().status == "available"


@pytest.fixture
def integration(text_app, monkeypatch):
    client, factory, settings = text_app
    settings.maimemo_api_token = SecretStr(TOKEN)
    monkeypatch.setattr(dictionary, "get_settings", lambda: settings)
    monkeypatch.setattr("backend.api.dictionary_routes.get_settings", lambda: settings)
    monkeypatch.setattr(annotations.MaimemoAnnotationClient, "fetch", lambda self, word: result(word))
    monkeypatch.setattr(dictionary.LLMService, "_complete", lambda *a, **k: pytest.fail("Annotations must never call AI"))
    return client, factory, settings


def test_dictionary_api_local_cache_and_annotations_preserve_learning_tables(integration):
    client, factory, _ = integration
    before = snapshot(factory)
    initial = client.get("/api/dictionary/bank").json()
    assert initial["maimemo"] is None and initial["can_fetch_annotations"]
    response = client.post("/api/dictionary/bank/annotations")
    assert response.status_code == 200 and response.headers["cache-control"] == "private, no-store"
    assert response.json()["maimemo"]["status"] == "available" and response.json()["dictionary"]["chinese"]
    assert response.json()["detail"] is None
    assert client.get("/api/dictionary/bank").json()["maimemo"]["cached"]
    assert snapshot(factory) == before


def test_annotations_never_flush_caller_pending_data(integration):
    _, factory, _ = integration
    with factory() as session:
        pending = User(id=98765)
        session.add(pending)
        dictionary.fetch_dictionary_annotations("bank", 1)
        assert pending in session.new
        with factory() as other:
            assert other.get(User, 98765) is None
        session.rollback()


@pytest.mark.parametrize("body", [{"refresh": "true"}, {"refresh": 1}, {"extra": True}, []])
def test_refresh_input_is_strict(integration, body):
    client, _, _ = integration
    assert client.post("/api/dictionary/bank/annotations", json=body).status_code == 422


def test_public_dictionary_does_not_serve_local_personal_notes(integration, monkeypatch):
    client, _, settings = integration
    client.post("/api/dictionary/bank/annotations")
    settings.app_mode = "public"
    from backend.auth import get_current_user_id
    from backend.main import app
    app.dependency_overrides[get_current_user_id] = lambda: 2
    try:
        value = client.get("/api/dictionary/bank").json()
        assert value["maimemo"] is None and not value["can_fetch_annotations"]
        assert client.post("/api/dictionary/bank/annotations").status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_user_id, None)
