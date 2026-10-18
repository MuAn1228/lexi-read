"""Import a fetched article once, without holding a database writer over I/O."""
from collections import Counter
from hashlib import sha256
from threading import Lock

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.models import Document, TextImportReceipt
from backend.repositories.vocabulary import VocabularyRepository
from backend.schemas import UrlImportRequest
from backend.services.documents import require_document_capacity
from backend.services.text_import import text_occurrences
from backend.services.vocabulary import VocabularyService
from backend.services.web_extraction import extract_article
from backend.services.web_fetch import fetch_page, normalize_url


# The local server runs one worker. A fixed-size lock pool serializes same-UUID
# requests through network work without an unbounded map of visitor-supplied keys.
_IMPORT_LOCKS = [Lock() for _ in range(64)]


def _existing(session: Session, user_id: int, key: str, fingerprint: str) -> Document | None:
    receipt = session.scalar(select(TextImportReceipt).where(
        TextImportReceipt.user_id == user_id, TextImportReceipt.request_id == key))
    if receipt is None:
        return None
    if receipt.payload_hash != fingerprint:
        raise HTTPException(409, "该请求编号已用于另一篇文章，请重新发起导入。")
    document = session.get(Document, receipt.document_id, populate_existing=True)
    if document is None or document.user_id != user_id:
        raise HTTPException(409, "该导入记录对应的文章已不存在，请刷新阅读材料。")
    if document.deleted_at is not None:
        raise HTTPException(409, "这篇文章已移入回收站，请在文章库的回收站中恢复。")
    return document


def import_url(session: Session, payload: UrlImportRequest, user_id: int = 1) -> Document:
    url = normalize_url(payload.url)
    fingerprint = sha256(("web\0" + url).encode("utf-8")).hexdigest()
    key = str(payload.request_id)
    stripe = int(sha256(f"{user_id}:{key}".encode()).hexdigest()[:8], 16) % len(_IMPORT_LOCKS)
    with _IMPORT_LOCKS[stripe]:
        try:
            existing = _existing(session, user_id, key, fingerprint)
            if existing is not None:
                session.commit()
                return existing
            require_document_capacity(session, user_id)
            # End the read transaction before remote I/O; no application writes
            # have occurred and concurrent learning remains available.
            session.rollback()
            html, final_url = fetch_page(url)
            title, content = extract_article(html, final_url)
            words = text_occurrences(content)
            if not words:
                raise HTTPException(422, "网页中没有可用的英文正文，请复制英文文章后粘贴导入。")
            for word in words:
                word.bbox_source = "web"
            session.execute(text("BEGIN IMMEDIATE"))
            # Also protect against retries handled by another server worker.
            existing = _existing(session, user_id, key, fingerprint)
            if existing is not None:
                session.commit()
                return existing
            require_document_capacity(session, user_id)
            document = Document(user_id=user_id, title=title, image_path="", width=0, height=0,
                                ocr_text=content, source="web", source_url=final_url, words=words)
            session.add(document)
            session.flush()
            counts = Counter(word.lemma for word in words)
            surfaces = {}
            for word in words:
                surfaces.setdefault(word.lemma, word.word)
            service = VocabularyService(VocabularyRepository(session, user_id))
            for lemma, count in counts.items():
                service.observe(surfaces[lemma], lemma, count)
            session.add(TextImportReceipt(user_id=user_id, request_id=key,
                                          payload_hash=fingerprint, document_id=document.id))
            session.commit()
            return document
        except Exception:
            session.rollback()
            raise
