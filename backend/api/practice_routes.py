from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.auth import get_current_user_id
from backend.database import get_session
from backend.services.practice_generation import (PracticeRequest, PracticeResult, PracticeTargetsResult,
                                                  generate_practice, list_practice_targets)

router = APIRouter(prefix="/api/practice")


@router.get("/targets", response_model=PracticeTargetsResult)
def targets(session: Session = Depends(get_session), user_id: int = Depends(get_current_user_id)):
    return list_practice_targets(session, user_id)


@router.post("/generate", response_model=PracticeResult)
def generate(payload: PracticeRequest, session: Session = Depends(get_session),
             user_id: int = Depends(get_current_user_id)):
    return generate_practice(session, payload, user_id)
