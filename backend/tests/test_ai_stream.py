"""SSE routes preserve cache, authorization, budgets and cancellation semantics."""
import asyncio
import json
from threading import Event

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from backend.auth import token_digest
from backend.models import Document, GuestCredential, User
from backend.public_limits import PublicUsage
from backend.services import ai_stream, dictionary, llm, reading_assistant as assistant
from backend.tests.test_dictionary import assistance, lesson  # noqa: F401
from backend.tests.test_llm_stream import Chunks, frame
from backend.tests.test_llm_transport import clean_client_registry, install_transport  # noqa: F401
from backend.tests.test_reading_assistant import quiz, selected, tutoring  # noqa: F401
from backend.tests.test_text_import import text_app  # noqa: F401

_real_complete = llm.LLMService._complete
DICTIONARY = "/api/dictionary/bank/explain-stream"
ASSISTANT = "/api/assistant/messages/stream"


def events(response):
    result = []
    for record in response.text.split("\n\n"):
        if not record.strip() or record.startswith(":"):
            continue
        lines = record.splitlines()
        result.append((lines[0].removeprefix("event: "), json.loads(lines[1].removeprefix("data: "))))
    return result


def provider(monkeypatch, content):
    monkeypatch.setattr(llm.LLMService, "_complete", _real_complete)
    split = content.index("summary") + len('summary":"可指银行') if "summary" in content else content.index("answer") + len('answer":"逐步生成')
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, stream=Chunks([frame(content[:split]), frame(content[split:]),
            frame(delta={}, finish="stop"), b"data: [DONE]\n\n"]))

    install_transport(monkeypatch, handle)
    return requests


def test_dictionary_real_stream_preserves_final_result_and_cache_hit_has_no_fake_progress(assistance, monkeypatch):
    client, _, _, _ = assistance
    calls = provider(monkeypatch, json.dumps({"recognized": True, "detail": lesson()},
                                           ensure_ascii=False, separators=(",", ":")))
    response = client.post(DICTIONARY)
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "private, no-store"
    data = events(response)
    assert data[0] == ("status", {"phase": "connecting"})
    assert any(event == "preview" and lesson()["summary"].startswith(item["text"]) for event, item in data)
    assert data[-1][0] == "result" and data[-1][1]["detail"] == lesson()
    assert len(calls) == 1 and calls[0]["stream"] is True
    cached = events(client.post(DICTIONARY))
    assert len(cached) == 1 and cached[0][0] == "result" and cached[0][1]["cached"]
    assert len(calls) == 1


@pytest.mark.parametrize("bad", ["word", "lemma", "schema", "truncated"])
def test_invalid_streamed_lesson_never_enters_cache_and_keeps_original_failure_result(assistance, monkeypatch, bad):
    client, _, settings, _ = assistance
    value = {"recognized": True, "detail": lesson()}
    if bad in {"word", "lemma"}:
        value["detail"][bad] = "different"
    elif bad == "schema":
        value["detail"]["senses"] = []
    content = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    provider(monkeypatch, content[:-10] if bad == "truncated" else content)
    response = client.post(DICTIONARY)
    data = events(response)
    assert data[-1][0] == "result" and data[-1][1]["detail"] is None
    assert data[-1][1]["dictionary"] and data[-1][1]["warning"]
    assert not list(settings.storage_dir.glob("dictionary-explanations/*.json"))


def test_dictionary_validation_and_public_quota_keep_http_status_before_sse_starts(assistance):
    client, factory, settings, calls = assistance
    assert client.post("/api/dictionary/123/explain-stream").status_code == 422
    assert not calls
    settings.app_mode = "public"
    settings.public_llm_user_daily_limit = 1
    with factory() as session:
        session.add(GuestCredential(token_hash=token_digest("a" * 43), user_id=2))
        session.commit()
    assert client.post(DICTIONARY).status_code == 401
    headers = {"Authorization": "Bearer " + "a" * 43}
    assert client.post(DICTIONARY, headers=headers).status_code == 200
    assert client.post(DICTIONARY, headers=headers).status_code == 200  # cache skips budget
    exhausted = client.post("/api/dictionary/garden/explain-stream", headers=headers)
    assert exhausted.status_code == 429 and exhausted.headers["retry-after"]
    assert len(calls) == 1
    with factory() as session:
        assert {row.count for row in session.scalars(select(PublicUsage))} == {1}


def test_assistant_stream_context_errors_remain_http_and_guest_isolation_is_preserved(tutoring):
    client, factory, settings, document, calls = tutoring
    assert client.post(ASSISTANT, json={"message": "提问", "document_id": 99999}).status_code == 404
    assert client.post(ASSISTANT, json={"message": "提问", **selected(document), "word": "other"}).status_code == 422
    assert client.post(ASSISTANT, json={"message": ""}).status_code == 422
    settings.app_mode = "public"
    with factory() as session:
        session.add(GuestCredential(token_hash=token_digest("b" * 43), user_id=2))
        session.commit()
    assert client.post(ASSISTANT, json={"message": "提问"}).status_code == 401
    assert client.post(ASSISTANT, json={"message": "提问", **selected(document)},
                       headers={"Authorization": "Bearer " + "b" * 43}).status_code == 404
    assert not calls


@pytest.mark.parametrize("mode", ["chat", "explain", "memory", "quiz"])
def test_assistant_answer_preview_is_real_and_quiz_never_previews_model_intro(tutoring, monkeypatch, mode):
    client, _, _, _, _ = tutoring
    content = json.dumps({"answer": "逐步生成的中文回答。", "quiz": quiz() if mode == "quiz" else None},
                         ensure_ascii=False, separators=(",", ":"))
    calls = provider(monkeypatch, content)
    response = client.post(ASSISTANT, json={"message": "帮我学习", "mode": mode})
    data = events(response)
    drafts = [item for event, item in data if event == "preview"]
    if mode == "quiz":
        assert not drafts and data[-1][1]["answer"] == assistant._QUIZ_INTRO
        assert data[-1][1]["quiz"] == quiz()
    else:
        assert drafts and "逐步生成的中文回答。".startswith(drafts[0]["text"])
        assert data[-1][1]["answer"] == "逐步生成的中文回答。"
    assert data[-1][0] == "result" and len(calls) == 1


def test_slow_assistant_context_revalidation_remains_enforced_after_stream_start(tutoring, monkeypatch):
    client, factory, _, document, _ = tutoring
    original = assistant.LLMService._complete

    def changed(*args):
        ai_stream.current_observer().status("connecting")
        with factory() as session:
            session.get(Document, document["id"]).title = "Changed while generating"
            session.commit()
        return original(*args)

    monkeypatch.setattr(assistant.LLMService, "_complete", changed)
    response = client.post(ASSISTANT, json={"message": "解释一下", **selected(document)})
    data = events(response)
    assert response.status_code == 200 and data[-1][0] == "error"
    assert data[-1][1]["status"] == 409


def test_unexpected_worker_errors_are_sanitized(assistance, monkeypatch):
    client, _, _, _ = assistance

    def fail(*args):
        ai_stream.current_observer().status("connecting")
        raise RuntimeError("secret transport URL and credentials")

    monkeypatch.setattr("backend.api.dictionary_routes.explain_dictionary", fail)
    response = client.post(DICTIONARY)
    data = events(response)
    assert data[-1] == ("error", {"message": "AI 服务暂时不可用，请稍后重试。", "status": 500})
    assert "secret" not in response.text


@pytest.mark.parametrize("started", [False, True])
def test_structured_http_exception_details_and_unapproved_headers_are_sanitized(assistance, monkeypatch, started):
    client, _, _, _ = assistance

    def fail(*args):
        if started:
            ai_stream.current_observer().status("connecting")
        raise HTTPException(422, {"private": "secret provider content"}, headers={"X-Private": "secret"})

    monkeypatch.setattr("backend.api.dictionary_routes.explain_dictionary", fail)
    response = client.post(DICTIONARY)
    if started:
        assert response.status_code == 200 and events(response)[-1][0] == "error"
    else:
        assert response.status_code == 422
    assert "secret" not in response.text and "x-private" not in response.headers


def test_stream_fallback_spends_exactly_one_business_budget_and_does_not_fake_preview(assistance, monkeypatch):
    client, factory, settings, _ = assistance
    settings.app_mode = "public"
    settings.public_llm_user_daily_limit = 1
    with factory() as session:
        session.add(GuestCredential(token_hash=token_digest("a" * 43), user_id=2))
        session.commit()
    monkeypatch.setattr(llm.LLMService, "_complete", _real_complete)
    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(400, json={"error": {"message": "stream is not supported", "param": "stream"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(
            {"recognized": True, "detail": lesson()}, ensure_ascii=False)}, "finish_reason": "stop"}]})

    install_transport(monkeypatch, handle)
    response = client.post(DICTIONARY, headers={"Authorization": "Bearer " + "a" * 43})
    data = events(response)
    assert data[-1][0] == "result" and data[-1][1]["detail"] == lesson()
    assert len(calls) == 2 and calls[0]["stream"] is True and "stream" not in calls[1]
    assert ("status", {"phase": "fallback"}) in data and not any(item[0] == "preview" for item in data)
    with factory() as session:
        assert {row.count for row in session.scalars(select(PublicUsage))} == {1}


def test_mailbox_is_bounded_and_coalesces_unsent_drafts():
    mailbox = ai_stream._Mailbox()
    for index in range(1000):
        mailbox.put("preview", {"text": str(index)})
    assert len(mailbox.items) == 1 and mailbox.take() == ("preview", {"text": "999"})
    for index in range(1000):
        mailbox.put("status", {"phase": str(index)})
    assert len(mailbox.items) == 8
    mailbox.put("result", {"detail": "final"})
    assert len(mailbox.items) == 8 and mailbox.items[-1][0] == "result"


def test_disconnect_closes_blocked_provider_and_prevents_cache_write_without_blocking_loop(assistance, monkeypatch):
    _, factory, settings, _ = assistance
    monkeypatch.setattr(llm.LLMService, "_complete", _real_complete)
    closed, done = Event(), Event()
    sessions = []

    class WaitingStream(httpx.SyncByteStream):
        def __iter__(self):
            yield frame('{"recognized":true,"detail":{"word":"bank","lemma":"bank","summary":"第一段真实草稿')
            assert closed.wait(3), "Disconnect must close the upstream without waiting for its next token"

        def close(self):
            closed.set()

    install_transport(monkeypatch, lambda _: httpx.Response(200, stream=WaitingStream()))

    class Request:
        async def is_disconnected(self):
            return False

    def operation(session):
        sessions.append(session)
        try:
            return dictionary.explain_dictionary("bank", 1, session)
        finally:
            done.set()

    async def exercise():
        with factory() as caller:
            response = await ai_stream.stream_response(Request(), caller.get_bind(), operation, kind="dictionary")
            async for record in response.body_iterator:
                if "event: preview" in record:
                    break
            await response.body_iterator.aclose()
            assert await asyncio.to_thread(closed.wait, 1)
            assert await asyncio.to_thread(done.wait, 1)
            assert sessions and sessions[0] is not caller

    asyncio.run(exercise())
    assert not list(settings.storage_dir.glob("dictionary-explanations/*.json"))
    assert all(entry.in_use == 0 for entry in llm._clients.values())


def test_cancel_at_final_validation_prevents_even_a_complete_lesson_entering_cache(assistance, monkeypatch):
    _, factory, settings, _ = assistance
    provider(monkeypatch, json.dumps({"recognized": True, "detail": lesson()},
                                   ensure_ascii=False, separators=(",", ":")))

    def emit(event, data):
        if event == "status" and data["phase"] == "validating":
            observer.cancel()

    observer = ai_stream.StreamObserver("dictionary", emit)
    with factory() as session, ai_stream.stream_context(observer), pytest.raises(ai_stream.StreamCancelled):
        dictionary.explain_dictionary("bank", 1, session)
    assert not list(settings.storage_dir.glob("dictionary-explanations/*.json"))
    assert all(entry.in_use == 0 for entry in llm._clients.values())
