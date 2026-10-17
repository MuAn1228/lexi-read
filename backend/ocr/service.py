"""PaddleOCR 3.x adapter; importing this module never downloads models.

The recognizer supplies word geometry and *line-level* confidence. Fallback
line splits are explicitly marked ``estimated`` and must not be presented as
precise character alignment. All boxes use original-image pixel coordinates.
"""

from __future__ import annotations

import math
import os
import re
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image

from backend.config import get_settings

ENGLISH_WORD = re.compile(r"[A-Za-z]+(?:['’\-][A-Za-z]+)*")


@dataclass(frozen=True)
class OCRWord:
    word: str
    bbox: list[int]
    line: int
    confidence: float
    bbox_source: str = "ocr"


@dataclass(frozen=True)
class OCRResult:
    text: str
    width: int
    height: int
    words: list[OCRWord]


class OCRUnavailable(RuntimeError):
    """Actionable OCR setup/inference error, suitable for an HTTP 503."""


def _plain(value: Any) -> Any:
    return value.tolist() if hasattr(value, "tolist") else value


def _box(value: Any, width: int, height: int) -> list[int]:
    """Accept xyxy or a polygon; return an axis-aligned, clipped rectangle."""
    value = _plain(value)
    if value and isinstance(value[0], (list, tuple)):
        xs, ys = zip(*value)
        value = [min(xs), min(ys), max(xs), max(ys)]
    if len(value) != 4 or not all(math.isfinite(float(n)) for n in value):
        raise ValueError("OCR returned an invalid bounding box")
    x0, y0, x1, y1 = map(float, value)
    return [max(0, min(width, math.floor(x0))), max(0, min(height, math.floor(y0))),
            max(0, min(width, math.ceil(x1))), max(0, min(height, math.ceil(y1)))]


def _split_line(text: str, box: list[int], line: int, confidence: float) -> list[OCRWord]:
    """Character-proportional approximation used only without word alignment."""
    x0, y0, x1, y1 = box
    span = max(1, len(text))
    result = []
    for match in ENGLISH_WORD.finditer(text):
        left = x0 + (x1 - x0) * match.start() / span
        right = x0 + (x1 - x0) * match.end() / span
        result.append(OCRWord(match.group(), [math.floor(left), y0, math.ceil(right), y1],
                              line, confidence, "estimated"))
    return result


def parse_paddle_results(results: Any, width: int, height: int) -> OCRResult:
    """Parse PaddleX 3.x output, including native ``text_word_boxes``."""
    text_lines: list[str] = []
    words: list[OCRWord] = []
    for page in results:
        # PaddleX OCRResult is dict-like; saved JSON may wrap it in `res`.
        if "res" in page and "rec_texts" not in page:
            page = page["res"]
        texts = page.get("rec_texts", [])
        scores = page.get("rec_scores", [])
        line_boxes = page.get("rec_boxes", page.get("rec_polys", []))
        word_texts = page.get("text_word", [])
        word_boxes = page.get("text_word_boxes", page.get("text_word_region", []))
        for index, raw_text in enumerate(texts):
            text = str(raw_text).strip()
            if not text:
                continue
            line = len(text_lines)
            text_lines.append(text)
            confidence = float(scores[index]) if index < len(scores) else 0.0
            confidence = max(0.0, min(1.0, confidence)) if math.isfinite(confidence) else 0.0
            native_text = _plain(word_texts[index]) if index < len(word_texts) else []
            native_boxes = _plain(word_boxes[index]) if index < len(word_boxes) else []
            if native_text and native_boxes and len(native_text) == len(native_boxes):
                for token, coords in zip(native_text, native_boxes):
                    token = str(token)
                    matches = list(ENGLISH_WORD.finditer(token))
                    if not matches:
                        continue
                    box = _box(coords, width, height)
                    if box[2] <= box[0] or box[3] <= box[1]:
                        continue
                    if len(matches) == 1:
                        words.append(OCRWord(matches[0].group(), box, line, confidence, "ocr"))
                    else:
                        words.extend(_split_line(token, box, line, confidence))
            elif index < len(line_boxes):
                box = _box(line_boxes[index], width, height)
                if box[2] > box[0] and box[3] > box[1]:
                    words.extend(_split_line(text, box, line, confidence))
    return OCRResult("\n".join(text_lines), width, height, words)


class OCRService:
    """One lazy CPU engine per process; serialize non-thread-safe inference."""

    def __init__(self, provider: str = "paddle") -> None:
        self.provider = provider.lower()
        self._engine: Any = None
        self._lock = threading.Lock()

    def _load_engine(self) -> None:
        if self._engine is not None:
            return
        if self.provider != "paddle":
            raise OCRUnavailable("Unsupported OCR_PROVIDER. Set OCR_PROVIDER=paddle.")
        settings = get_settings()
        cache = settings.storage_dir / "models"
        cache.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(cache))
        # Cached models work offline; avoid probing every hosting service at import.
        # Missing models are still downloaded by PaddleX's normal host fallback.
        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        try:
            import cv2
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise OCRUnavailable(
                "PaddleOCR is not installed. Run: python -m pip install -r requirements-ocr.txt"
            ) from exc
        try:
            # OpenCV otherwise starts one worker per logical CPU in addition to
            # Paddle's workers. Bound preprocessing resources on desktop hosts.
            cv2.setNumThreads(settings.ocr_cpu_threads)
            self._engine = PaddleOCR(
                text_detection_model_name="PP-OCRv5_mobile_det",
                text_recognition_model_name="en_PP-OCRv5_mobile_rec",
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                return_word_box=True,
                device="cpu",
                cpu_threads=settings.ocr_cpu_threads,
                enable_mkldnn=False,
            )
        except Exception as exc:
            raise OCRUnavailable(
                "PaddleOCR could not initialize. On the first upload, internet access is "
                "required to download its models. Check backend logs and retry."
            ) from exc

    def recognize(self, path: Path) -> OCRResult:
        with Image.open(path) as image:
            width, height = image.size
        with self._lock:
            self._load_engine()
            try:
                results = self._engine.predict(str(path), return_word_box=True)
                return parse_paddle_results(results, width, height)
            except Exception as exc:
                raise OCRUnavailable(
                    "OCR inference failed. Check backend logs; try a clear, upright JPG or PNG."
                ) from exc


@lru_cache(maxsize=1)
def get_ocr_service() -> OCRService:
    return OCRService(get_settings().ocr_provider)
