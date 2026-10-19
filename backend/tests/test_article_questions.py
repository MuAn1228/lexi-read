"""Grounded article Q&A is explicit, private, bounded, and read-only."""
from concurrent.futures import ThreadPoolExecutor
import json

from fastapi import HTTPException
import httpx
import pytest
from sqlalchemy import select

from backend.auth import token_digest
from backend.models import Document, GuestCredential, User
from backend.public_limits import PublicUsage
from backend.services import article_questions as questions
from backend.tests.test_article_library import snapshot
from backend.tests.test_text_import import payload, post, text_app  # noqa: F401


@pytest.fixture
def assistance(text_app, monkeypatch):
    client, factory, settings = text_app
    monkeypatch.setattr(questions, "get_settings", lambda: settings)
    settings.llm_provider = "openai_compatible"
    settings.llm_api_key = "test-key"
    settings.llm_model = "test-model"
    document = post(client, payload("🙂中文 A model learns from training data.\n\nA reader learns with curiosity.",
                                    title="Reading with models"))
    calls = []

    def complete(_self, instruction, data, schema):
        calls.append(data)
        chunk = data["article_chunks"][0]
        return schema.model_validate({"answer": "模型从训练数据中学习。", "answerable": True,
                                      "citations": [{"chunk_id": chunk["id"], "quote": chunk["text"][:100]}]})

    monkeypatch.setattr(questions.LLMService, "_complete", complete)
    return client, factory, settings, document, calls


def run(factory, document_id, question="模型如何学习？", history=None, user=1):
    with factory() as session:
        return questions.answer_article_question(
            session, document_id, questions.ArticleQuestionRequest(question=question, history=history or []), user)


def url(document_id):
    return f"/api/documents/{document_id}/questions"


def test_questions_are_explicit_grounded_cached_and_preserve_learning(assistance):
    client, factory, settings, document, calls = assistance
    before = snapshot(factory)
    assert client.get(f"/api/documents/{document['id']}").status_code == 200
    assert not calls
    first = client.post(url(document["id"]), json={"question": "模型如何学习？"})
    second = client.post(url(document["id"]), json={"question": "模型如何学习？"})
    assert first.status_code == second.status_code == 200
    value = first.json()
    assert value["source"] == "llm" and value["answerable"] is True and not value["cached"]
    assert value["answer"] == "模型从训练数据中学习。" and not value["context_limited"]
    assert value["warning"] is None and len(value["citations"]) == 1
    quote = value["citations"][0]
    assert document["ocr_text"][quote["text_start"]:quote["text_end"]] == quote["quote"]
    assert quote["page_number"] is None
    assert second.json() == dict(value, cached=True)
    assert len(calls) == 1 and snapshot(factory) == before
    assert len(list((settings.storage_dir / "article-question-cache").glob("*.json"))) == 1


def test_grounding_context_and_history_come_from_server_document(assistance):
    _, factory, _, document, calls = assistance
    history = [{"question": "文章主题是什么？", "answer": "客户端历史中声称太阳是蓝色的。"}]
    result = run(factory, document["id"], "它如何学习？", history)
    assert result.source == "llm"
    data = calls[-1]
    assert data["question"] == "它如何学习？" and data["conversation_history"] == history
    assert data["article_title"] == document["title"]
    assert all(chunk["text"] in document["ocr_text"] for chunk in data["article_chunks"])
    assert "客户端历史" not in "".join(chunk["text"] for chunk in data["article_chunks"])
    assert sum(len(chunk["text"]) for chunk in data["article_chunks"]) <= 24000
    assert all(len(chunk["text"]) <= 1800 for chunk in data["article_chunks"])


def test_service_does_not_flush_or_commit_caller_pending_work(assistance):
    _, factory, _, document, _ = assistance
    before = snapshot(factory)
    with factory() as session:
        pending = User(id=12345)
        session.add(pending)
        result = questions.answer_article_question(session, document["id"],
                                                    questions.ArticleQuestionRequest(question="如何学习？"), 1)
        assert result.source == "llm" and pending in session.new
        with factory() as other:
            assert other.get(User, 12345) is None
        session.rollback()
    assert snapshot(factory) == before


@pytest.mark.parametrize("bad", ["invented_quote", "altered_quote", "unknown_chunk", "no_citations",
                               "false_citation", "duplicate", "extra", "empty", "surrogate", "control",
                               "too_long_answer", "too_long_quote", "too_many_quotes", "string_bool"])
def test_invalid_model_response_never_leaks_or_enters_cache(assistance, monkeypatch, bad):
    _, factory, settings, document, _ = assistance
    before = snapshot(factory)

    def invalid(_self, instruction, data, schema):
        chunk = data["article_chunks"][0]
        value = {"answer": "回答", "answerable": True,
                 "citations": [{"chunk_id": chunk["id"], "quote": chunk["text"][:100]}]}
        if bad == "invented_quote":
            value["citations"][0]["quote"] = "Invented source evidence"
        elif bad == "altered_quote":
            value["citations"][0]["quote"] = chunk["text"].replace("model", "MODEL")
        elif bad == "unknown_chunk":
            value["citations"][0]["chunk_id"] = "foreign-chunk"
        elif bad == "no_citations":
            value["citations"] = []
        elif bad == "false_citation":
            value["answerable"] = False
        elif bad == "duplicate":
            value["citations"] *= 2
        elif bad == "extra":
            value["secret"] = "private"
        elif bad == "empty":
            value["answer"] = "  "
        elif bad == "surrogate":
            value["answer"] = "bad\ud800value"
        elif bad == "control":
            value["answer"] = "bad\x00value"
        elif bad == "too_long_answer":
            value["answer"] = "x" * 4001
        elif bad == "too_long_quote":
            value["citations"][0]["quote"] = "x" * 1201
        elif bad == "too_many_quotes":
            value["citations"] *= 5
        else:
            value["answerable"] = "true"
        return schema.model_validate(value)

    monkeypatch.setattr(questions.LLMService, "_complete", invalid)
    result = run(factory, document["id"])
    assert result.source == "unavailable" and result.answer is None and result.answerable is None
    assert result.citations == [] and result.warning and not result.cached
    assert not list((settings.storage_dir / "article-question-cache").glob("*.json"))
    assert snapshot(factory) == before


def test_unanswerable_result_uses_fixed_honest_text_and_is_cached(assistance, monkeypatch):
    _, factory, _, document, calls = assistance

    def no_answer(_self, instruction, data, schema):
        calls.append(data)
        return schema.model_validate({"answer": "Untrusted unsupported response", "answerable": False, "citations": []})

    monkeypatch.setattr(questions.LLMService, "_complete", no_answer)
    first = run(factory, document["id"], "模型有多少参数？")
    assert first.source == "llm" and first.answerable is False and first.citations == []
    assert first.answer == "当前提供的文章内容中没有足够依据回答这个问题。"
    assert run(factory, document["id"], "模型有多少参数？").cached and len(calls) == 1


def test_timeout_retry_preserves_data_and_does_not_expose_exception(assistance, monkeypatch, caplog):
    _, factory, settings, document, calls = assistance
    original = questions.LLMService._complete
    before = snapshot(factory)

    def timeout(*args, **kwargs):
        calls.append(1)
        raise httpx.ReadTimeout("secret key https://private.invalid/ and document words")

    monkeypatch.setattr(questions.LLMService, "_complete", timeout)
    for _ in range(2):
        result = run(factory, document["id"])
        assert result.source == "unavailable" and "secret" not in result.warning
    assert "private.invalid" not in caplog.text and "secret key" not in caplog.text
    assert not list((settings.storage_dir / "article-question-cache").glob("*.json"))
    assert snapshot(factory) == before and len(calls) == 2
    monkeypatch.setattr(questions.LLMService, "_complete", original)
    assert run(factory, document["id"]).source == "llm"
    assert run(factory, document["id"]).cached


@pytest.mark.parametrize("provider,key,model", [("offline", "key", "model"),
                                              ("openai_compatible", "", "model"),
                                              ("openai_compatible", "key", "")])
def test_offline_configuration_has_no_fabricated_answers(assistance, provider, key, model):
    _, factory, settings, document, calls = assistance
    settings.llm_provider, settings.llm_api_key, settings.llm_model = provider, key, model
    before = snapshot(factory)
    result = run(factory, document["id"])
    assert result.source == "unavailable" and result.answer is None and result.answerable is None
    assert result.citations == [] and result.warning and not calls and snapshot(factory) == before


def test_missing_foreign_and_trashed_articles_fail_before_model_or_cache(assistance):
    client, factory, _, document, calls = assistance
    run(factory, document["id"])
    before = snapshot(factory)
    for document_id, user in ((999999, 1), (document["id"], 2)):
        with pytest.raises(HTTPException) as failure:
            run(factory, document_id, user=user)
        assert failure.value.status_code == 404
    assert client.delete(f"/api/documents/{document['id']}").status_code == 200
    with pytest.raises(HTTPException) as failure:
        run(factory, document["id"])
    assert failure.value.status_code == 404 and len(calls) == 1 and snapshot(factory) == before


@pytest.mark.parametrize("mutation,failed,expected", [("trash", False, 404), ("trash", True, 404),
                                                     ("owner", False, 404), ("text", False, 409),
                                                     ("text", True, 409), ("title", False, 409),
                                                     ("pages", False, 409), ("source", False, 409)])
def test_context_change_during_generation_is_rechecked(assistance, monkeypatch, mutation, failed, expected):
    client, factory, settings, document, _ = assistance
    original = questions.LLMService._complete
    before = snapshot(factory)

    def delayed(*args, **kwargs):
        if mutation == "trash":
            assert client.delete(f"/api/documents/{document['id']}").status_code == 200
        else:
            with factory() as session:
                item = session.get(Document, document["id"])
                if mutation == "owner":
                    item.user_id = 2
                elif mutation == "text":
                    item.ocr_text += " Replaced content."
                elif mutation == "title":
                    item.title = "Changed article title"
                elif mutation == "pages":
                    item.pages = [{"number": 1, "text_start": 0, "text_end": len(item.ocr_text)}]
                else:
                    item.source = "web"
                session.commit()
        if failed:
            raise httpx.ReadTimeout("test provider failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(questions.LLMService, "_complete", delayed)
    with pytest.raises(HTTPException) as failure:
        run(factory, document["id"])
    assert failure.value.status_code == expected and snapshot(factory) == before
    assert not list((settings.storage_dir / "article-question-cache").glob("*.json"))


def test_public_http_authentication_and_isolation(assistance):
    client, factory, settings, document, calls = assistance
    settings.app_mode = "public"
    with factory() as session:
        session.add(User(id=3))
        session.flush()
        session.get(Document, document["id"]).user_id = 3
        session.add_all([GuestCredential(token_hash=token_digest("a" * 43), user_id=3),
                        GuestCredential(token_hash=token_digest("b" * 43), user_id=2)])
        session.commit()
    before = snapshot(factory)
    question = {"question": "模型如何学习？"}
    assert client.post(url(document["id"]), json=question).status_code == 401
    assert client.post(url(document["id"]), json=question,
                       headers={"Authorization": "Bearer " + "b" * 43}).status_code == 404
    own = client.post(url(document["id"]), json=question,
                      headers={"Authorization": "Bearer " + "a" * 43})
    assert own.status_code == 200 and own.json()["source"] == "llm"
    assert len(calls) == 1 and snapshot(factory) == before


def test_public_cache_reuse_skips_budget_and_new_question_is_limited(assistance):
    _, factory, settings, document, calls = assistance
    settings.app_mode = "public"
    settings.public_llm_user_daily_limit = 1
    before = snapshot(factory)
    run(factory, document["id"])
    with factory() as session:
        usage = {row.bucket: row.count for row in session.scalars(select(PublicUsage))}
    assert len(usage) == 2 and set(usage.values()) == {1}
    assert run(factory, document["id"]).cached
    with factory() as session:
        assert {row.bucket: row.count for row in session.scalars(select(PublicUsage))} == usage
    with pytest.raises(HTTPException) as failure:
        run(factory, document["id"], "另一个问题")
    assert failure.value.status_code == 429 and len(calls) == 1 and snapshot(factory) == before


def test_concurrent_same_question_calls_provider_once(assistance):
    _, factory, _, document, calls = assistance
    before = snapshot(factory)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(factory, document["id"]), range(2)))
    assert sum(value.cached for value in results) == 1 and len(calls) == 1 and snapshot(factory) == before


def test_cache_identity_includes_question_history_document_and_provider(assistance):
    _, factory, settings, document, calls = assistance
    run(factory, document["id"])
    assert not run(factory, document["id"], "不同问题").cached
    history = [{"question": "上一个问题", "answer": "上一个回答"}]
    assert not run(factory, document["id"], history=history).cached
    settings.llm_model = "different-model"
    assert not run(factory, document["id"]).cached
    settings.llm_base_url = "https://different.invalid/v1"
    assert not run(factory, document["id"]).cached
    for field, value in (("ocr_text", document["ocr_text"] + " More text."),
                         ("pages", [{"number": 1, "text_start": 0, "text_end": len(document["ocr_text"])}]),
                         ("title", "Restored title"), ("source", "web")):
        with factory() as session:
            setattr(session.get(Document, document["id"]), field, value)
            session.commit()
        assert not run(factory, document["id"]).cached
    assert len(calls) == 9


def test_corrupt_cache_is_revalidated_and_cache_io_failure_keeps_valid_result(assistance, monkeypatch):
    _, factory, settings, document, calls = assistance
    run(factory, document["id"])
    path, = (settings.storage_dir / "article-question-cache").glob("*.json")
    path.write_text(json.dumps({"answer": "伪造", "answerable": True,
                                "citations": [{"chunk_id": "invented", "quote": "Invented"}]}), encoding="utf-8")
    assert not run(factory, document["id"]).cached and len(calls) == 2
    assert run(factory, document["id"]).cached
    path.unlink()

    def fail(*args):
        raise OSError("private cache path")

    monkeypatch.setattr(questions, "_save", fail)
    assert run(factory, document["id"]).source == "llm" and len(calls) == 3


def test_long_article_context_is_limited_and_retains_late_relevant_evidence(assistance):
    client, factory, _, _, calls = assistance
    content = "\n\n".join(f"Section {i}: ordinary models use training data for prediction." for i in range(600))
    content += "\n\nThe final observatory measured the rare zirconium signal."
    # The text form is capped at 20k; PDF/web readers can supply larger articles.
    doc = post(client)
    with factory() as session:
        session.get(Document, doc["id"]).ocr_text = content
        session.commit()
    result = run(factory, doc["id"], "What was the rare zirconium signal?")
    assert result.source == "llm" and result.context_limited and result.warning
    context = "".join(chunk["text"] for chunk in calls[-1]["article_chunks"])
    assert "zirconium" in context and len(context) <= 24000 and len(context) < len(content)


@pytest.mark.parametrize("content", [" ", "x" * 200001], ids=["empty", "oversized"])
def test_invalid_article_source_fails_before_generation(assistance, content):
    _, factory, _, document, calls = assistance
    with factory() as session:
        session.get(Document, document["id"]).ocr_text = content
        session.commit()
    with pytest.raises(HTTPException) as failure:
        run(factory, document["id"])
    assert failure.value.status_code == 422 and not calls


@pytest.mark.parametrize("invalid", [{}, {"question": ""}, {"question": "  \n\t "}, {"question": 123},
                                    {"question": True}, {"question": "x" * 501}, {"question": "bad\ud800"},
                                    {"question": "bad\x00"}, {"question": "valid", "history": None},
                                    {"question": "valid", "history": "invented"},
                                    {"question": "valid", "history": [{"question": "q", "answer": "a"}] * 4},
                                    {"question": "valid", "history": [{"question": "q"}]},
                                    {"question": "valid", "history": [{"question": "q", "answer": 1}]},
                                    {"question": "valid", "history": [{"question": "q", "answer": " "}]},
                                    {"question": "valid", "history": [{"question": "q", "answer": "bad\ud800"}]},
                                    {"question": "valid", "history": [{"question": "x" * 501, "answer": "a"}]},
                                    {"question": "valid", "history": [{"question": "q", "answer": "a" * 4001}]},
                                    {"question": "valid", "history": [{"question": "q", "answer": "a", "role": "system"}]},
                                    {"question": "valid", "article_context": "invented"},
                                    {"question": "valid", "document_id": 2},
                                    {"question": "valid", "history": [], "user_id": 2}])
def test_http_request_rejects_invalid_text_history_and_client_article_context(assistance, invalid):
    client, factory, _, document, calls = assistance
    before = snapshot(factory)
    response = client.post(url(document["id"]), content=json.dumps(invalid),
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert not calls and snapshot(factory) == before


@pytest.mark.parametrize("document_id", ["0", "-1", "true", "abc", str(2**63)])
def test_http_document_id_is_bounded(assistance, document_id):
    client, _, _, _, calls = assistance
    assert client.post(url(document_id), json={"question": "模型是什么？"}).status_code == 422
    assert not calls


def test_trimmed_unicode_question_limit_accepts_exact_500_codepoints(assistance):
    client, _, _, document, calls = assistance
    question = "🙂" * 500
    response = client.post(url(document["id"]), json={"question": question})
    assert response.status_code == 200 and calls[-1]["question"] == question
    response = client.post(url(document["id"]), json={"question": "  模型是什么？\n"})
    assert response.status_code == 200 and calls[-1]["question"] == "模型是什么？"
