"""Explicit PDF/image text corrections, independent of OCR and learning observations."""
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path

from fastapi import HTTPException
from pypdf import PdfReader
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from backend.models import Document, TextImportReceipt, WordOccurrence
from backend.schemas import ProofreadOut, ProofreadPage, ProofreadRequest
from backend.services.documents import require_document
from backend.services.pdf_import import MAX_PDF_BYTES, MAX_PDF_PAGES, _guard_source_images
from backend.services.pdf_ocr import open_renderer, render_page_image
from backend.services.text_import import text_occurrences


def _hash(value) -> str:
    return sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
                             separators=(",", ":")).encode()).hexdigest()


def require_pdf(session: Session, document_id: int, user_id: int) -> Document:
    doc = require_document(session, document_id, user_id)
    if doc.source != "pdf":
        raise HTTPException(422, "校对工作台目前仅支持 PDF 阅读材料。")
    return doc


def require_proofread_document(session: Session, document_id: int, user_id: int) -> Document:
    doc = require_document(session, document_id, user_id)
    if doc.source not in {"pdf", "upload", "demo"}:
        raise HTTPException(422, "校对工作台仅支持 PDF 和图片阅读材料。")
    return doc


def proofread_revision(doc: Document) -> str:
    # New IDs are allocated before deleting old tokens: an A -> B -> A edit
    # never restores the original revision or accepts an obsolete editor draft.
    value = {"text": doc.ocr_text, "pages": doc.pages,
             "occurrences": [word.id for word in doc.words]}
    # Preserve existing PDF revisions and pending receipts across this upgrade.
    if doc.source in {"upload", "demo"}:
        value["source"] = "image"
    return _hash(value)


def _page_texts(doc: Document) -> list[ProofreadPage]:
    if doc.source in {"upload", "demo"}:
        return [ProofreadPage(number=1, text=doc.ocr_text)]
    pages = doc.pages or []
    position = 0
    if not 1 <= len(pages) <= MAX_PDF_PAGES:
        raise HTTPException(422, "文章分页信息无效，请重新导入原 PDF。")
    result = []
    for number, page in enumerate(pages, 1):
        if (not isinstance(page, dict) or page.get("number") != number
                or type(page.get("text_start")) is not int or page.get("text_start") != position
                or type(page.get("text_end")) is not int
                or not position <= page["text_end"] <= len(doc.ocr_text)):
            raise HTTPException(422, "文章分页信息无效，请重新导入原 PDF。")
        result.append(ProofreadPage(number=number, text=doc.ocr_text[position:page["text_end"]]))
        position = page["text_end"]
        if number < len(pages):
            if doc.ocr_text[position:position + 2] != "\n\n":
                raise HTTPException(422, "文章分页信息无效，请重新导入原 PDF。")
            position += 2
    if position != len(doc.ocr_text):
        raise HTTPException(422, "文章分页信息无效，请重新导入原 PDF。")
    return result


def read_proofread(session: Session, document_id: int, user_id: int) -> ProofreadOut:
    # SQLite's legacy SELECT behavior does not start a database transaction.
    # A connection-level savepoint keeps text, page boundaries and occurrence IDs
    # in one snapshot without flushing, committing or rolling back caller state.
    # Session.begin_nested() would flush pending ORM edits before the savepoint.
    with session.no_autoflush, session.connection().begin_nested():
        doc = require_proofread_document(session, document_id, user_id)
        return ProofreadOut(document_id=doc.id, source="pdf" if doc.source == "pdf" else "image",
                            revision=proofread_revision(doc), pages=_page_texts(doc))


def _replacement(pages: list[ProofreadPage], *, source: str = "pdf"):
    metadata, words = [], []
    position, line = 0, 0
    for page in pages:
        if page.number > 1:
            position += 2
            line += 2
        metadata.append({"number": page.number, "text_start": position,
                         "text_end": position + len(page.text)})
        for word in text_occurrences(page.text):
            word.text_start += position
            word.text_end += position
            word.line += line
            word.bbox_source = "pdf" if source == "pdf" else "corrected_text"
            if source != "pdf":
                # No original OCR box/confidence can describe rewritten text.
                word.confidence = 0
            words.append(word)
        position += len(page.text)
        line += page.text.count("\n")
    if not words:
        raise HTTPException(422, "校对后的全文至少需要保留一个可用的英文单词，不能清空整篇文章。")
    return "\n\n".join(page.text for page in pages), metadata, words


def _bookmark_target(doc: Document, previous: list[ProofreadPage], updated: list[ProofreadPage], words):
    """Map unchanged prefixes/suffixes or a unique token neighborhood, bounded O(n).

    Ambiguous duplicate passages deliberately clear the bookmark rather than
    silently pointing at a different occurrence. No fuzzy word matching occurs.
    """
    old = next((word for word in doc.words if word.id == doc.bookmark_occurrence_id), None)
    if (old is None or type(old.text_start) is not int or type(old.text_end) is not int
            or not 0 <= old.text_start < old.text_end <= len(doc.ocr_text)
            or doc.ocr_text[old.text_start:old.text_end] != old.word):
        return None
    regions = (doc.pages if doc.source == "pdf" else
               [{"number": 1, "text_start": 0, "text_end": len(doc.ocr_text)}])
    page = next((p for p in regions if p["text_start"] <= old.text_start < old.text_end <= p["text_end"]), None)
    if page is None:
        return None
    number = page["number"]
    before, after = previous[number - 1].text, updated[number - 1].text
    start, end = old.text_start - page["text_start"], old.text_end - page["text_start"]
    prefix = 0
    while prefix < min(len(before), len(after)) and before[prefix] == after[prefix]:
        prefix += 1
    suffix = 0
    while suffix < min(len(before), len(after)) - prefix and before[-suffix - 1] == after[-suffix - 1]:
        suffix += 1
    target = start if end <= prefix else (start + len(after) - len(before) if start >= len(before) - suffix else None)
    if target is None:
        # A unique verbatim neighborhood is safe even if edits precede and follow
        # the bookmark. Short/duplicate contexts remain intentionally unmapped.
        left, right = max(0, start - 40), min(len(before), end + 40)
        snippet = before[left:right]
        match = after.find(snippet)
        if len(snippet) >= 20 and match >= 0 and after.find(snippet, match + 1) < 0:
            target = match + start - left
    offset = sum(len(p.text) + 2 for p in updated[:number - 1])
    return next((word for word in words if target is not None and word.text_start == offset + target
                 and word.text_end == offset + target + len(old.word) and word.word == old.word), None)


def save_proofread(session: Session, document_id: int, payload: ProofreadRequest, user_id: int) -> Document:
    try:
        session.execute(text("BEGIN IMMEDIATE"))
        # Always enforce ownership/source/deletion, including successful retries.
        doc = require_proofread_document(session, document_id, user_id)
        fingerprint = _hash({"operation": "pdf-proofread-v1" if doc.source == "pdf" else "image-proofread-v1",
                             "document_id": document_id, "base_revision": payload.base_revision,
                             "pages": [page.model_dump() for page in payload.pages]})
        receipt = session.scalar(select(TextImportReceipt).where(
            TextImportReceipt.user_id == user_id, TextImportReceipt.request_id == str(payload.request_id)))
        if receipt is not None:
            if receipt.payload_hash != fingerprint or receipt.document_id != document_id:
                raise HTTPException(409, "该请求编号已用于其他操作，请重新打开校对工作台后保存。")
            # The route serializes after commit. Load occurrences while this
            # writer still protects the corresponding text and page metadata.
            len(doc.words)
            session.commit()
            return doc
        if proofread_revision(doc) != payload.base_revision:
            raise HTTPException(409, "文章正文已在其他窗口修改，请保留当前草稿并重新加载最新正文后校对。")
        previous = _page_texts(doc)
        if len(previous) != len(payload.pages):
            message = ("请提交原 PDF 的全部页面，不能增加或删除页面。" if doc.source == "pdf" else
                       "图片正文只有一页，不能增加或删除页面。")
            raise HTTPException(422, message)
        content, pages, words = _replacement(payload.pages, source=doc.source)
        if doc.source != "pdf":
            pages = []  # A virtual editing page is not PDF pagination metadata.
        if content != doc.ocr_text or pages != doc.pages:
            bookmark = _bookmark_target(doc, previous, payload.pages, words)
            old_ids = [word.id for word in doc.words]
            # Insert first, while all old rows exist, so SQLite never recycles an
            # old occurrence ID. Stale tabs must fail, never analyze another word.
            for word in words:
                word.document_id = doc.id
            session.add_all(words)
            session.flush()
            session.execute(delete(WordOccurrence).where(WordOccurrence.document_id == doc.id,
                                                          WordOccurrence.id <= max(old_ids, default=0)),
                            execution_options={"synchronize_session": "fetch"})
            doc.ocr_text, doc.pages = content, pages
            doc.bookmark_occurrence_id = bookmark.id if bookmark is not None else None
            session.flush()
            session.expire(doc, ["words"])
        # Correcting a surface is not another encounter or assessment. Existing
        # vocabulary, mastery, events, counts and review schedules stay untouched.
        session.add(TextImportReceipt(user_id=user_id, request_id=str(payload.request_id),
                                      payload_hash=fingerprint, document_id=doc.id))
        # expire_on_commit=False retains this coherent response snapshot even if
        # another correction commits before document_out reads the collection.
        len(doc.words)
        session.commit()
        return doc
    except Exception:
        session.rollback()
        raise


def pdf_page_png(session: Session, document_id: int, number: int, user_id: int) -> bytes:
    doc = require_pdf(session, document_id, user_id)
    if not 1 <= number <= len(doc.pages or []):
        raise HTTPException(404, "未找到这个 PDF 页面。")
    path = Path(doc.image_path)
    if not path.is_file():
        raise HTTPException(404, "PDF 原文件缺失，请重新导入。")
    with path.open("rb") as stream:
        data = stream.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(413, "PDF 原文件超过 10 MB，无法预览。")
    try:
        reader = PdfReader(BytesIO(data), strict=True)
        if reader.is_encrypted or len(reader.pages) != len(doc.pages) or len(reader.pages) > MAX_PDF_PAGES:
            raise HTTPException(422, "PDF 原文件与分页信息不一致，请重新导入。")
        _guard_source_images(reader.pages[number - 1], number)
        with open_renderer(data) as rendered:
            image = render_page_image(rendered, number)
            try:
                output = BytesIO()
                image.save(output, "PNG")
                result = output.getvalue()
            finally:
                image.close()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, f"PDF 第 {number} 页无法预览，请重新导出 PDF 后重试。") from exc
    # No writer is held while rasterizing; recheck a concurrent trash/restore.
    require_pdf(session, document_id, user_id)
    return result
