"""Lazy, local OCR adapters with image-coordinate word boxes."""

from .service import OCRResult, OCRService, OCRUnavailable, OCRWord, get_ocr_service

__all__ = ["OCRResult", "OCRService", "OCRUnavailable", "OCRWord", "get_ocr_service"]
