"""Draft text must come from real approved JSON fields, including split escapes."""
import json

import pytest

from backend.services.progressive_json import progressive_text


def test_dictionary_preview_arrives_before_json_or_summary_finishes():
    prefix = '{"recognized":true,"detail":{"word":"read","summary":"'
    text = "阅读，也可以表示理解文字。"
    for length in range(len(text) + 1):
        assert progressive_text(prefix + text[:length], "dictionary") == text[:length]
    assert progressive_text(prefix + text + '","senses":[', "dictionary") == text


def test_assistant_top_level_answer_only_and_key_looking_strings_are_not_fields():
    assert progressive_text('{"reasoning":"先思考答案","nested":{"answer":"不能展示"},"answer":"收到的回答',
                            "assistant") == "收到的回答"
    assert progressive_text('{"nested":{"answer":"不能展示"}}', "assistant") == ""
    assert progressive_text(json.dumps({"reasoning": '"answer":"不能展示"'}), "assistant") == ""
    assert progressive_text('{"answer":"正常回答","quiz":{"explanation":"不能展示"}}', "assistant") == "正常回答"


@pytest.mark.parametrize("content", [
    '{"recognized":false,"detail":{"summary":"不能编造"}}',
    '{"recognized":"true","detail":{"summary":"不能编造"}}',
    '{"detail":{"summary":"尚未确认"}',
    '{"recognized":1,"detail":{"summary":"尚未确认"}}',
    '{"recognized":true,"summary":"不是详情里的字段"}',
    '{"recognized":true,"detail":{"nested":{"summary":"嵌套伪字段"}}}',
])
def test_dictionary_requires_literal_recognition_and_exact_path(content):
    assert progressive_text(content, "dictionary") == ""


def test_dictionary_can_receive_fields_in_other_order_without_exposing_unrecognized_words():
    prefix = '{"detail":{"summary":"可靠释义"},"recognized":'
    assert progressive_text(prefix, "dictionary") == ""
    assert progressive_text(prefix + "false}", "dictionary") == ""
    assert progressive_text(prefix + "true}", "dictionary") == "可靠释义"


def test_unicode_escapes_and_surrogate_pair_are_held_until_complete():
    prefix = '{"answer":"已经收到'
    assert progressive_text(prefix + "\\", "assistant") == "已经收到"
    assert progressive_text(prefix + "\\u4e", "assistant") == "已经收到"
    assert progressive_text(prefix + "\\u4e2d", "assistant") == "已经收到中"
    for suffix in ("\\ud83d", "\\ud83d\\", "\\ud83d\\u", "\\ud83d\\ude", "\\ud83d\\ude0"):
        assert progressive_text(prefix + suffix, "assistant") == "已经收到"
    assert progressive_text(prefix + "\\ud83d\\ude00", "assistant") == "已经收到😀"
    assert progressive_text(prefix + '\\n新的一行\\t制表\\"引号\\\\斜线', "assistant") == '已经收到\n新的一行\t制表"引号\\斜线'


def test_every_chunk_boundary_in_unicode_encoded_json_returns_only_a_received_text_prefix():
    text = "中文回答😀，包含一个引号\"和换行\n继续。"
    encoded = json.dumps({"answer": text, "quiz": None}, ensure_ascii=True)
    observed = [progressive_text(encoded[:index], "assistant") for index in range(len(encoded) + 1)]
    assert observed[-1] == text
    assert all(text.startswith(value) for value in observed)
    assert all(right.startswith(left) for left, right in zip(observed, observed[1:]))
    assert any(value and value != text for value in observed)


@pytest.mark.parametrize("content", [
    '{"answer":"先收到","answer":"重复键"}',
    '{"answer":"先收到",}',
    '{"answer":"先收到"} unexpected',
    '{"answer":"含有\\u0000控制符"}',
    '{"answer":"含有\\r回车"}',
    '{"answer":"含有\\ud800孤立代理"}',
    '{"answer":"含有\\udc00孤立代理"}',
    '{"answer":"含有\\ud800\\u0041错误代理"}',
    '{"answer":"错误\\x转义"}',
    '[{"answer":"错误根结构"}]',
    '```json\n{"answer":"不能展示代码围栏"}',
])
def test_invalid_json_or_unsafe_text_is_not_repaired_or_rendered(content):
    assert progressive_text(content, "assistant") == ""


def test_preview_lengths_and_input_depth_are_bounded():
    assert len(progressive_text('{"answer":"' + "答" * 4000, "assistant")) == 3000
    assert len(progressive_text('{"recognized":true,"detail":{"summary":"' + "词" * 400,
                                "dictionary")) == 300
    assert progressive_text('{"answer":"' + "答" * 30_000, "assistant") == ""
    assert progressive_text('{"answer":"已收到","nested":' + "[" * 40, "assistant") == ""
