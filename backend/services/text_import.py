"""Import plain text without OCR, and commit each browser request at most once."""
from bisect import bisect_right
from collections import Counter
import hashlib
import json
import re

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.models import Document, TextImportReceipt, WordOccurrence
from backend.ocr.service import ENGLISH_WORD
from backend.repositories.vocabulary import VocabularyRepository
from backend.schemas import TextImportRequest
from backend.services.documents import require_document_capacity
from backend.services.lemmatization import normalize_word
from backend.services.vocabulary import VocabularyService


def text_occurrences(content: str) -> list[WordOccurrence]:
    """Keep exact code point spans and surfaces; unsupported tokens stay plain text."""
    sentences = [(match.start(), match.end(), re.sub(r"\s+", " ", match.group()).strip())
                 for match in re.finditer(r"[^.!?]+(?:[.!?]+|$)", content)]
    newlines = [index for index, char in enumerate(content) if char == "\n"]
    words, sentence_index = [], 0
    for match in ENGLISH_WORD.finditer(content):
        try:
            lemma = normalize_word(match.group())
        except ValueError:
            # In particular, never truncate a long token into invented words.
            continue
        while sentence_index < len(sentences) and sentences[sentence_index][1] <= match.start():
            sentence_index += 1
        sentence = (sentences[sentence_index][2] if sentence_index < len(sentences)
                    else match.group())
        words.append(WordOccurrence(
            word=match.group(), lemma=lemma, sentence=sentence,
            bbox=[0, 0, 0, 0], line=bisect_right(newlines, match.start()), confidence=1,
            bbox_source="text", text_start=match.start(), text_end=match.end(),
        ))
    return words


def import_text(session: Session, payload: TextImportRequest, user_id: int = 1) -> Document:
    title = payload.title or next((line.strip()[:80] for line in payload.text.splitlines()
                                   if line.strip()), "粘贴文章")
    # Hash normalized content, not UUID or transport JSON whitespace/order.
    fingerprint = hashlib.sha256(json.dumps(
        {"text": payload.text, "title": title}, ensure_ascii=False,
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    key = str(payload.request_id)
    try:
        # Acquire the SQLite writer before capacity/receipt/exposure reads;
        # distinct concurrent imports and retries cannot race their counters.
        session.execute(text("BEGIN IMMEDIATE"))
        receipt = session.scalar(select(TextImportReceipt).where(
            TextImportReceipt.user_id == user_id, TextImportReceipt.request_id == key,
        ))
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
        words = text_occurrences(payload.text)
        if not words:
            raise HTTPException(422, "文章中没有可用的英文单词，请粘贴英文文章（单词不超过 150 个字符）。")
        document = Document(user_id=user_id, title=title, image_path="", width=0, height=0,
                            ocr_text=payload.text, source="text", words=words)
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
