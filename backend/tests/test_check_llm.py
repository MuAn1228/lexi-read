"""The connection check must distinguish real validated responses from fallback."""
import json

import httpx
import pytest
from pydantic import ValidationError

from backend.config import Settings
from scripts import check_llm


@pytest.fixture(params=[True, False], ids=["json-mode", "prompt-json"])
def configured(monkeypatch, request):
    settings = Settings(llm_provider="openai_compatible", llm_model="agnes-3.0-flash",
                        llm_api_key="secret-test-key", llm_base_url="https://apihub.agnes-ai.com/v1/",
                        llm_json_mode=request.param, llm_max_tokens=2048,
                        _env_file=None)
    monkeypatch.setattr(check_llm, "Settings", lambda: settings)
    return settings


def mock_transport(monkeypatch, handler):
    client = httpx.Client
    monkeypatch.setattr("backend.services.llm.httpx.Client",
                        lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))


def explanation():
    return {"word": "curious", "lemma": "curious", "meaning_in_context": "好奇的",
            "simple_explanation": "这里指学生想了解植物如何生长。",
            "part_of_speech": "adjective", "importance": .8}


def candidates():
    return {"candidates": [
        {"word": word, "meaning": "test meaning", "reason": "test reason", "relation": "concept",
         "semantic_relevance": .9, "context_relevance": .9, "learning_value": .8}
        for word in ("plant growth", "garden", "sunlight", "water")
    ]}


def response(data):
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(data)},
                                                "finish_reason": "stop"}]})


def test_success_validates_both_responses_and_applies_three_word_budget(configured, monkeypatch, capsys):
    requests = []

    def handler(request):
        data = json.loads(request.content)
        assert str(request.url) == "https://apihub.agnes-ai.com/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer secret-test-key"
        assert request.headers["content-type"] == "application/json"
        assert data["model"] == "agnes-3.0-flash"
        assert data["max_tokens"] == 2048
        if configured.llm_json_mode:
            assert data["response_format"] == {"type": "json_object"}
        else:
            assert "response_format" not in data
        reading = json.loads(data["messages"][1]["content"])
        assert reading["current_word"] == reading["lemma"] == "curious"
        assert reading["current_sentence"] == check_llm.SENTENCE
        assert reading["document_context"] == check_llm.ARTICLE
        assert reading["recent_learning_events"] == []
        assert reading["known_words"] == reading["unknown_words"] == reading["uncertain_words"] == []
        requests.append(data)
        return response(explanation() if len(requests) == 1 else candidates())

    mock_transport(monkeypatch, handler)
    assert check_llm.main([]) == 0
    output = capsys.readouterr()
    assert len(requests) == 2
    assert "source=llm" in output.out and "3 related words" in output.out
    assert not output.err
    assert configured.llm_api_key not in output.out


@pytest.mark.parametrize("overrides,category", [
    ({"llm_provider": "offline"}, "offline"),
    ({"llm_provider": "unsupported"}, "configuration"),
    ({"llm_api_key": ""}, "configuration"),
    ({"llm_model": " "}, "configuration"),
])
def test_invalid_configuration_never_contacts_provider(configured, monkeypatch, capsys, overrides, category):
    for name, value in overrides.items():
        setattr(configured, name, value)
    requests = []
    mock_transport(monkeypatch, lambda request: requests.append(request))
    assert check_llm.main([]) == 2
    assert f"FAIL [{category}]" in capsys.readouterr().err
    assert requests == []


@pytest.mark.parametrize("failed_stage", ["explanation", "recommendations"])
@pytest.mark.parametrize("failure", ["auth", "network", "invalid_json", "invalid_schema", "truncated"])
def test_provider_failures_are_not_reported_as_success_or_leaked(
        configured, monkeypatch, capsys, caplog, failed_stage, failure):
    requests = []
    sensitive = "secret-test-key https://user:password@provider.invalid/raw-provider-body"

    def handler(request):
        requests.append(request)
        if failed_stage == "recommendations" and len(requests) == 1:
            return response(explanation())
        if failure == "auth":
            return httpx.Response(401, text=sensitive)
        if failure == "network":
            raise httpx.ConnectError(sensitive, request=request)
        if failure == "invalid_json":
            return httpx.Response(200, json={"choices": [{"message": {"content": sensitive}}]})
        if failure == "truncated":
            data = explanation() if failed_stage == "explanation" else candidates()
            return httpx.Response(200, json={"choices": [{
                "message": {"content": json.dumps(data)}, "finish_reason": "length"}]})
        return response({"unexpected": sensitive})

    mock_transport(monkeypatch, handler)
    assert check_llm.main([]) == 1
    output = capsys.readouterr()
    assert "FAIL [fallback]" in output.err
    assert failed_stage in output.err.lower()
    assert "PASS" not in output.out
    assert len(requests) == (1 if failed_stage == "explanation" else 2)
    for secret in ("secret-test-key", "user:password", "raw-provider-body"):
        assert secret not in output.out + output.err + caplog.text


def test_settings_errors_do_not_print_exception_or_secret(monkeypatch, capsys):
    def invalid_settings():
        raise ValueError("secret-setting-value")

    monkeypatch.setattr(check_llm, "Settings", invalid_settings)
    assert check_llm.main([]) == 2
    output = capsys.readouterr()
    assert "FAIL [configuration]" in output.err
    assert "secret-setting-value" not in output.out + output.err


@pytest.mark.parametrize("max_tokens", [0, -1, 1.5])
def test_invalid_token_budget_is_rejected(max_tokens):
    with pytest.raises(ValidationError):
        Settings(llm_max_tokens=max_tokens, _env_file=None)
