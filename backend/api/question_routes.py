from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from backend.auth import get_current_user_id
from backend.database import get_session
from backend.services.article_questions import (ArticleQuestionRequest, ArticleQuestionResult,
                                                answer_article_question)

router = APIRouter(prefix="/api/documents")


@router.post("/{document_id}/questions", response_model=ArticleQuestionResult)
def ask_article(document_id: Annotated[int, Path(gt=0, le=2**63 - 1)], payload: ArticleQuestionRequest,
                session: Session = Depends(get_session), user_id: int = Depends(get_current_user_id)):
    return answer_article_question(session, document_id, payload, user_id)
