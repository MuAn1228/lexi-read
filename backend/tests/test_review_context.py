from copy import deepcopy
from types import SimpleNamespace

import pytest

from backend.services.review_context import review_target_range
from backend.services.text_import import text_occurrences


def doc(content, source="text", pages=None):
    return SimpleNamespace(ocr_text=content, source=source, pages=pages)


@pytest.mark.parametrize("content", [
    "Model model model.",
    "  \tModel\n\nmodel\t  model.  ",
    "中文🙂 Model model model.",
])
def test_repeated_word_uses_exact_import_anchor(content):
    document = doc(content)
    words = [word for word in text_occurrences(content) if word.word == "model"]
    ranges = [review_target_range(document, word) for word in words]
    assert all(span is not None for span in ranges)
    assert len(set(ranges)) == len(words)
    for word, span in zip(words, ranges):
        assert word.sentence[slice(*span)] == word.word


def test_codepoints_and_original_inflection_not_lemma():
    content = "🙂 中文 We learned\n  useful things."
    occurrence = next(word for word in text_occurrences(content) if word.word == "learned")
    assert occurrence.lemma == "learn"
    start, end = review_target_range(doc(content), occurrence)
    assert start == len("🙂 中文 We ")
    assert occurrence.sentence[start:end] == "learned"


def test_pdf_sentence_mapping_stays_inside_page_even_without_punctuation():
    first, second = "Model learns", "🙂 A model learns"
    content = first + "\n\n" + second
    second_start = len(first) + 2
    pages = [{"text_start": 0, "text_end": len(first)},
             {"text_start": second_start, "text_end": len(content)}]
    occurrence = next(word for word in text_occurrences(second) if word.word == "model")
    occurrence.text_start += second_start
    occurrence.text_end += second_start
    assert review_target_range(doc(content, "pdf", pages), occurrence) == (4, 9)
    assert review_target_range(doc(content, "pdf", []), occurrence) is None


@pytest.mark.parametrize("start,end,sentence", [
    (True, 5, "model models."), (0, False, "model models."),
    (-1, 5, "model models."), (0, 99, "model models."),
    (None, 5, "model models."), (0, None, "model models."),
    (0, 5, "A different model."), (6, 11, "model models."),
    (2, 7, "model models."),
])
def test_invalid_authoritative_offsets_are_not_guessed(start, end, sentence):
    occurrence = SimpleNamespace(word="model", sentence=sentence, text_start=start, text_end=end)
    assert review_target_range(doc("model models."), occurrence) is None


@pytest.mark.parametrize("sentence,word,expected", [
    ("A model works.", "model", (2, 7)),
    ("A model meets a model.", "model", None),
    ("Several models work.", "model", None),
    ("A model-based system.", "model", None),
    ("A Model works.", "model", None),
    ("We learned this.", "learned", (3, 10)),
    ("It's helpful.", "It's", (0, 4)),
])
def test_ocr_only_accepts_unique_exact_complete_surface(sentence, word, expected):
    occurrence = SimpleNamespace(word=word, sentence=sentence, text_start=None, text_end=None)
    assert review_target_range(doc(sentence, "upload"), occurrence) == expected


@pytest.mark.parametrize("source", ["text", "web", "pdf"])
def test_missing_text_anchors_do_not_become_guessed_ocr_ranges(source):
    occurrence = SimpleNamespace(word="model", sentence="A model.", text_start=None, text_end=None)
    assert review_target_range(doc("A model.", source), occurrence) is None


def test_mapping_does_not_mutate_inputs():
    document = doc("A model.")
    occurrence = text_occurrences(document.ocr_text)[1]
    before_document = deepcopy(vars(document))
    before_occurrence = dict(vars(occurrence))
    assert review_target_range(document, occurrence) == (2, 7)
    assert vars(document) == before_document
    assert vars(occurrence) == before_occurrence
