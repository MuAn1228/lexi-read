"""Independent dictionary-sense learning and review endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from sqlalchemy.orm import Session

from backend.auth import get_current_user_id
from backend.database import get_session
from backend.schemas import (SenseMarkRequest, SenseReviewOut, VocabularySenseOut,
                             VocabularySensesOut)
from backend.services.sense_learning import SenseLearningService
from backend.services.sense_review import SenseReviewService


router = APIRouter(prefix="/api")


@router.get("/vocabulary/{word}/senses", response_model=VocabularySensesOut)
def vocabulary_senses(word: str, response: Response, session: Session = Depends(get_session),
                      user_id: int = Depends(get_current_user_id)):
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return SenseLearningService(session, user_id).catalog(word)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put("/vocabulary/{word}/senses/{sense_key}", response_model=VocabularySenseOut)
def mark_vocabulary_sense(payload: SenseMarkRequest, word: str,
                          sense_key: str = Path(pattern=r"^[a-f0-9]{64}$"),
                          session: Session = Depends(get_session),
                          user_id: int = Depends(get_current_user_id)):
    try:
        return SenseLearningService(session, user_id).mark(word, sense_key, payload)
    except ValueError as exc:
        session.rollback()
        raise HTTPException(422, str(exc)) from exc


@router.get("/sense-review", response_model=SenseReviewOut)
def sense_review(limit: int = Query(10, ge=1, le=50),
                 tz_offset_minutes: int = Query(480, ge=-720, le=840),
                 plan_minutes: int | None = Query(None, ge=10, le=30),
                 sense_ids: list[int] | None = Query(None, max_length=50),
                 session: Session = Depends(get_session),
                 user_id: int = Depends(get_current_user_id)):
    if plan_minutes is not None and plan_minutes not in {10, 20, 30}:
        raise HTTPException(422, "每日学习时长只能选择 10、20 或 30 分钟。")
    if sense_ids is not None and any(value <= 0 for value in sense_ids):
        raise HTTPException(422, "义项编号必须为正整数。")
    if sense_ids is not None and len(sense_ids) != len(set(sense_ids)):
        raise HTTPException(422, "义项编号不能重复。")
    return SenseReviewService(session, user_id).read(
        limit, tz_offset_minutes, sense_ids, plan_minutes,
    )
