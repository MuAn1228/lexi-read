from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.auth import get_current_user_id
from backend.database import get_session
from backend.services.review_meaning import (ReviewMeaningRequest, ReviewMeaningResult,
                                             reveal_review_meaning)

router = APIRouter(prefix="/api/review")


@router.post("/meaning", response_model=ReviewMeaningResult)
def reveal_meaning(payload: ReviewMeaningRequest, session: Session = Depends(get_session),
                   user_id: int = Depends(get_current_user_id)):
    return reveal_review_meaning(session, payload.vocabulary_id, payload.occurrence_id, user_id)
