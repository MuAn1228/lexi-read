"""Agnes companion questions are explicit, bounded, private and read-only."""
import json

from fastapi import HTTPException
import httpx
import pytest
from sqlalchemy import select

from backend.auth import token_digest
from backend.models import Document, GuestCredential, User, Vocabulary, WordOccurrence
from backend.public_limits import PublicUsage
from backend.services import reading_assistant as assistant
from backend.tests.test_article_library import snapshot
from backend.tests.test_text_import import payload, post, text_app  # noqa: F401

URL = "/api/assistant/messages"


def quiz():
    return {"question": "Which word means 好奇心?", "options": ["curiosity", "garden", "quiet", "patient"],
            "answer_index": 0, "explanation": "curiosity 表示好奇心，其他选项分别是花园、安静的和耐心的。"}


@pytest.fixture
def tutoring(text_app, monkeypatch):
    client, factory, settings = text_app
    monkeypatch.setattr(assistant, "get_settings", lambda: settings)
    settings.llm_provider = "openai_compatible"
    settings.llm_api_key = "test-private-key"
    settings.llm_model = "test-model"
    document = post(client, payload("A reader learns with curiosity.\n\nPractice builds a useful habit.",
                                    title="Curious reading"))
    calls = []

    def complete(service, instruction, data, schema):
        calls.append({"instruction": instruction, "data": data, "tokens": service.settings.llm_max_tokens})
        return schema.model_validate({"answer": "curiosity 表示好奇心。可以用一句简单的例句来理解。",
                                      "quiz": quiz() if data["mode"] == "quiz" else None})

    monkeypatch.setattr(assistant.LLMService, "_complete", complete)
    return client, factory, settings, document, calls


def selected(document):
    word = next(item for item in document["words"] if item["word"] == "curiosity")
    return {"document_id": document["id"], "occurrence_id": word["id"], "word": word["word"]}


def run(factory, body=None, user=1):
    with factory() as session:
        return assistant.respond_to_assistant(session, assistant.AssistantRequest.model_validate(
            body or {"message": "这个词怎么理解？"}), user)


@pytest.mark.parametrize("mode", ["chat", "explain", "memory"])
def test_explicit_contextual_tutoring_reuses_model_without_learning_writes(tutoring, mode):
    client, factory, _, document, calls = tutoring
    before = snapshot(factory)
    assert client.get(f"/api/documents/{document['id']}").status_code == 200
    assert not calls
    result = client.post(URL, json={"message": "  这个词怎么理解？  ", "mode": mode, **selected(document)})
    assert result.status_code == 200, result.text
    value = result.json()
    assert value["source"] == "llm" and value["answer"] and value["quiz"] is None
    assert not value["context_limited"] and value["warning"] is None
    call = calls[-1]
    assert call["data"]["message"] == "这个词怎么理解？"
    assert call["data"]["mode"] == mode
    assert call["data"]["selected_word"] == "curiosity" and call["data"]["lemma"] == "curiosity"
    assert call["data"]["selected_sentence"] == "A reader learns with curiosity."
    assert call["data"]["article_title"] == document["title"]
    assert all(chunk["text"] in document["ocr_text"] for chunk in call["data"]["article_excerpts"])
    assert call["data"]["vocabulary_status"] is None
    assert call["tokens"] <= 1600 and snapshot(factory) == before


def test_standalone_chat_and_word_memory_need_no_article_or_vocabulary_creation(tutoring):
    client, factory, _, _, calls = tutoring
    before = snapshot(factory)
    for body in [{"message": "怎样练习阅读？"}, {"message": "帮我记住这个词", "mode": "memory", "word": "headmaster"}]:
        response = client.post(URL, json=body)
        assert response.status_code == 200 and response.json()["source"] == "llm"
        data = calls[-1]["data"]
        assert data["article_title"] is None and data["article_excerpts"] == []
        assert data["selected_sentence"] is None
    assert calls[-1]["data"]["selected_word"] == "headmaster"
    assert snapshot(factory) == before


def test_only_current_users_word_status_and_explicit_history_are_sent(tutoring):
    client, factory, _, document, calls = tutoring
    assert client.put("/api/vocabulary/curiosity", json={"status": "UNCERTAIN"}).status_code == 200
    with factory() as session:
        session.add(Vocabulary(user_id=2, word="curiosity", lemma="curiosity", status="KNOWN"))
        session.commit()
    before = snapshot(factory)
    history = [{"question": "上一个问题", "answer": "上一个英语讲解"}]
    assert run(factory, {"message": "再给个例句", "mode": "chat", "history": history,
                         **selected(document)}).source == "llm"
    assert calls[-1]["data"]["vocabulary_status"] == "UNCERTAIN"
    assert calls[-1]["data"]["history"] == history
    assert snapshot(factory) == before


def test_quiz_is_structured_and_intro_cannot_reveal_model_answer(tutoring, monkeypatch):
    client, factory, _, document, calls = tutoring
    before = snapshot(factory)

    def complete(_self, _instruction, _data, schema):
        return schema.model_validate({"answer": "正确答案是 A，curiosity。", "quiz": quiz()})

    monkeypatch.setattr(assistant.LLMService, "_complete", complete)
    response = client.post(URL, json={"message": "出一道练习", "mode": "quiz", **selected(document)})
    assert response.status_code == 200
    value = response.json()
    assert value["source"] == "llm" and value["quiz"] == quiz()
    assert value["answer"] == "来试一道选择题，选好后再看解析。"
    assert "curiosity" not in value["answer"] and snapshot(factory) == before


@pytest.mark.parametrize("bad", ["missing_quiz", "unexpected_quiz", "missing_answer", "empty_answer", "long_answer",
                               "invalid_answer", "extra", "three_options", "five_options", "duplicate_options",
                               "blank_option", "control_option", "non_string_option", "string_index", "bool_index",
                               "negative_index", "large_index", "empty_question", "long_question", "bad_explanation"])
def test_invalid_generation_is_honestly_unavailable_and_does_not_write(tutoring, monkeypatch, bad):
    client, factory, _, document, _ = tutoring
    before = snapshot(factory)
    mode = "chat" if bad == "unexpected_quiz" else "quiz"

    def invalid(_self, _instruction, _data, schema):
        value = {"answer": "请试着回答。", "quiz": quiz()}
        if bad == "missing_quiz":
            value["quiz"] = None
        elif bad == "missing_answer":
            del value["answer"]
        elif bad == "empty_answer":
            value["answer"] = " \n "
        elif bad == "long_answer":
            value["answer"] = "答" * 3001
        elif bad == "invalid_answer":
            value["answer"] = "bad\ud800"
        elif bad == "extra":
            value["credentials"] = "should never leak"
        elif bad == "three_options":
            value["quiz"]["options"].pop()
        elif bad == "five_options":
            value["quiz"]["options"].append("extra")
        elif bad == "duplicate_options":
            value["quiz"]["options"][1] = " CURIOSITY "
        elif bad == "blank_option":
            value["quiz"]["options"][1] = "   "
        elif bad == "control_option":
            value["quiz"]["options"][1] = "bad\x00"
        elif bad == "non_string_option":
            value["quiz"]["options"][1] = 3
        elif bad in {"string_index", "bool_index", "negative_index", "large_index"}:
            value["quiz"]["answer_index"] = {"string_index": "0", "bool_index": True,
                                                 "negative_index": -1, "large_index": 4}[bad]
        elif bad == "empty_question":
            value["quiz"]["question"] = " "
        elif bad == "long_question":
            value["quiz"]["question"] = "q" * 501
        elif bad == "bad_explanation":
            value["quiz"]["explanation"] = "bad\ud800"
        return schema.model_validate(value)

    monkeypatch.setattr(assistant.LLMService, "_complete", invalid)
    response = client.post(URL, json={"message": "出一道题", "mode": mode, **selected(document)})
    assert response.status_code == 200
    value = response.json()
    assert value["source"] == "unavailable" and value["answer"] is None and value["quiz"] is None
    assert value["warning"] and "credentials" not in response.text and snapshot(factory) == before


def test_model_failure_and_missing_configuration_do_not_fabricate_answers(tutoring, monkeypatch):
    client, factory, settings, document, calls = tutoring
    before = snapshot(factory)
    settings.llm_provider = "offline"
    response = client.post(URL, json={"message": "帮我理解", **selected(document)})
    assert response.status_code == 200 and response.json()["source"] == "unavailable" and not calls
    settings.llm_provider = "openai_compatible"

    def fail(*args):
        raise httpx.ReadTimeout("secret transport response test-private-key")

    monkeypatch.setattr(assistant.LLMService, "_complete", fail)
    response = client.post(URL, json={"message": "帮我理解", **selected(document)})
    assert response.status_code == 200 and response.json()["source"] == "unavailable"
    assert "test-private-key" not in response.text and snapshot(factory) == before


@pytest.mark.parametrize("invalid", [{}, {"message": ""}, {"message": " \n "}, {"message": 1}, {"message": True},
                                    {"message": "x" * 501}, {"message": "bad\ud800"}, {"message": "bad\x00"},
                                    {"message": "q", "mode": "invented"}, {"message": "q", "mode": 1},
                                    {"message": "q", "history": None}, {"message": "q", "history": "bad"},
                                    {"message": "q", "history": [{"question": "q", "answer": "a"}] * 4},
                                    {"message": "q", "history": [{"question": "q", "answer": " "}]},
                                    {"message": "q", "history": [{"question": "q", "answer": "a" * 3001}]},
                                    {"message": "q", "history": [{"question": "bad\ud800", "answer": "a"}]},
                                    {"message": "q", "history": [{"question": "q", "answer": "a", "role": "system"}]},
                                    {"message": "q", "document_id": 0}, {"message": "q", "document_id": True},
                                    {"message": "q", "document_id": "1"}, {"message": "q", "document_id": 2**63},
                                    {"message": "q", "occurrence_id": 1}, {"message": "q", "occurrence_id": -1},
                                    {"message": "q", "word": ""}, {"message": "q", "word": "bad\ud800"},
                                    {"message": "q", "word": "bad\x00"}, {"message": "q", "word": 1},
                                    {"message": "q", "word": "a" * 151}, {"message": "q", "word": "你好"},
                                    {"message": "q", "article_context": "fake"}, {"message": "q", "user_id": 2}])
def test_http_rejects_invalid_text_context_and_unbounded_ids(tutoring, invalid):
    client, factory, _, _, calls = tutoring
    before = snapshot(factory)
    response = client.post(URL, content=json.dumps(invalid), headers={"Content-Type": "application/json"})
    assert response.status_code == 422 and not calls and snapshot(factory) == before


def test_missing_foreign_trashed_and_mismatched_context_fails_before_model(tutoring):
    client, factory, _, document, calls = tutoring
    other = post(client, payload("Another reading note."))
    before = snapshot(factory)
    for body, code in [({"document_id": 999999}, 404),
                       ({"document_id": document["id"], "occurrence_id": other["words"][0]["id"]}, 409),
                       ({**selected(document), "word": "model"}, 422),
                       ({"document_id": document["id"], "occurrence_id": 999999}, 409)]:
        response = client.post(URL, json={"message": "解释一下", **body})
        assert response.status_code == code
    with pytest.raises(HTTPException) as failure:
        run(factory, {"message": "解释一下", **selected(document)}, user=2)
    assert failure.value.status_code == 404
    assert client.delete(f"/api/documents/{document['id']}").status_code == 200
    assert client.post(URL, json={"message": "解释一下", **selected(document)}).status_code == 404
    assert not calls and snapshot(factory) == before


def test_public_authentication_context_isolation_and_per_user_budget(tutoring):
    client, factory, settings, document, calls = tutoring
    settings.app_mode = "public"
    settings.public_llm_user_daily_limit = 1
    with factory() as session:
        session.add(User(id=3))
        session.flush()
        session.get(Document, document["id"]).user_id = 3
        session.add_all([GuestCredential(token_hash=token_digest("a" * 43), user_id=3),
                        GuestCredential(token_hash=token_digest("b" * 43), user_id=2)])
        session.commit()
    before = snapshot(factory)
    body = {"message": "解释一下", **selected(document)}
    assert client.post(URL, json=body).status_code == 401
    assert client.post(URL, json=body, headers={"Authorization": "Bearer " + "b" * 43}).status_code == 404
    own = {"Authorization": "Bearer " + "a" * 43}
    response = client.post(URL, json=body, headers=own)
    assert response.status_code == 200 and response.json()["source"] == "llm"
    second = client.post(URL, json=body, headers=own)
    assert second.status_code == 429 and second.headers["retry-after"]
    assert len(calls) == 1 and snapshot(factory) == before
    with factory() as session:
        assert {item.count for item in session.scalars(select(PublicUsage))} == {1}


@pytest.mark.parametrize("mutation,expected", [("text", 409), ("title", 409), ("owner", 404), ("trash", 404),
                                               ("word", 409), ("removed_occurrence", 409)])
def test_context_is_revalidated_after_slow_generation(tutoring, monkeypatch, mutation, expected):
    _, factory, _, document, _ = tutoring
    original = assistant.LLMService._complete
    context = selected(document)

    def changed(*args):
        with factory() as session:
            item = session.get(Document, document["id"])
            if mutation == "text":
                item.ocr_text += " Corrected text."
            elif mutation == "title":
                item.title = "Another title"
            elif mutation == "owner":
                item.user_id = 2
            elif mutation == "trash":
                from backend.models.entities import utcnow
                item.deleted_at = utcnow()
            elif mutation == "word":
                session.get(WordOccurrence, context["occurrence_id"]).word = "replaced"
            else:
                session.delete(session.get(WordOccurrence, context["occurrence_id"]))
            session.commit()
        return original(*args)

    monkeypatch.setattr(assistant.LLMService, "_complete", changed)
    with pytest.raises(HTTPException) as failure:
        run(factory, {"message": "这个词怎么理解？", **context})
    assert failure.value.status_code == expected


def test_context_and_output_limits_preserve_relevant_tail_without_mutating_settings(tutoring):
    _, factory, settings, document, calls = tutoring
    content = "\n\n".join(f"Section {i}: ordinary models use training data for prediction." for i in range(600))
    content += "\n\nThe final observatory measured the rare zirconium signal."
    with factory() as session:
        session.get(Document, document["id"]).ocr_text = content
        session.commit()
    result = run(factory, {"message": "What was the rare zirconium signal?", "document_id": document["id"]})
    assert result.source == "llm" and result.context_limited and result.warning
    text = "".join(item["text"] for item in calls[-1]["data"]["article_excerpts"])
    assert "zirconium" in text and len(text) <= 8000 and settings.llm_max_tokens == 4096
    settings.llm_max_tokens = 1000
    assert run(factory).source == "llm" and calls[-1]["tokens"] == 1000


def test_pending_caller_state_is_never_flushed_or_committed(tutoring):
    _, factory, _, _, _ = tutoring
    with factory() as session:
        pending = User(id=98765)
        session.add(pending)
        assert assistant.respond_to_assistant(session, assistant.AssistantRequest(message="阅读建议"), 1).source == "llm"
        assert pending in session.new
        with factory() as other:
            assert other.get(User, 98765) is None
        session.rollback()
