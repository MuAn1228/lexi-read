from types import SimpleNamespace

import pytest

from backend.services.article_context import (CHUNK_CHARS, CONTEXT_CHARS,
                                               prepare_article_context, resolve_article_quote)


def document(text, *, source="text", pages=None):
    return SimpleNamespace(ocr_text=text, source=source, pages=pages)


def test_short_article_is_complete_and_exact_with_unicode_and_whitespace():
    content = "🙂 A model learns.\n\n  We\tlearned from data.\n"
    context = prepare_article_context(document(content), "What did we learn?")
    assert not context.limited
    assert context.total_chars == context.selected_chars == len(content)
    assert "".join(chunk.text for chunk in context.chunks) == content
    quote = "We\tlearned from data."
    citation = resolve_article_quote(context, context.chunks[0].id, quote)
    assert content[citation["text_start"]:citation["text_end"]] == quote
    assert citation["text_start"] == content.index("We")
    assert citation["page_number"] is None


def test_repeated_quote_is_bound_to_selected_chunk_not_first_in_document():
    content = "A repeated fact. " * 250
    context = prepare_article_context(document(content), "fact")
    second = context.chunks[1]
    result = resolve_article_quote(context, second.id, "repeated fact.")
    assert result["text_start"] >= second.text_start
    assert content[result["text_start"]:result["text_end"]] == "repeated fact."


def test_pdf_chunks_never_cross_page_and_keep_original_page_numbers():
    texts = ["First page without punctuation", "", "🙂 Third page explains a discovery."]
    content = "\n\n".join(texts)
    cursor, pages = 0, []
    for index, text in enumerate(texts, 1):
        pages.append({"number": index, "text_start": cursor, "text_end": cursor + len(text)})
        cursor += len(text) + 2
    context = prepare_article_context(document(content, source="pdf", pages=pages), "discovery")
    assert not context.limited
    assert [chunk.page_number for chunk in context.chunks] == [1, 3]
    citation = resolve_article_quote(context, context.chunks[-1].id, texts[-1])
    assert citation["page_number"] == 3
    assert content[citation["text_start"]:citation["text_end"]] == texts[-1]


def long_article():
    return "\n\n".join(f"Section {index}. " + ("Readers consider ordinary ideas. " * 65)
                       + ("The unique quasar signal was detected here." if index == 27 else "")
                       for index in range(40))


def test_long_article_selects_later_keyword_evidence_and_is_bounded_in_order():
    content = long_article()
    context = prepare_article_context(document(content), "Where was the quasar signal detected?")
    assert context.limited
    assert context.selected_chars <= CONTEXT_CHARS < context.total_chars
    assert any("unique quasar signal" in chunk.text for chunk in context.chunks)
    assert all(len(chunk.text) <= CHUNK_CHARS for chunk in context.chunks)
    assert [chunk.text_start for chunk in context.chunks] == sorted(chunk.text_start for chunk in context.chunks)
    assert all(content[chunk.text_start:chunk.text_end] == chunk.text for chunk in context.chunks)
    assert context == prepare_article_context(document(content), "Where was the quasar signal detected?")


def test_prior_question_can_retrieve_relevant_context_for_followup():
    context = prepare_article_context(document(long_article()), "为什么？", ["What is the quasar signal?"])
    assert any("unique quasar signal" in chunk.text for chunk in context.chunks)


def test_chinese_question_uses_spread_out_excerpts_and_admits_limited_coverage():
    content = long_article()
    context = prepare_article_context(document(content), "这篇文章的主要观点是什么？")
    assert context.limited and context.selected_chars <= CONTEXT_CHARS
    assert context.chunks[0].text_start == 0
    assert context.chunks[-1].text_end == len(content)
    assert len(context.chunks) > 8


@pytest.mark.parametrize("quote", ["", " \n\t", "A model learns!", "A model  learns.", "x" * 1201],
                         ids=["empty", "whitespace", "invented", "changed-spacing", "too-long"])
def test_invalid_or_invented_quotes_are_rejected(quote):
    context = prepare_article_context(document("A model learns."), "What learns?")
    with pytest.raises(ValueError):
        resolve_article_quote(context, context.chunks[0].id, quote)


def test_unknown_or_unsent_chunk_cannot_supply_evidence():
    context = prepare_article_context(document(long_article()), "quasar")
    with pytest.raises(ValueError):
        resolve_article_quote(context, "chunk-999999", "A sentence.")


@pytest.mark.parametrize("content", ["", " \n\t", "x" * 200001], ids=["empty", "whitespace", "oversized"])
def test_empty_or_oversized_article_is_rejected(content):
    with pytest.raises(ValueError):
        prepare_article_context(document(content), "summary")


@pytest.mark.parametrize("pages", [None, [], [{"number": 1, "text_start": True, "text_end": 4}],
                                  [{"number": 1, "text_start": 0, "text_end": 99}],
                                  [{"number": 1, "text_start": 2, "text_end": 4}],
                                  [{"number": 1, "text_start": 0, "text_end": 2}],
                                  [{"number": 2, "text_start": 0, "text_end": 4}],
                                  [{"number": 1, "text_start": 0, "text_end": 4},
                                   {"number": 2, "text_start": 2, "text_end": 4}]])
def test_bad_pdf_boundaries_never_guess_or_cross_pages(pages):
    with pytest.raises(ValueError):
        prepare_article_context(document("Test", source="pdf", pages=pages), "summary")
