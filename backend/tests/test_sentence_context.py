"""Small context windows must preserve the selected sentence's actual location."""
from types import SimpleNamespace

import pytest

from backend.services.sentences import _sentence_context


def context(text, sentence, offset=None, start=0, end=None):
    return _sentence_context(SimpleNamespace(ocr_text=text),
                             SimpleNamespace(sentence=sentence, text_start=offset),
                             start, len(text) if end is None else end)


def test_short_article_is_preserved_verbatim():
    text = "🙂中文\nMia reads.\n\nShe learns."
    assert context(text, "Mia reads.", text.index("Mia")) == text


def test_sentence_beyond_old_page_prefix_is_included_with_local_evidence():
    sentence = "Mia hesitated whenever she met an unfamiliar expression."
    text = "Unrelated introduction. " * 700 + "The library closes soon. " + sentence + " Her friend waited. " * 100
    excerpt = context(text, sentence, text.index("hesitated"))
    assert len(excerpt) == 2400 and excerpt in text
    assert sentence in excerpt and "The library closes soon." in excerpt and "Her friend waited." in excerpt
    assert excerpt != text[:2400]


def test_identical_sentences_use_the_selected_occurrence_not_the_first_match():
    sentence = "She sat by the bank."
    text = sentence + " First context. " * 300 + "River context. " * 30 + sentence + "Water context. " * 100
    excerpt = context(text, sentence, text.rfind(sentence) + 12)
    assert sentence in excerpt and "River context." in excerpt and "Water context." in excerpt
    assert not excerpt.startswith(sentence)


def test_pdf_context_never_includes_adjacent_pages_and_keeps_unicode_offsets():
    page_one = "SECRET FROM ANOTHER PAGE. " * 100
    page_two = "🙂中文 " * 1000 + "The library was quiet. " + "🙂绿色 " * 100
    text = page_one + page_two + "DO NOT INCLUDE THE NEXT PAGE."
    excerpt = context(text, "The library was quiet.", text.index("library"), len(page_one), len(page_one + page_two))
    assert "The library was quiet." in excerpt
    assert len(excerpt) == 2400 and excerpt in page_two
    assert "SECRET" not in excerpt and "NEXT PAGE" not in excerpt


def test_long_sentence_is_never_cut_and_window_stays_bounded():
    sentence = "Long sentence " + "word " * 700 + "."
    text = "Before. " * 500 + sentence + " After. " * 500
    excerpt = context(text, sentence, text.index(sentence) + 20)
    assert excerpt == sentence and len(excerpt) < 4000


@pytest.mark.parametrize("offset", [None, -1, 999999])
def test_missing_legacy_ocr_offsets_still_find_the_sentence(offset):
    sentence = "The final sentence is here."
    text = "Long unrelated page. " * 500 + sentence
    assert sentence in context(text, sentence, offset)


def test_unknown_ocr_sentence_falls_back_to_a_bounded_local_window():
    text = "Beginning. " * 500 + "Actual selected location. " + "Ending. " * 300
    excerpt = context(text, "A legacy OCR sentence no longer in the text.", text.index("Actual"))
    assert "Actual selected location." in excerpt and excerpt in text and len(excerpt) == 2400
