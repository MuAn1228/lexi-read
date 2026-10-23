"""Connection reuse must keep credentials isolated and validation intact."""
import json
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Barrier, Event, Thread

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.config import Settings
from backend.services import llm


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    meaning: str = Field(min_length=1, max_length=100)


@pytest.fixture(autouse=True)
def clean_client_registry():
    llm._close_idle_clients()
    yield
    assert all(entry.in_use == 0 for entry in llm._clients.values())
    llm._close_idle_clients()


def settings(**overrides):
    return Settings(llm_provider="openai_compatible", llm_model="test-model",
                    llm_api_key="test-key", llm_base_url="https://provider.invalid/v1",
                    _env_file=None).model_copy(update=overrides)


def install_transport(monkeypatch, handler):
    real_client = httpx.Client
    created = []

    def factory(**kwargs):
        client = real_client(transport=httpx.MockTransport(handler), **kwargs)
        created.append((client, kwargs))
        return client

    monkeypatch.setattr(llm.httpx, "Client", factory)
    return created


def response(content=None):
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(
        {"meaning": "详细的中文释义"} if content is None else content)}, "finish_reason": "stop"}]})


def complete(config=None):
    return llm.LLMService(config or settings())._complete("Explain in Chinese.", {"word": "read"}, Answer)


def test_separate_service_instances_reuse_client_without_altering_payload(monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        return response()

    created = install_transport(monkeypatch, handle)
    assert complete().meaning == complete().meaning == "详细的中文释义"
    assert len(created) == 1 and len(requests) == 2
    client, options = created[0]
    assert not client.is_closed
    assert options["limits"].max_connections == 8
    assert options["limits"].max_keepalive_connections == 4
    assert options["limits"].keepalive_expiry == 60
    for request in requests:
        payload = json.loads(request.content)
        assert payload["model"] == "test-model"
        assert payload["max_tokens"] == settings().llm_max_tokens
        assert payload["response_format"] == {"type": "json_object"}
        schema_text = payload["messages"][0]["content"].split("JSON schema: ", 1)[1]
        assert json.loads(schema_text) == Answer.model_json_schema()
        assert schema_text == json.dumps(Answer.model_json_schema(), ensure_ascii=False, separators=(",", ":"))
        assert payload["messages"][1]["content"] == '{"word":"read"}'
    llm._close_idle_clients()
    assert client.is_closed


def test_successive_services_reuse_real_loopback_tcp_connection(monkeypatch):
    connections, received = [], []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            connections.append(self.connection)

        def do_POST(self):
            received.append(self.rfile.read(int(self.headers["Content-Length"])))
            body = response().content
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    thread.start()
    try:
        config = settings(llm_base_url=f"http://127.0.0.1:{server.server_port}/v1")
        assert complete(config).meaning == complete(config).meaning == "详细的中文释义"
        assert len(received) == 2 and len(connections) == 1
    finally:
        llm._close_idle_clients()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("overrides", [
    {"llm_api_key": "second-key"},
    {"llm_base_url": "https://another.invalid/v1"},
    {"llm_timeout_seconds": 12},
])
def test_provider_credentials_and_timeouts_have_separate_cookie_jars(monkeypatch, overrides):
    requests = []

    def handle(request):
        requests.append(request)
        result = response()
        result.headers["set-cookie"] = "provider-session=first; Path=/"
        return result

    created = install_transport(monkeypatch, handle)
    complete()
    complete(settings(**overrides))
    assert len(created) == 2
    assert [request.headers.get("cookie") for request in requests] == [None, None]
    assert requests[1].headers["authorization"] == "Bearer " + overrides.get("llm_api_key", "test-key")
    assert "test-key" not in repr(list(llm._clients))
    assert "second-key" not in repr(list(llm._clients))


def test_request_specific_model_mode_and_budget_remain_independent(monkeypatch):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return response()

    created = install_transport(monkeypatch, handle)
    complete()
    complete(settings(llm_model="another-model", llm_json_mode=False, llm_max_tokens=2048))
    assert len(created) == 1
    assert requests[1]["model"] == "another-model"
    assert requests[1]["max_tokens"] == 2048
    assert "response_format" not in requests[1]
    assert requests[0]["model"] == "test-model" and "response_format" in requests[0]


def test_idle_registry_entries_are_bounded_and_evicted_clients_close(monkeypatch):
    created = install_transport(monkeypatch, lambda _: response())
    for index in range(llm._MAX_CLIENT_CONFIGS + 1):
        complete(settings(llm_api_key=f"test-key-{index}"))
    assert len(llm._clients) == llm._MAX_CLIENT_CONFIGS
    assert created[0][0].is_closed
    assert all(not client.is_closed for client, _ in created[1:])


def test_concurrent_calls_share_one_client_without_serializing_requests(monkeypatch):
    arrived = Barrier(4)

    def handle(request):
        arrived.wait(timeout=5)
        return response()

    created = install_transport(monkeypatch, handle)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: complete(), range(4)))
    assert [result.meaning for result in results] == ["详细的中文释义"] * 4
    assert len(created) == 1
    assert all(entry.in_use == 0 for entry in llm._clients.values())


def test_active_client_is_never_evicted_or_closed_under_configuration_pressure(monkeypatch):
    entered, release = Event(), Event()

    def handle(request):
        if request.headers["authorization"] == "Bearer blocking-key":
            entered.set()
            assert release.wait(timeout=5)
        return response()

    monkeypatch.setattr(llm, "_MAX_CLIENT_CONFIGS", 1)
    created = install_transport(monkeypatch, handle)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(complete, settings(llm_api_key="blocking-key"))
        try:
            assert entered.wait(timeout=5)
            with pytest.raises(httpx.PoolTimeout):
                complete(settings(llm_timeout_seconds=.01))
            llm._close_idle_clients()
            assert len(created) == 1 and not created[0][0].is_closed
        finally:
            release.set()
        assert future.result(timeout=5).meaning == "详细的中文释义"
    complete()
    assert created[0][0].is_closed and len(llm._clients) == 1


def test_invalid_model_output_still_fails_full_schema_validation_and_releases_lease(monkeypatch):
    created = install_transport(monkeypatch, lambda _: response({"meaning": "", "unexpected": "x"}))
    with pytest.raises(ValidationError):
        complete()
    assert all(entry.in_use == 0 for entry in llm._clients.values())
    assert len(created) == 1


def test_transport_failure_releases_lease_without_automatic_retry(monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        raise httpx.ReadTimeout("test timeout", request=request)

    install_transport(monkeypatch, handle)
    with pytest.raises(httpx.ReadTimeout):
        complete()
    assert len(requests) == 1
    assert all(entry.in_use == 0 for entry in llm._clients.values())
