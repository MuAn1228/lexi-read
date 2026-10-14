"""Run actual CPU PaddleOCR on the bundled English image, never a mock.

From the project root: python scripts/ocr_smoke.py
The first run downloads models; cached models are reused on later runs.
"""

from dataclasses import asdict
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.ocr.service import OCRService  # noqa: E402

EXPECTED = [
    "A curious student reads every day.",
    "Reading helps us discover a better world.",
    "Practice makes difficult words feel familiar.",
]


def main() -> None:
    service = OCRService()
    timings = []
    for _ in range(2):
        started = time.perf_counter()
        result = service.recognize(ROOT / "samples" / "reading.png")
        timings.append(round(time.perf_counter() - started, 3))
        assert result.text.splitlines() == EXPECTED, result.text
        assert len(result.words) == 19
        assert {w.line for w in result.words} == {0, 1, 2}
        assert all(w.bbox_source == "ocr" for w in result.words)
        assert all(0 <= w.bbox[0] < w.bbox[2] <= result.width and
                   0 <= w.bbox[1] < w.bbox[3] <= result.height for w in result.words)
        assert all(0.0 <= w.confidence <= 1.0 for w in result.words)
    print(json.dumps({**asdict(result), "first_run_seconds": timings[0],
                      "warm_run_seconds": timings[1]}, indent=2))


if __name__ == "__main__":
    main()
