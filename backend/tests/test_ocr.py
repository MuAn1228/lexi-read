"""Output-contract tests; no model download needed. Real inference: scripts/ocr_smoke.py."""

import math
import sys
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from backend.config import Settings
from backend.ocr.service import OCRService, OCRUnavailable, parse_paddle_results


@pytest.mark.parametrize("threads", [1, 2, 8])
def test_ocr_cpu_budget_reaches_both_engines_without_eager_download(monkeypatch, tmp_path, threads):
    configured = Settings(data_dir=tmp_path, ocr_cpu_threads=threads, _env_file=None)
    monkeypatch.setattr("backend.ocr.service.get_settings", lambda: configured)
    cv_threads, paddle_options = [], []
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(setNumThreads=cv_threads.append))
    def create_engine(**options):
        paddle_options.append(options)
        return object()
    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=create_engine))
    service = OCRService()
    assert paddle_options == []
    service._load_engine()
    service._load_engine()
    assert cv_threads == [threads]
    assert len(paddle_options) == 1
    assert paddle_options[0]["cpu_threads"] == threads
    assert paddle_options[0]["device"] == "cpu"


@pytest.mark.parametrize("threads", [0, 9, 1.5])
def test_invalid_cpu_budget_is_rejected(threads):
    with pytest.raises(ValidationError):
        Settings(ocr_cpu_threads=threads, _env_file=None)


def test_native_words_use_ocr_geometry_line_confidence_and_clip_to_image():
    result = parse_paddle_results([{
        "rec_texts": ["Hello, world!", "Again"],
        "rec_scores": [0.91, 1.5],
        "text_word": [["Hello,", "world!"], ["Again"]],
        "text_word_boxes": [[[-3.2, -2, 31.1, 17.5], [40, 1, 105, 18]],
                            [[2, 20, 35, 60]]],
    }], 100, 40)
    assert result.text == "Hello, world!\nAgain"
    assert (result.width, result.height) == (100, 40)
    assert [w.word for w in result.words] == ["Hello", "world", "Again"]
    assert [w.bbox for w in result.words] == [[0, 0, 32, 18], [40, 1, 100, 18], [2, 20, 35, 40]]
    assert [w.line for w in result.words] == [0, 0, 1]
    assert [w.confidence for w in result.words] == [0.91, 0.91, 1.0]
    assert {w.bbox_source for w in result.words} == {"ocr"}


def test_polygon_words_saved_json_and_invalid_confidence():
    result = parse_paddle_results([{"res": {
        "rec_texts": ["book"], "rec_scores": [math.nan],
        "text_word": [["book"]],
        "text_word_region": [[[[1.5, 2], [21.5, 1], [22, 11], [2, 12]]]],
    }}], 30, 20)
    assert result.words[0].bbox == [1, 1, 22, 12]
    assert result.words[0].confidence == 0
    assert result.words[0].bbox_source == "ocr"


def test_line_fallback_is_explicitly_estimated_and_keeps_apostrophes():
    result = parse_paddle_results([{
        "rec_texts": [" ", "Don't re-read 123."],
        "rec_scores": [0, 0.8],
        "rec_boxes": [[0, 0, 1, 1], [0, 10, 180, 30]],
    }], 200, 100)
    assert result.text == "Don't re-read 123."
    assert [w.word for w in result.words] == ["Don't", "re-read"]
    assert [w.bbox for w in result.words] == [[0, 10, 50, 30], [60, 10, 130, 30]]
    assert {w.bbox_source for w in result.words} == {"estimated"}
    assert {w.line for w in result.words} == {0}


def test_mismatched_native_arrays_use_line_fallback_instead_of_dropping_words():
    result = parse_paddle_results([{
        "rec_texts": ["one two"], "text_word": [["one", "two"]],
        "text_word_boxes": [[[0, 0, 30, 20]]], "rec_boxes": [[0, 0, 70, 20]],
    }], 100, 30)
    assert [w.word for w in result.words] == ["one", "two"]
    assert {w.bbox_source for w in result.words} == {"estimated"}


def test_compound_native_token_is_split_but_marked_estimated():
    result = parse_paddle_results([{
        "rec_texts": ["one/two"], "text_word": [["one/two"]],
        "text_word_boxes": [[[0, 0, 70, 20]]],
    }], 100, 30)
    assert [w.word for w in result.words] == ["one", "two"]
    assert [w.bbox for w in result.words] == [[0, 0, 30, 20], [40, 0, 70, 20]]
    assert {w.bbox_source for w in result.words} == {"estimated"}


def test_numbers_empty_and_zero_area_boxes_produce_no_words():
    result = parse_paddle_results([{
        "rec_texts": ["123 empty"], "text_word": [["123", "empty"]],
        "text_word_boxes": [[[0, 0, 20, 20], [110, 0, 150, 20]]],
    }], 100, 30)
    assert result.words == []
    assert parse_paddle_results([], 100, 30).text == ""


def test_invalid_coordinates_fail_instead_of_saving_corrupt_geometry():
    with pytest.raises(ValueError, match="invalid bounding box"):
        parse_paddle_results([{
            "rec_texts": ["word"], "rec_boxes": [[0, 0, math.inf, 30]],
        }], 100, 100)


def test_unsupported_provider_has_actionable_error_without_importing_paddle():
    with pytest.raises(OCRUnavailable, match="OCR_PROVIDER=paddle"):
        OCRService("unknown")._load_engine()
