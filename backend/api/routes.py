from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.database import get_session
from backend.auth import create_guest_session, get_current_user_id
from backend.config import get_settings
from backend.models import WordOccurrence
from backend.agents import VocabularyAgent
from backend.repositories.vocabulary import VocabularyRepository
from backend.schemas import AnalysisOut, DashboardOut, DocumentOut, DocumentSummary, DocumentUpdateRequest, EventOut, FeedbackOut, FeedbackRequest, MarkRequest, OccurrenceRequest, RecommendationRequest, RecommendationsOut, RelationsOut, ReviewOut, TextImportRequest, VocabularyOut, VocabularyRecommendationRequest, WordHistoryOut
from backend.services.feedback import FeedbackService
from backend.services.relations import RelationService
from backend.services.dashboard import DashboardService
from backend.services.review import ReviewService
from backend.services.review_marks import ReviewMarkService
from backend.services.documents import change_document_deleted, create_demo, document_out, import_image, list_documents, require_document, require_current_occurrence, update_document
from backend.services.text_import import import_text
from backend.services.pdf_import import import_pdf
from backend.schemas import UrlImportRequest, ProofreadOut, ProofreadRequest
from backend.services.proofreading import read_proofread, save_proofread, pdf_page_png
from backend.services.url_import import import_url
from backend.services.vocabulary import VocabularyService
from backend.services.llm import LLMService
from backend.services.lemmatization import normalize_word
from backend.public_limits import pdf_ocr_budget, require_llm_budget, require_ocr_budget, require_pdf_preview_budget

router = APIRouter(prefix="/api")


@router.post("/session", status_code=201)
def guest_session(request: Request, response: Response, session: Session = Depends(get_session)):
    if get_settings().app_mode != "public":
        raise HTTPException(404, "本机模式不需要访客身份。")
    from backend.public_limits import check_signup_budget
    check_signup_budget(request, session)
    response.headers["Cache-Control"] = "no-store"
    return create_guest_session(session)


@router.get("/relations", response_model=RelationsOut)
def relations(session: Session = Depends(get_session), user_id: int = Depends(get_current_user_id)):
    return RelationService(session, user_id).read()


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(session: Session = Depends(get_session), user_id: int = Depends(get_current_user_id)):
    return DashboardService(session, user_id).read()


@router.get("/review", response_model=ReviewOut)
def review(limit: int = Query(10, ge=1, le=50), tz_offset_minutes: int = Query(480, ge=-720, le=840),
           vocabulary_ids: list[int] | None = Query(None, max_length=50), session: Session = Depends(get_session),
           user_id: int = Depends(get_current_user_id)):
    if vocabulary_ids is not None and any(value <= 0 for value in vocabulary_ids):
        raise HTTPException(422, "词条编号必须为正整数。")
    return ReviewService(session, user_id).read(limit, tz_offset_minutes, vocabulary_ids)


@router.get("/documents", response_model=list[DocumentSummary])
def documents(q: str | None = Query(None, max_length=200), tag: str | None = Query(None, max_length=20),
              source: Literal["text", "upload", "demo", "pdf", "web"] | None = None, deleted: bool = False,
              sort: Literal["newest", "oldest", "title"] = "newest",
              session: Session = Depends(get_session), user_id: int = Depends(get_current_user_id)):
    return list_documents(session, user_id, q=q, tag=tag, source=source, deleted=deleted, sort=sort)


@router.post("/documents/upload", response_model=DocumentOut, status_code=201,
             dependencies=[Depends(require_ocr_budget)])
@router.post("/documents", response_model=DocumentOut, status_code=201,
             dependencies=[Depends(require_ocr_budget)])
def upload_document(file: UploadFile, session: Session = Depends(get_session),
                    user_id: int = Depends(get_current_user_id)):
    try:
        return document_out(import_image(session, file, user_id), session, user_id)
    finally:
        file.file.close()


@router.post("/documents/demo", response_model=DocumentOut)
def demo_document(session: Session = Depends(get_session), user_id: int = Depends(get_current_user_id)):
    return document_out(create_demo(session, user_id), session, user_id)


@router.post("/documents/text", response_model=DocumentOut, status_code=201)
def text_document(payload: TextImportRequest, session: Session = Depends(get_session),
                  user_id: int = Depends(get_current_user_id)):
    return document_out(import_text(session, payload, user_id), session, user_id)


@router.post("/documents/pdf", response_model=DocumentOut, status_code=201)
def pdf_document(file: UploadFile, request_id: UUID = Form(...),
                 ocr_mode: Literal["auto", "text", "scan"] = Form("auto"), session: Session = Depends(get_session),
                 user_id: int = Depends(get_current_user_id)):
    try:
        return document_out(import_pdf(session, file, request_id, user_id, mode=ocr_mode,
                                       ocr_budget=lambda pages: pdf_ocr_budget(session, user_id, pages)), session, user_id)
    finally:
        file.file.close()


@router.get("/documents/{document_id}", response_model=DocumentOut)
def read_document(document_id: int, session: Session = Depends(get_session),
                  user_id: int = Depends(get_current_user_id)):
    return document_out(require_document(session, document_id, user_id), session, user_id)


@router.post("/documents/url", response_model=DocumentOut, status_code=201)
def url_document(payload: UrlImportRequest, session: Session = Depends(get_session),
                 user_id: int = Depends(get_current_user_id)):
    return document_out(import_url(session, payload, user_id), session, user_id)


@router.get("/documents/{document_id}/image")
def document_image(document_id: int, session: Session = Depends(get_session),
                   user_id: int = Depends(get_current_user_id)):
    doc = require_document(session, document_id, user_id)
    if doc.source in {"text", "pdf", "web"}:
        raise HTTPException(404, "这篇文章没有可用的原图。")
    if not Path(doc.image_path).is_file():
        raise HTTPException(404, "原图文件缺失，请重新导入。")
    return FileResponse(doc.image_path, media_type="image/png", headers={"Cache-Control": "private, no-store"})


@router.get("/documents/{document_id}/pdf")
def document_pdf(document_id: int, session: Session = Depends(get_session),
                 user_id: int = Depends(get_current_user_id)):
    doc = require_document(session, document_id, user_id)
    if doc.source != "pdf":
        raise HTTPException(404, "这篇文章没有 PDF 原文件。")
    if not Path(doc.image_path).is_file():
        raise HTTPException(404, "PDF 原文件缺失，请重新导入。")
    return FileResponse(doc.image_path, media_type="application/pdf", filename=f"article-{doc.id}.pdf",
                        headers={"Cache-Control": "private, no-store"})


@router.get("/documents/{document_id}/proofread", response_model=ProofreadOut)
def proofread_document(document_id: int, response: Response, session: Session = Depends(get_session),
                       user_id: int = Depends(get_current_user_id)):
    response.headers["Cache-Control"] = "private, no-store"
    return read_proofread(session, document_id, user_id)


@router.put("/documents/{document_id}/proofread", response_model=DocumentOut)
def correct_document(document_id: int, payload: ProofreadRequest, response: Response,
                     session: Session = Depends(get_session), user_id: int = Depends(get_current_user_id)):
    response.headers["Cache-Control"] = "private, no-store"
    return document_out(save_proofread(session, document_id, payload, user_id), session, user_id)


@router.get("/documents/{document_id}/pdf/pages/{page_number}/image",
            dependencies=[Depends(require_pdf_preview_budget)])
def document_pdf_page(document_id: int, page_number: int, session: Session = Depends(get_session),
                      user_id: int = Depends(get_current_user_id)):
    return Response(content=pdf_page_png(session, document_id, page_number, user_id), media_type="image/png",
                    headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.put("/documents/{document_id}", response_model=DocumentSummary)
def edit_document(document_id: int, payload: DocumentUpdateRequest, session: Session = Depends(get_session),
                  user_id: int = Depends(get_current_user_id)):
    return update_document(session, document_id, payload, user_id)


@router.delete("/documents/{document_id}", response_model=DocumentSummary)
def trash_document(document_id: int, session: Session = Depends(get_session),
                   user_id: int = Depends(get_current_user_id)):
    return change_document_deleted(session, document_id, user_id, restore=False)


@router.post("/documents/{document_id}/restore", response_model=DocumentSummary)
def restore_document(document_id: int, session: Session = Depends(get_session),
                     user_id: int = Depends(get_current_user_id)):
    return change_document_deleted(session, document_id, user_id, restore=True)


@router.get("/vocabulary", response_model=list[VocabularyOut])
def vocabulary(session: Session = Depends(get_session), user_id: int = Depends(get_current_user_id)):
    return VocabularyRepository(session, user_id).all()


@router.get("/vocabulary/{word}/history", response_model=WordHistoryOut)
def word_history(word: str, session: Session = Depends(get_session), user_id: int = Depends(get_current_user_id)):
    try:
        lemma = normalize_word(word)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    repository = VocabularyRepository(session, user_id)
    entry = repository.get(lemma)
    if entry is None:
        raise HTTPException(404, "该单词尚无词库记录。")
    return WordHistoryOut(vocabulary=entry, last_mark=repository.last_mark(lemma),
                          events=repository.word_history(lemma), maimemo_source=repository.last_maimemo_import(lemma))


@router.put("/vocabulary/{word}", response_model=VocabularyOut)
def mark_word(word: str, payload: MarkRequest, session: Session = Depends(get_session),
              user_id: int = Depends(get_current_user_id)):
    try:
        repository = VocabularyRepository(session, user_id)
        if payload.origin == "review" and payload.request_id is not None:
            return ReviewMarkService(repository).apply(word, payload.status, payload.document_id, payload.request_id)
        # Ordinary marks also modify the schedule. Reserve the writer before
        # checking ownership or reading existing vocabulary to avoid lost state.
        session.execute(text("BEGIN IMMEDIATE"))
        if payload.document_id is not None:
            require_document(session, payload.document_id, user_id)
        metadata = {"origin": "review"} if payload.origin == "review" else None
        return VocabularyService(repository).mark(
            word, payload.status, payload.document_id, metadata=metadata,
        )
    except Exception as exc:
        session.rollback()
        if isinstance(exc, ValueError):
            raise HTTPException(422, str(exc)) from exc
        raise


@router.get("/events", response_model=list[EventOut])
def events(limit: int = Query(100, ge=1, le=1000), session: Session = Depends(get_session),
           user_id: int = Depends(get_current_user_id)):
    return VocabularyRepository(session, user_id).history(limit)


def require_occurrence(session: Session, occurrence_id: int, user_id: int):
    occurrence = session.get(WordOccurrence, occurrence_id)
    if occurrence is None:
        raise HTTPException(404, "未找到该单词在文中的位置。")
    return occurrence, require_document(session, occurrence.document_id, user_id)


@router.post("/words/analyze", response_model=AnalysisOut, dependencies=[Depends(require_llm_budget)])
def analyze_word(payload: OccurrenceRequest, session: Session = Depends(get_session),
                 user_id: int = Depends(get_current_user_id)):
    occurrence, doc = require_occurrence(session, payload.occurrence_id, user_id)
    repository = VocabularyRepository(session, user_id)
    context = VocabularyAgent(repository).build_context(occurrence, doc)
    analysis = LLMService().analyze_word(occurrence.word, occurrence.lemma, occurrence.sentence, doc.ocr_text,
                                       learner_context=context)
    try:
        # A slow model response may finish after another tab trashes this article.
        session.execute(text("BEGIN IMMEDIATE"))
        require_current_occurrence(session, payload.occurrence_id, doc.id, user_id)
        VocabularyService(repository).lookup(occurrence.word, doc.id, analysis.source)
    except Exception:
        session.rollback()
        raise
    return analysis


@router.post("/words/recommend", response_model=RecommendationsOut, dependencies=[Depends(require_llm_budget)])
def recommend_words(payload: RecommendationRequest, session: Session = Depends(get_session),
                    user_id: int = Depends(get_current_user_id)):
    occurrence, doc = require_occurrence(session, payload.occurrence_id, user_id)
    return VocabularyAgent(VocabularyRepository(session, user_id)).recommend(occurrence, doc, payload.relation)


@router.post("/vocabulary/{word}/recommend", response_model=RecommendationsOut,
             dependencies=[Depends(require_llm_budget)])
def expand_vocabulary(word: str, payload: VocabularyRecommendationRequest,
                      session: Session = Depends(get_session), user_id: int = Depends(get_current_user_id)):
    try:
        return VocabularyAgent(VocabularyRepository(session, user_id)).recommend_vocabulary(word, payload.relation)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/recommendations/{batch_id}/feedback", response_model=FeedbackOut)
def recommendation_feedback(batch_id: int, payload: FeedbackRequest, session: Session = Depends(get_session),
                            user_id: int = Depends(get_current_user_id)):
    return FeedbackService(VocabularyRepository(session, user_id)).apply(batch_id, payload)
