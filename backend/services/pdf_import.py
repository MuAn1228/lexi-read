"""Import text-based PDFs atomically, retaining exact positions on every page."""
from collections import Counter
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import unicodedata
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile
from pypdf import PdfReader
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.models import Document, TextImportReceipt, WordOccurrence
from backend.repositories.vocabulary import VocabularyRepository
from backend.services.documents import require_document_capacity
from backend.services.text_import import text_occurrences
from backend.services.vocabulary import VocabularyService


MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 100
MAX_PDF_CHARACTERS = 200_000


def pdf_name(filename: str | None) -> str:
    # Browser filenames never become storage paths, including Windows-style paths.
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not name.lower().endswith(".pdf"):
        raise HTTPException(415, "请上传 PDF 文件（.pdf）。")
    if any(unicodedata.category(char) in {"Cc", "Cs", "Zl", "Zp"} for char in name):
        raise HTTPException(422, "PDF 文件名包含无效字符，请重命名后再上传。")
    return name


def extract_pdf(data: bytes) -> tuple[str, list[dict], list[WordOccurrence]]:
    if not data.startswith(b"%PDF-"):
        raise HTTPException(422, "文件内容不是有效的 PDF，请重新导出后上传。")
    try:
        reader = PdfReader(BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise HTTPException(422, "暂不支持加密 PDF，请先移除密码后再上传。")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise HTTPException(413, "PDF 不能超过 100 页。")
        contents, pages, words = [], [], []
        position, line = 0, 0
        for number, page in enumerate(reader.pages, 1):
            content = (page.extract_text() or "").replace("\r\n", "\n").replace("\r", "\n")
            # Invalid PDF character maps must not produce unencodable JSON/SQLite data.
            content = "".join(char if char in "\n\t" or unicodedata.category(char) not in {"Cc", "Cs"}
                              else " " for char in content)
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
            contents.append(content)
            position += len(content)
            line += content.count("\n")
        if not words:
            raise HTTPException(422, "PDF 中没有可提取的英文单词；扫描版暂不支持，请上传可以选中文字的英文 PDF。")
        return "\n\n".join(contents), pages, words
    except HTTPException:
        raise
    except Exception as exc:
        # Parser/encoding failures are user-facing validation errors; no file or row exists yet.
        raise HTTPException(422, "PDF 损坏或无法提取正文，请重新导出文本型 PDF 后再上传。") from exc


def import_pdf(session: Session, upload: UploadFile, request_id: UUID, user_id: int = 1) -> Document:
    name = pdf_name(upload.filename)
    data = upload.file.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(413, "PDF 不能超过 10 MB。")
    fingerprint = sha256(json.dumps(
        {"source": "pdf", "filename": name, "content_hash": sha256(data).hexdigest()},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    path: Path | None = None
    try:
        # Reserve the writer before receipt/capacity reads and expensive parsing.
        # Concurrent retries observe and store the PDF exactly once.
        session.execute(text("BEGIN IMMEDIATE"))
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
            session.commit()
            return document
        require_document_capacity(session, user_id)
        content, pages, words = extract_pdf(data)
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
