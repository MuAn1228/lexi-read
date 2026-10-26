"""Provider streaming is incremental, bounded, cancellable, and fully validated."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from time import monotonic

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.services import llm
from backend.services.ai_stream import StreamCancelled, StreamObserver, stream_context
from backend.services.dictionary import Generation
from backend.services.reading_assistant import AssistantGeneration
from backend.tests.test_dictionary import lesson
from backend.tests.test_llm_transport import clean_client_registry, install_transport, settings  # noqa: F401


class Chunks(httpx.SyncByteStream):
    def __init__(self, chunks, before_chunk=None):
        self.chunks = chunks
        self.before_chunk = before_chunk
        self.yielded = 0
        self.closed = False

    def __iter__(self):
        for index, chunk in enumerate(self.chunks):
            if self.before_chunk:
                self.before_chunk(index)
            self.yielded += 1
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk

    def close(self):
        self.closed = True


def frame(content=None, *, finish=None, delta=None, newline="\n"):
    value = {"choices": [{"index": 0, "delta": delta if delta is not None else {"content": content},
                          "finish_reason": finish}]}
    return ("data: " + json.dumps(value, ensure_ascii=False) + newline * 2).encode()


def streamed(chunks, *, before_chunk=None):
    stream = Chunks(chunks, before_chunk)
    return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=stream), stream


def complete(observer, schema=AssistantGeneration):
    with stream_context(observer):
        return llm.LLMService(settings())._complete("Give a Chinese answer.", {"word": "bank"}, schema)


def previews(events):
    return [data["text"] for event, data in events if event == "preview"]


def phases(events):
    return [data["phase"] for event, data in events if event == "status"]


@pytest.mark.parametrize("kind", ["dictionary", "assistant"])
def test_preview_is_emitted_before_remaining_provider_content_is_requested(monkeypatch, kind):
    events, requests = [], []
    if kind == "dictionary":
        result = {"recognized": True, "detail": lesson()}
        serialized = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        marker = "可指银行"
        split = serialized.index(marker) + len(marker)
        expected = result["detail"]["summary"]
        schema = Generation
    else:
        serialized = '{"answer":"已经收到的完整回答。","quiz":null}'
        marker = "已经收到"
        split = serialized.index(marker) + len(marker)
        expected = "已经收到的完整回答。"
        schema = AssistantGeneration

    def before_chunk(index):
        if index == 1:
            assert previews(events) == [marker]
            assert "validating" not in phases(events)

    response, source = streamed([frame(serialized[:split]), frame(serialized[split:]),
                                 frame(delta={}, finish="stop"), b"data: [DONE]\n\n"],
                                before_chunk=before_chunk)

    def handle(request):
        requests.append(json.loads(request.content))
        return response

    install_transport(monkeypatch, handle)
    result = complete(StreamObserver(kind, lambda event, data: events.append((event, data))), schema)
    assert (result.detail.summary if kind == "dictionary" else result.answer) == expected
    assert requests[0]["stream"] is True and len(requests) == 1
    assert phases(events)[0] == "connecting"
    assert "generating" in phases(events) and phases(events)[-1] == "validating"
    assert source.closed and all(entry.in_use == 0 for entry in llm._clients.values())


def test_sse_and_utf8_cross_tcp_chunks_crlf_and_reasoning_never_becomes_preview(monkeypatch):
    events = []
    payload = (b": provider heartbeat\r\n\r\n"
               + frame(delta={"role": "assistant", "reasoning_content": "private reasoning"}, newline="\r\n")
               + frame('{"answer":"逐步到达😀', newline="\r\n")
               + frame('的中文回答。","quiz":null}', newline="\r\n")
               + frame(delta={}, finish="stop", newline="\r\n")
               + b"data: [DONE]\r\n\r\n")
    # One byte per TCP chunk exercises split CRLF, SSE fields and multibyte UTF-8.
    response, source = streamed([payload[index:index + 1] for index in range(len(payload))])
    install_transport(monkeypatch, lambda _: response)
    result = complete(StreamObserver("assistant", lambda event, data: events.append((event, data))))
    assert result.answer == "逐步到达😀的中文回答。"
    assert previews(events) and all(result.answer.startswith(text) for text in previews(events))
    assert "private reasoning" not in json.dumps(events)
    assert source.closed


@pytest.mark.parametrize("content,finish,done,expected", [
    ('{"answer":"","quiz":null}', "stop", True, ValidationError),
    ('{"answer":"回答","unexpected":"private"}', "stop", True, ValidationError),
    ('{"answer":"未完整生成的回答","quiz":null}', "length", True, ValueError),
    ('{"answer":"被过滤的回答","quiz":null}', "content_filter", True, ValueError),
    ('{"answer":"没有正常结束的回答","quiz":null}', None, True, ValueError),
    ('{"answer":"连接突然结束的回答","quiz":null}', None, False, ValueError),
    ('{"answer":"格式不完整', "stop", True, ValidationError),
])
def test_stream_still_requires_normal_finish_and_complete_schema(monkeypatch, content, finish, done, expected):
    events, requests = [], []
    chunks = [frame(content)]
    if finish is not None:
        chunks.append(frame(delta={}, finish=finish))
    if done:
        chunks.append(b"data: [DONE]\n\n")
    response, source = streamed(chunks)

    def handle(request):
        requests.append(request)
        return response

    install_transport(monkeypatch, handle)
    with pytest.raises(expected):
        complete(StreamObserver("assistant", lambda event, data: events.append((event, data))))
    assert len(requests) == 1 and "fallback" not in phases(events)
    assert source.closed and all(entry.in_use == 0 for entry in llm._clients.values())


class LongAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(max_length=40_000)


@pytest.mark.parametrize("content_length,valid", [(30_000, True), (30_001, False)])
def test_stream_preserves_thirty_thousand_character_response_limit(monkeypatch, content_length, valid):
    content = json.dumps({"answer": "答" * (content_length - len('{"answer":""}'))},
                         ensure_ascii=False, separators=(",", ":"))
    assert len(content) == content_length
    response, source = streamed([frame(content), frame(delta={}, finish="stop"), b"data: [DONE]\n\n"])
    install_transport(monkeypatch, lambda _: response)
    observer = StreamObserver("assistant", lambda *_: None)
    if valid:
        assert len(complete(observer, LongAnswer).answer) == content_length - len('{"answer":""}')
    else:
        with pytest.raises(ValueError):
            complete(observer, LongAnswer)
    assert source.closed and all(entry.in_use == 0 for entry in llm._clients.values())


@pytest.mark.parametrize("status", [400, 404, 415, 422])
def test_explicit_unsupported_stream_status_falls_back_once_without_fake_preview(monkeypatch, status):
    events, requests = [], []
    content = '{"answer":"普通请求返回的完整回答。","quiz":null}'

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            assert payload["stream"] is True
            return httpx.Response(status, json={"error": "stream parameter not supported"})
        assert "stream" not in payload
        return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]})

    install_transport(monkeypatch, handle)
    result = complete(StreamObserver("assistant", lambda event, data: events.append((event, data))))
    assert result.answer == "普通请求返回的完整回答。" and len(requests) == 2
    assert phases(events).count("fallback") == 1 and not previews(events)
    assert all(entry.in_use == 0 for entry in llm._clients.values())


@pytest.mark.parametrize("status", [400, 422])
@pytest.mark.parametrize("error", [
    {"error": "Unknown model", "request": {"stream": True}},
    {"error": {"message": "Unknown model", "param": "model"}, "stream": True},
    {"error": "The message parameter is invalid"},
])
def test_unrelated_bad_request_does_not_trigger_stream_fallback(monkeypatch, status, error):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(status, json=error)

    install_transport(monkeypatch, handle)
    with pytest.raises(httpx.HTTPStatusError):
        complete(StreamObserver("assistant", lambda *_: None))
    assert len(requests) == 1


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_other_http_failures_are_never_retried_as_nonstream(monkeypatch, status):
    events, requests = [], []

    def handle(request):
        requests.append(request)
        return httpx.Response(status, json={"error": "secret provider details"})

    install_transport(monkeypatch, handle)
    with pytest.raises(httpx.HTTPStatusError):
        complete(StreamObserver("assistant", lambda event, data: events.append((event, data))))
    assert len(requests) == 1 and "fallback" not in phases(events)


@pytest.mark.parametrize("failure_at", ["connect", "read", "malformed"])
def test_network_and_format_errors_do_not_trigger_another_provider_request(monkeypatch, failure_at):
    events, requests = [], []
    response, source = streamed([frame('{"answer":"已收到的草稿'),
                                 httpx.ReadTimeout("secret network failure") if failure_at == "read"
                                 else b"data: invalid provider JSON\n\n"])

    def handle(request):
        requests.append(request)
        if failure_at == "connect":
            raise httpx.ConnectTimeout("secret connection failure", request=request)
        return response

    install_transport(monkeypatch, handle)
    with pytest.raises((httpx.HTTPError, ValueError)):
        complete(StreamObserver("assistant", lambda event, data: events.append((event, data))))
    assert len(requests) == 1 and "fallback" not in phases(events)
    if failure_at != "connect":
        assert source.closed
    assert all(entry.in_use == 0 for entry in llm._clients.values())


def test_cancel_after_first_real_preview_closes_transport_without_consuming_completion(monkeypatch):
    events = []
    response, source = streamed([frame('{"answer":"收到第一段'),
                                 frame('，后续不应读取。","quiz":null}'), frame(delta={}, finish="stop")])
    install_transport(monkeypatch, lambda _: response)

    def emit(event, data):
        events.append((event, data))
        if event == "preview":
            observer.cancel()

    observer = StreamObserver("assistant", emit)
    with pytest.raises(StreamCancelled):
        complete(observer)
    assert previews(events) == ["收到第一段"] and source.yielded == 1
    assert "validating" not in phases(events)
    assert observer.response is None and source.closed
    assert all(entry.in_use == 0 for entry in llm._clients.values())


def test_quiz_observer_can_disable_all_answer_preview(monkeypatch):
    events = []
    response, source = streamed([frame('{"answer":"正确答案可能泄漏'),
                                 frame('，仅用于最终结构验证。","quiz":null}'),
                                 frame(delta={}, finish="stop"), b"data: [DONE]\n\n"])
    install_transport(monkeypatch, lambda _: response)
    result = complete(StreamObserver("assistant", lambda event, data: events.append((event, data)),
                                     preview_enabled=False))
    assert result.answer.startswith("正确答案") and not previews(events) and source.closed


def test_external_cancel_interrupts_real_http_read_before_next_provider_chunk(monkeypatch):
    """A socket stalled after a real token must not delay cancellation to timeout."""
    previewed, release_server, completed, cancel_completed = Event(), Event(), Event(), Event()
    outcomes = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            self.close_connection = True
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()

            def send(data):
                self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                self.wfile.flush()

            try:
                send(frame('{"answer":"真实到达的第一段'))
                # No more bytes arrive until the test finishes or fails. The
                # provider's delay is deliberately longer than cancellation.
                release_server.wait(timeout=10)
                send(frame('，后面的回答。","quiz":null}'))
                send(frame(delta={}, finish="stop"))
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        def log_message(self, *_):
            pass

    def emit(event, data):
        if event == "preview":
            assert data["text"] == "真实到达的第一段"
            previewed.set()

    observer = StreamObserver("assistant", emit)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    config = settings(llm_base_url=f"http://127.0.0.1:{server.server_port}/v1", llm_timeout_seconds=10)

    def generate():
        try:
            with stream_context(observer):
                llm.LLMService(config)._complete("Answer in Chinese.", {}, AssistantGeneration)
        except BaseException as exc:
            outcomes.append(exc)
        finally:
            completed.set()

    def cancel():
        try:
            observer.cancel()
        finally:
            cancel_completed.set()

    worker = Thread(target=generate, daemon=True)
    canceller = Thread(target=cancel, daemon=True)
    server_thread.start()
    worker.start()
    try:
        assert previewed.wait(timeout=3), "the local provider did not deliver the first real token"
        # Give the generation thread time to enter its next blocking socket
        # read; cancellation inside the preview callback would miss that case.
        assert not completed.wait(timeout=.05)
        started = monotonic()
        canceller.start()
        assert completed.wait(timeout=2), "cancellation waited for the next provider chunk or read timeout"
        assert cancel_completed.wait(timeout=.5), "closing the upstream response blocked cancellation"
        assert monotonic() - started < 2.5
        assert len(outcomes) == 1 and isinstance(outcomes[0], StreamCancelled)
        assert observer.response is None
        assert all(entry.in_use == 0 for entry in llm._clients.values())
    finally:
        release_server.set()
        worker.join(timeout=3)
        if canceller.ident is not None:
            canceller.join(timeout=3)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=3)
