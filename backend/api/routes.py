from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database import get_session
from backend.models import Document, WordOccurrence
from backend.agents import VocabularyAgent
from backend.repositories.vocabulary import VocabularyRepository
from backend.schemas import AnalysisOut, DashboardOut, DocumentOut, DocumentSummary, EventOut, MarkRequest, OccurrenceRequest, RecommendationsOut, VocabularyOut, WordHistoryOut
from backend.services.dashboard import DashboardService
from backend.services.documents import create_demo, document_out, document_summary, import_image, require_document
from backend.services.vocabulary import VocabularyService
from backend.services.llm import LLMService
from backend.services.lemmatization import normalize_word

router = APIRouter(prefix="/api")


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(session: Session = Depends(get_session)):
    return DashboardService(session).read()


@router.get("/documents", response_model=list[DocumentSummary])
def documents(session: Session = Depends(get_session)):
    return [document_summary(doc) for doc in session.scalars(select(Document).where(
        Document.user_id == 1).order_by(Document.created_at.desc(), Document.id.desc()))]


@router.post("/documents/upload", response_model=DocumentOut, status_code=201)
@router.post("/documents", response_model=DocumentOut, status_code=201)
def upload_document(file: UploadFile, session: Session = Depends(get_session)):
    try:
        return document_out(import_image(session, file), session)
    finally:
        file.file.close()


@router.post("/documents/demo", response_model=DocumentOut)
def demo_document(session: Session = Depends(get_session)):
    return document_out(create_demo(session), session)


@router.get("/documents/{document_id}", response_model=DocumentOut)
def read_document(document_id: int, session: Session = Depends(get_session)):
    return document_out(require_document(session, document_id), session)


@router.get("/documents/{document_id}/image")
def document_image(document_id: int, session: Session = Depends(get_session)):
    doc = require_document(session, document_id)
    if not Path(doc.image_path).is_file():
        raise HTTPException(404, "原图文件缺失，请重新导入。")
    return FileResponse(doc.image_path, media_type="image/png")


@router.get("/vocabulary", response_model=list[VocabularyOut])
def vocabulary(session: Session = Depends(get_session)):
    return VocabularyRepository(session).all()


@router.get("/vocabulary/{word}/history", response_model=WordHistoryOut)
def word_history(word: str, session: Session = Depends(get_session)):
    try:
        lemma = normalize_word(word)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    repository = VocabularyRepository(session)
    entry = repository.get(lemma)
    if entry is None:
        raise HTTPException(404, "该单词尚无词库记录。")
    return WordHistoryOut(vocabulary=entry, last_mark=repository.last_mark(lemma),
                          events=repository.word_history(lemma))


@router.put("/vocabulary/{word}", response_model=VocabularyOut)
def mark_word(word: str, payload: MarkRequest, session: Session = Depends(get_session)):
    if payload.document_id is not None:
        require_document(session, payload.document_id)
    try:
        return VocabularyService(VocabularyRepository(session)).mark(word, payload.status, payload.document_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/events", response_model=list[EventOut])
def events(limit: int = Query(100, ge=1, le=1000), session: Session = Depends(get_session)):
    return VocabularyRepository(session).history(limit)


def require_occurrence(session: Session, occurrence_id: int):
    occurrence = session.get(WordOccurrence, occurrence_id)
    if occurrence is None:
        raise HTTPException(404, "未找到该单词在文中的位置。")
    return occurrence, require_document(session, occurrence.document_id)


@router.post("/words/analyze", response_model=AnalysisOut)
def analyze_word(payload: OccurrenceRequest, session: Session = Depends(get_session)):
    occurrence, doc = require_occurrence(session, payload.occurrence_id)
    analysis = LLMService().analyze_word(occurrence.word, occurrence.lemma, occurrence.sentence, doc.ocr_text)
    VocabularyService(VocabularyRepository(session)).lookup(occurrence.word, doc.id, analysis.source)
    return analysis


@router.post("/words/recommend", response_model=RecommendationsOut)
def recommend_words(payload: OccurrenceRequest, session: Session = Depends(get_session)):
    occurrence, doc = require_occurrence(session, payload.occurrence_id)
    return VocabularyAgent(VocabularyRepository(session)).recommend(occurrence, doc)
