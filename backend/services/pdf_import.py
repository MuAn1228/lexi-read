"""Import native/scanned PDFs atomically with exact Unicode page positions."""
from collections import Counter
from contextlib import nullcontext
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from threading import Lock
from typing import Callable, ContextManager, Literal
import unicodedata
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile
from pypdf import PdfReader
from pypdf.generic import ContentStream
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.models import Document, TextImportReceipt, WordOccurrence
from backend.ocr.service import ENGLISH_WORD, OCRUnavailable
from backend.repositories.vocabulary import VocabularyRepository
from backend.services.documents import require_document_capacity
from backend.services.pdf_ocr import recognize_pdf_pages
from backend.services.text_import import text_occurrences
from backend.services.vocabulary import VocabularyService


MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 100
MAX_PDF_CHARACTERS = 200_000
MAX_PDF_OCR_PAGES = 20
MAX_PDF_SOURCE_PAGE_PIXELS = 50_000_000
PDFMode = Literal["auto", "text", "scan"]
OCRBudget = Callable[[int], ContextManager]
# Fixed stripes have bounded memory even if clients send unlimited UUIDs.
# Same-process retries wait before parsing; the durable receipt handles restarts
# and the final writer transaction also handles other server processes.
_import_locks = tuple(Lock() for _ in range(64))


def pdf_name(filename: str | None) -> str:
    # Browser filenames never become storage paths, including Windows-style paths.
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not name.lower().endswith(".pdf"):
        raise HTTPException(415, "请上传 PDF 文件（.pdf）。")
    if any(unicodedata.category(char) in {"Cc", "Cs", "Zl", "Zp"} for char in name):
        raise HTTPException(422, "PDF 文件名包含无效字符，请重命名后再上传。")
    return name


def _clean_text(content: str) -> str:
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(char if char in "\n\t" or unicodedata.category(char) not in {"Cc", "Cs"}
                   else " " for char in content)


def _has_images(resources, seen: set[int] | None = None, depth: int = 0, pdf=None) -> bool:
    """Inspect image resources without decoding potentially huge embedded images."""
    if depth > 10 or not resources:
        return False
    seen = seen if seen is not None else set()
    resources = resources.get_object()
    objects = resources.get("/XObject", {})
    objects = objects.get_object() if hasattr(objects, "get_object") else objects
    for reference in objects.values():
        obj = reference.get_object()
        identity = id(obj)
        if identity in seen:
            continue
        seen.add(identity)
        if obj.get("/Subtype") == "/Image":
            return True
        if obj.get("/Subtype") == "/Form":
            if _has_images(obj.get("/Resources"), seen, depth + 1, pdf):
                return True
            if any(operator == b"INLINE IMAGE" for _, operator in ContentStream(obj, pdf).operations):
                return True
    return False


def _page_visuals(page) -> tuple[bool, bool]:
    images = _has_images(page.get("/Resources"), pdf=page.pdf)
    contents = page.get_contents()
    operators = {operator for _, operator in contents.operations} if contents is not None else set()
    images = images or b"INLINE IMAGE" in operators
    # Empty streams and text positioning alone are not visible blank-page ink.
    painted = bool(operators & {b"Do", b"INLINE IMAGE", b"f", b"F", b"f*", b"S", b"s",
                                b"B", b"B*", b"b", b"b*", b"sh"})
    return images, painted


def _guard_source_images(page, number: int) -> None:
    """Bound source image decode memory using metadata, never image payloads.

    Output raster limits do not bound embedded images decoded by PDFium. Count
    shared XObjects only once per page, including images inside nested Forms
    and inline images whose dimensions are stored with abbreviated keys.
    """
    per_image_limit = get_settings().max_image_pixels
    seen: set[int] = set()
    total = 0

    def dimensions(image) -> None:
        nonlocal total
        try:
            width = int(image.get("/Width", image.get("/W", 0)))
            height = int(image.get("/Height", image.get("/H", 0)))
        except (TypeError, ValueError, OverflowError) as exc:
            raise HTTPException(422, f"PDF 第 {number} 页的图片尺寸无效，请重新导出 PDF。") from exc
        if width <= 0 or height <= 0:
            raise HTTPException(422, f"PDF 第 {number} 页的图片尺寸无效，请重新导出 PDF。")
        pixels = width * height
        total += pixels
        if pixels > per_image_limit or total > MAX_PDF_SOURCE_PAGE_PIXELS:
            raise HTTPException(413, f"PDF 第 {number} 页的扫描图分辨率过大，请压缩扫描图分辨率后重新上传（单张最多 {per_image_limit // 1_000_000} 百万像素，每页合计最多 50 百万像素）。")

    def inline_images(stream) -> None:
        if stream is None:
            return
        contents = stream if isinstance(stream, ContentStream) else ContentStream(stream, page.pdf)
        for operands, operator in contents.operations:
            if operator == b"INLINE IMAGE":
                dimensions(operands["settings"])

    def image_object(obj) -> None:
        identity = id(obj)
        if identity in seen:
            return
        seen.add(identity)
        dimensions(obj)
        # Soft/explicit image masks can carry their own large raster even when
        # the visible base image is small. Color-key masks are arrays, not images.
        for key in ("/SMask", "/Mask"):
            mask = obj.get(key)
            mask = mask.get_object() if hasattr(mask, "get_object") else mask
            if isinstance(mask, dict) and mask.get("/Subtype") == "/Image":
                image_object(mask)

    def resources_images(resources, depth: int = 0) -> None:
        if not resources:
            return
        if depth > 10:
            raise HTTPException(413, f"PDF 第 {number} 页的图片嵌套过深，请重新导出 PDF 后上传。")
        resources = resources.get_object()
        objects = resources.get("/XObject", {})
        objects = objects.get_object() if hasattr(objects, "get_object") else objects
        for reference in objects.values():
            obj = reference.get_object()
            identity = id(obj)
            if identity in seen:
                continue
            if obj.get("/Subtype") == "/Image":
                image_object(obj)
            elif obj.get("/Subtype") == "/Form":
                seen.add(identity)
                inline_images(obj)
                resources_images(obj.get("/Resources"), depth + 1)

    inline_images(page.get_contents())
    resources_images(page.get("/Resources"))


def extract_pdf(data: bytes, mode: PDFMode = "auto", ocr_budget: OCRBudget | None = None
                ) -> tuple[str, list[dict], list[WordOccurrence]]:
    if mode not in {"auto", "text", "scan"}:
        raise HTTPException(422, "请选择有效的 PDF 识别方式。")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(422, "文件内容不是有效的 PDF，请重新导出后上传。")
    try:
        reader = PdfReader(BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise HTTPException(422, "暂不支持加密 PDF，请先移除密码后再上传。")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise HTTPException(413, "PDF 不能超过 100 页。")
        contents: list[str] = []
        ocr_pages: list[int] = []
        for number, page in enumerate(reader.pages, 1):
            failed = False
            try:
                content = _clean_text(page.extract_text() or "")
            except Exception as exc:
                if mode == "text":
                    raise HTTPException(422, f"PDF 第 {number} 页无法提取文字，请改用扫描识别。") from exc
                content, failed = "", True
            if mode == "text":
                contents.append(content)
                continue
            images, painted = _page_visuals(page)
            native_words = sum(1 for _ in ENGLISH_WORD.finditer(content))
            needs_ocr = ((mode == "scan" and bool(content.strip() or images or painted or failed))
                         or (mode == "auto" and (failed or (images and native_words < 20)
                                                  or (not content.strip() and painted))))
            if needs_ocr:
                _guard_source_images(page, number)
                ocr_pages.append(number)
                contents.append("")
            else:
                contents.append(content)
        if len(ocr_pages) > MAX_PDF_OCR_PAGES:
            raise HTTPException(413, "单次最多识别 20 页扫描内容，请拆分 PDF 后上传；文字版 PDF 仍支持 100 页。")
        if sum(map(len, contents)) + max(0, len(contents) - 1) * 2 > MAX_PDF_CHARACTERS:
            raise HTTPException(413, "PDF 提取的正文不能超过 200,000 个字符。")
        if ocr_pages:
            with ocr_budget(len(ocr_pages)) if ocr_budget is not None else nullcontext():
                recognized = recognize_pdf_pages(data, ocr_pages)
            for number in ocr_pages:
                contents[number - 1] = _clean_text(recognized[number])
                if contents[number - 1].strip() and not text_occurrences(contents[number - 1]):
                    raise HTTPException(422, f"PDF 第 {number} 页没有识别到英文单词，请换用清晰的英文扫描件。")
        pages, words = [], []
        position, line = 0, 0
        for number, content in enumerate(contents, 1):
            if number > 1:
                position += 2  # The same separator is used by the final join below.
                line += 2
            if position + len(content) > MAX_PDF_CHARACTERS:
                raise HTTPException(413, "PDF 提取的正文不能超过 200,000 个字符。")
            pages.append({"number": number, "text_start": position, "text_end": position + len(content)})
            # Tokenize pages separately: a sentence must never cross a page boundary.
            for word in text_occurrences(content):
                word.text_start += position
                word.text_end += position
                word.line += line
                word.bbox_source = "pdf"
                words.append(word)
            position += len(content)
            line += content.count("\n")
        if not words:
            raise HTTPException(422, "PDF 中没有可用的英文单词，请上传英文 PDF；扫描件请使用自动识别或强制扫描识别。")
        return "\n\n".join(contents), pages, words
    except (HTTPException, OCRUnavailable):
        raise
    except Exception as exc:
        # Parser/encoding failures are user-facing validation errors; no file or row exists yet.
        raise HTTPException(422, "PDF 损坏或无法提取正文，请重新导出 PDF 后再上传。") from exc


def import_pdf(session: Session, upload: UploadFile, request_id: UUID, user_id: int = 1,
               mode: PDFMode = "auto", ocr_budget: OCRBudget | None = None) -> Document:
    if mode not in {"auto", "text", "scan"}:
        raise HTTPException(422, "请选择有效的 PDF 识别方式。")
    name = pdf_name(upload.filename)
    data = upload.file.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(413, "PDF 不能超过 10 MB。")
    payload = {"source": "pdf", "filename": name, "content_hash": sha256(data).hexdigest()}
    if mode != "auto":
        payload["mode"] = mode
    fingerprint = sha256(json.dumps(
        payload,
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    lock = _import_locks[hash((user_id, str(request_id))) % len(_import_locks)]
    with lock:
        return _import_prepared(session, name, data, request_id, user_id, mode, ocr_budget, fingerprint)


def _receipt_document(session: Session, request_id: UUID, user_id: int, fingerprint: str) -> Document | None:
    receipt = session.scalar(select(TextImportReceipt).where(
        TextImportReceipt.user_id == user_id, TextImportReceipt.request_id == str(request_id)))
    if receipt is not None:
        if receipt.payload_hash != fingerprint:
            raise HTTPException(409, "该请求编号已用于另一篇文章，请重新发起导入。")
        document = session.get(Document, receipt.document_id)
        if document is None or document.user_id != user_id:
            raise HTTPException(409, "该导入记录对应的文章已不存在，请刷新阅读材料。")
        if document.deleted_at is not None:
            raise HTTPException(409, "这篇文章已移入回收站，请在文章库的回收站中恢复。")
        return document
    return None


def _import_prepared(session: Session, name: str, data: bytes, request_id: UUID, user_id: int,
                     mode: PDFMode, ocr_budget: OCRBudget | None, fingerprint: str) -> Document:
    path: Path | None = None
    try:
        document = _receipt_document(session, request_id, user_id, fingerprint)
        if document is not None:
            session.commit()
            return document
        require_document_capacity(session, user_id)
        # End the preliminary read transaction before rendering/inference. A
        # long OCR job must never hold SQLite's writer or block learning writes.
        session.rollback()
        content, pages, words = extract_pdf(data, mode, ocr_budget)
        session.execute(text("BEGIN IMMEDIATE"))
        document = _receipt_document(session, request_id, user_id, fingerprint)
        if document is not None:
            session.commit()
            return document
        require_document_capacity(session, user_id)
        folder = get_settings().storage_dir / "pdfs"
        folder.mkdir(parents=True, exist_ok=True)
        candidate = folder / f"{uuid4().hex}.pdf"
        # Exclusive creation protects cleanup against an unexpected path collision.
        with candidate.open("xb") as stream:
            path = candidate
            stream.write(data)
        document = Document(user_id=user_id, title=(name[:-4].strip() or "PDF 阅读材料")[:250],
                            image_path=str(path), width=0, height=0, ocr_text=content,
                            source="pdf", pages=pages, words=words)
        session.add(document)
        session.flush()
        counts = Counter(word.lemma for word in words)
        surfaces = {}
        for word in words:
            surfaces.setdefault(word.lemma, word.word)
        service = VocabularyService(VocabularyRepository(session, user_id))
        for lemma, count in counts.items():
            service.observe(surfaces[lemma], lemma, count)
        session.add(TextImportReceipt(user_id=user_id, request_id=str(request_id),
                                      payload_hash=fingerprint, document_id=document.id))
        session.commit()
        return document
    except Exception:
        session.rollback()
        if path is not None:
            path.unlink(missing_ok=True)
        raise
