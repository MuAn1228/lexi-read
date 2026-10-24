from datetime import date
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.auth import get_current_user_id
from backend.database import get_session
from backend.schemas_history import HistoryKind, LearningHistoryOut
from backend.services.learning_history import LearningHistoryService


router = APIRouter(prefix="/api")


@router.get("/learning-history", response_model=LearningHistoryOut)
def learning_history(
    days: int = Query(30, ge=7, le=90),
    tz_offset_minutes: int = Query(480, ge=-720, le=840),
    day: date | None = None,
    kind: HistoryKind = "all",
    q: str = Query("", max_length=100),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    session: Session = Depends(get_session),
    user_id: int = Depends(get_current_user_id),
):
    return LearningHistoryService(session, user_id).read(
        days=days, tz_offset_minutes=tz_offset_minutes, day=day,
        kind=kind, q=q, page=page, page_size=page_size,
    )
