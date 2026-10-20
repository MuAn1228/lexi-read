import re
from collections import Counter
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.models import Document, WordOccurrence
from backend.models.entities import utcnow
from backend.ocr.service import ENGLISH_WORD, OCRResult, OCRWord, get_ocr_service
from backend.repositories.vocabulary import VocabularyRepository
from backend.schemas import DocumentOut, DocumentSummary, DocumentUpdateRequest, WordOut
from backend.services.vocabulary import VocabularyService, normalize_word


def document_summary(doc: Document, word_count: int | None = None) -> DocumentSummary:
    return DocumentSummary(id=doc.id, title=doc.title,
                           image_url=None if doc.source in {"text", "pdf", "web"} else f"/api/documents/{doc.id}/image",
                           width=doc.width, height=doc.height, source=doc.source, source_url=doc.source_url,
                           created_at=doc.created_at, word_count=len(doc.words) if word_count is None else word_count,
                           tags=doc.tags or [], bookmark_occurrence_id=doc.bookmark_occurrence_id,
                           deleted_at=doc.deleted_at, excerpt=re.sub(r"\s+", " ", doc.ocr_text).strip()[:160],
                           page_count=len(doc.pages or []) if doc.source == "pdf" else 1,
                           pdf_url=f"/api/documents/{doc.id}/pdf" if doc.source == "pdf" else None)


def list_documents(session: Session, user_id: int, *, q: str | None = None, tag: str | None = None,
                   source: str | None = None, deleted: bool = False, sort: str = "newest") -> list[DocumentSummary]:
    # One aggregate query, without materializing the occurrences of every article.
    # Python casefold supports Unicode titles/tags; substring search is literal,
    # including SQL wildcard characters such as % and _.
    query = select(Document, func.count(WordOccurrence.id)).outerjoin(
        WordOccurrence, WordOccurrence.document_id == Document.id,
    ).where(Document.user_id == user_id,
            Document.deleted_at.is_not(None) if deleted else Document.deleted_at.is_(None))
    if source is not None:
        query = query.where(Document.source == source)
    query = query.group_by(Document.id).order_by(
        Document.created_at.asc() if sort == "oldest" else Document.created_at.desc(),
        Document.id.asc() if sort == "oldest" else Document.id.desc(),
    )
    result = []
    with session.no_autoflush:
        for doc, count in session.execute(query):
            if q and q.strip().casefold() not in (doc.title + "\n" + doc.ocr_text).casefold():
                continue
            if tag and tag.strip().casefold() not in {value.casefold() for value in (doc.tags or [])}:
                continue
            result.append(document_summary(doc, count))
    if sort == "title":
        result.sort(key=lambda doc: (doc.title.casefold(), doc.id))
    return result


def document_out(doc: Document, session: Session, user_id: int = 1) -> DocumentOut:
    statuses = {entry.lemma: entry.status for entry in VocabularyRepository(session, user_id).all()}
    words = tuple(WordOut(id=w.id, word=w.word, lemma=w.lemma, sentence=w.sentence,
                          bbox=None if doc.source in {"text", "pdf", "web"} else tuple(w.bbox), line=w.line,
                          confidence=None if doc.source in {"text", "pdf", "web"} else w.confidence,
                          bbox_source=w.bbox_source, text_start=w.text_start, text_end=w.text_end,
                          status=statuses.get(w.lemma)) for w in doc.words)
    return DocumentOut(**document_summary(doc).model_dump(), ocr_text=doc.ocr_text, words=words, pages=doc.pages or [])


def require_document(session: Session, document_id: int, user_id: int = 1, *, include_deleted: bool = False) -> Document:
    document = session.get(Document, document_id, populate_existing=True)
    if document is None or document.user_id != user_id or (document.deleted_at is not None and not include_deleted):
        raise HTTPException(404, "未找到这篇阅读材料。")
    return document


def require_current_occurrence(session: Session, occurrence_id: int, document_id: int, user_id: int):
    """A slow AI response must not record a lookup against corrected old text."""
    document = require_document(session, document_id, user_id)
    occurrence = session.get(WordOccurrence, occurrence_id, populate_existing=True)
    if occurrence is None or occurrence.document_id != document.id:
        raise HTTPException(409, "文章正文已校对更新，请重新打开文章并选择单词。")
    return occurrence, document


def require_document_capacity(session: Session, user_id: int) -> None:
    settings = get_settings()
    if settings.app_mode != "public":
        return
    count = session.scalar(select(func.count(Document.id)).where(
        Document.user_id == user_id, Document.deleted_at.is_(None)))
    if count >= settings.public_max_documents_per_user:
        raise HTTPException(409, "当前访客的阅读材料数量已达上限，请联系网站管理员。")


def update_document(session: Session, document_id: int, payload: DocumentUpdateRequest,
                    user_id: int) -> DocumentSummary:
    try:
        session.execute(text("BEGIN IMMEDIATE"))
        doc = require_document(session, document_id, user_id)
        if "bookmark_occurrence_id" in payload.model_fields_set and payload.bookmark_occurrence_id is not None:
            occurrence = session.get(WordOccurrence, payload.bookmark_occurrence_id)
            if occurrence is None or occurrence.document_id != doc.id:
                raise HTTPException(422, "书签需要指向这篇文章中的单词。")
        for field in payload.model_fields_set:
            setattr(doc, field, getattr(payload, field))
        session.flush()
        count = session.scalar(select(func.count(WordOccurrence.id)).where(WordOccurrence.document_id == doc.id))
        result = document_summary(doc, count)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise


def change_document_deleted(session: Session, document_id: int, user_id: int, *, restore: bool) -> DocumentSummary:
    try:
        session.execute(text("BEGIN IMMEDIATE"))
        doc = require_document(session, document_id, user_id, include_deleted=True)
        if restore and doc.deleted_at is not None:
            require_document_capacity(session, user_id)
            doc.deleted_at = None
        elif not restore and doc.deleted_at is None:
            doc.deleted_at = utcnow()
        session.flush()
        count = session.scalar(select(func.count(WordOccurrence.id)).where(WordOccurrence.document_id == doc.id))
        result = document_summary(doc, count)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise


def sentence_contexts(result: OCRResult) -> list[str]:
    """Match OCR tokens sequentially into punctuation-delimited, cross-line sentences."""
    text = result.text
    spans = [(m.start(), m.end(), re.sub(r"\s+", " ", m.group()).strip())
             for m in re.finditer(r"[^.!?]+(?:[.!?]+|$)", text)]
    contexts, cursor = [], 0
    lines = text.splitlines()
    for word in result.words:
        match = re.search(r"(?<![A-Za-z])" + re.escape(word.word) + r"(?![A-Za-z])",
                          text[cursor:], re.IGNORECASE)
        if match:
            position = cursor + match.start()
            cursor += match.end()
            contexts.append(next((sentence for start, end, sentence in spans if start <= position < end), word.word))
        else:
            contexts.append(lines[word.line] if 0 <= word.line < len(lines) else word.word)
    return contexts


def save_document(session: Session, path: Path, title: str, result: OCRResult, source: str,
                  user_id: int = 1) -> Document:
    if not result.words:
        raise HTTPException(422, "图片中没有识别到英文单词，请上传清晰的英文 JPG 或 PNG。")
    doc = Document(user_id=user_id, title=title[:250], image_path=str(path), width=result.width, height=result.height,
                   ocr_text=result.text, source=source)
    session.add(doc)
    counts: Counter[str] = Counter()
    surface_words: dict[str, str] = {}
    for word, sentence in zip(result.words, sentence_contexts(result)):
        try:
            lemma = normalize_word(word.word)
        except ValueError:
            continue
        doc.words.append(WordOccurrence(word=word.word, lemma=lemma, sentence=sentence,
                                        bbox=word.bbox, line=word.line, confidence=word.confidence,
                                        bbox_source=word.bbox_source))
        counts[lemma] += 1
        surface_words.setdefault(lemma, word.word)
    if not counts:
        raise HTTPException(422, "未识别到可用的英文单词。")
    service = VocabularyService(VocabularyRepository(session, user_id))
    for lemma, count in counts.items():
        service.observe(surface_words[lemma], lemma, count)
    session.commit()
    session.refresh(doc)
    return doc


def import_image(session: Session, upload: UploadFile, user_id: int = 1) -> Document:
    settings = get_settings()
    require_document_capacity(session, user_id)
    if Path(upload.filename or "").suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        raise HTTPException(415, "仅支持 JPG 和 PNG 图片。")
    data = upload.file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, "图片不能超过 10 MB。")
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format not in {"JPEG", "PNG"}:
                raise HTTPException(415, "文件内容必须是 JPG 或 PNG 图片。")
            if image.width * image.height > settings.max_image_pixels:
                raise HTTPException(413, "图片不能超过 2500 万像素。")
            image.verify()
        with Image.open(BytesIO(data)) as image:
            normalized = ImageOps.exif_transpose(image).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise HTTPException(422, "图片损坏或无法读取。") from exc
    except Image.DecompressionBombError as exc:
        raise HTTPException(413, "图片尺寸过大。") from exc
    folder = settings.storage_dir / "images"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid4().hex}.png"
    try:
        normalized.save(path, "PNG")
        result = get_ocr_service().recognize(path)
        return save_document(session, path, Path(upload.filename).name, result, "upload", user_id)
    except Exception:
        session.rollback()
        path.unlink(missing_ok=True)
        raise


DEMO_LINES = [
    "Understanding Generalization",
    "A machine learning model learns patterns from training data.",
    "Generalization is the ability to perform well on unseen data.",
    "An empirical evaluation measures performance on new samples",
    "from the same distribution. Overfitting occurs when a model",
    "memorizes training examples instead of learning useful patterns.",
    "Regularization can reduce overfitting and improve generalization.",
    "A distribution shift makes reliable prediction more difficult.",
    "Cross-validation helps estimate performance on unseen data.",
]


def create_demo(session: Session, user_id: int = 1) -> Document:
    existing = session.scalar(select(Document).where(
        Document.user_id == user_id, Document.source == "demo", Document.deleted_at.is_(None)))
    if existing and Path(existing.image_path).is_file():
        return existing
    require_document_capacity(session, user_id)
    image = Image.new("RGB", (1320, 720), "#fffefb")
    draw = ImageDraw.Draw(image)
    font = None
    for font_path in ("C:/Windows/Fonts/georgia.ttf", "DejaVuSerif.ttf"):
        try:
            font = ImageFont.truetype(font_path, 29)
            break
        except OSError:
            pass
    font = font or ImageFont.load_default(size=29)
    words = []
    draw.text((62, 32), "READING NOTE  /  01", fill="#879189", font=ImageFont.load_default(size=17))
    for line_index, line in enumerate(DEMO_LINES):
        y = 96 + line_index * 62
        draw.text((62, y), line, font=font, fill="#27372f")
        for match in ENGLISH_WORD.finditer(line):
            x = 62 + draw.textlength(line[:match.start()], font=font)
            box = draw.textbbox((x, y), match.group(), font=font)
            words.append(OCRWord(match.group(), [int(n) for n in box], line_index, 1.0, "demo"))
    folder = get_settings().storage_dir / "images"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"demo-{uuid4().hex}.png"
    try:
        image.save(path)
        return save_document(session, path, "Understanding Generalization · 示例", OCRResult(
            "\n".join(DEMO_LINES), image.width, image.height, words), "demo", user_id)
    except Exception:
        session.rollback()
        path.unlink(missing_ok=True)
        raise
