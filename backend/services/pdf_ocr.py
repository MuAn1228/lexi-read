"""Bounded, temporary PDF rasterization feeding the existing local OCR engine.

PDFium is loaded only for pages that need OCR. Its complete object lifecycle is
serialized: even independent PDFium documents are not safe on multiple threads.
"""
from contextlib import contextmanager
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock

from fastapi import HTTPException
from backend.ocr.service import OCRUnavailable, get_ocr_service


MAX_RENDER_EDGE = 2200
MAX_RENDER_PIXELS = 4_000_000
MAX_OCR_CHARACTERS = 200_000
_pdfium_lock = Lock()


def render_scale(width: float, height: float) -> float:
    """Fit at most 180 dpi inside both memory bounds before allocating pixels."""
    if not all(math.isfinite(value) and value > 0 for value in (width, height)):
        raise ValueError("Invalid PDF page dimensions")
    # One-pixel margin accounts for the renderer rounding each dimension up.
    return min(2.5, (MAX_RENDER_EDGE - 1) / max(width, height),
               math.sqrt((MAX_RENDER_PIXELS - 2 * MAX_RENDER_EDGE) / width / height))


@contextmanager
def open_renderer(data: bytes):
    """Lazy native renderer; no native object survives the global mutex."""
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise OCRUnavailable("扫描 PDF 渲染组件未安装，请先运行 python -m pip install -r requirements.txt。") from exc
    with _pdfium_lock:
        document = pdfium.PdfDocument(data)
        try:
            yield document
        finally:
            document.close()


def render_page_image(document, number: int):
    """Copy one bounded RGB image; caller closes it inside open_renderer()."""
    page = document[number - 1]
    try:
        width, height = page.get_size()
        bitmap = page.render(scale=render_scale(width, height), rev_byteorder=True, limit_image_cache=True)
        try:
            # to_pil may share PDFium memory. convert makes an independent copy.
            image = bitmap.to_pil().convert("RGB")
        finally:
            bitmap.close()
    finally:
        page.close()
    if image.width > MAX_RENDER_EDGE or image.height > MAX_RENDER_EDGE or image.width * image.height > MAX_RENDER_PIXELS:
        image.close()
        raise ValueError("Rendered PDF page exceeded its pixel bound")
    return image


def render_page(document, number: int, destination: Path) -> bool:
    """Render one page; skip uniform blanks only for OCR, never for preview."""
    image = render_page_image(document, number)
    try:
        extrema = image.getextrema()
        if all(high - low <= 2 for low, high in extrema):
            return False
        image.save(destination, "PNG")
        return True
    finally:
        image.close()


def recognize_pdf_pages(data: bytes, page_numbers: list[int]) -> dict[int, str]:
    """Process one temporary PNG at a time; never persist rasterized pages."""
    results: dict[int, str] = {}
    total_characters = 0
    with TemporaryDirectory(prefix="lexiread-pdf-") as temporary:
        path = Path(temporary) / "page.png"
        try:
            with open_renderer(data) as document:
                for number in page_numbers:
                    try:
                        if not render_page(document, number, path):
                            results[number] = ""
                            continue
                        result = get_ocr_service().recognize(path)
                        if not result.text.strip():
                            raise HTTPException(422, f"PDF 第 {number} 页没有识别到文字，请换用清晰的扫描件或选择仅提取文字。")
                        total_characters += len(result.text)
                        if total_characters > MAX_OCR_CHARACTERS:
                            raise HTTPException(413, "PDF 提取的正文不能超过 200,000 个字符。")
                        results[number] = result.text
                    except OCRUnavailable as exc:
                        raise OCRUnavailable(f"PDF 第 {number} 页识别失败，请检查本机 OCR 是否已安装，或换用清晰、摆正的扫描件后重试。") from exc
                    except HTTPException:
                        raise
                    except Exception as exc:
                        raise HTTPException(422, f"PDF 第 {number} 页无法渲染或识别，请重新导出 PDF 后重试。") from exc
                    finally:
                        path.unlink(missing_ok=True)
        except (HTTPException, OCRUnavailable):
            raise
        except Exception as exc:
            raise HTTPException(422, "扫描 PDF 无法渲染，请重新导出 PDF 后重试。") from exc
    return results
