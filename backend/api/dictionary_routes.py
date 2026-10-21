from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from backend.auth import get_current_user_id
from backend.database import get_session
from backend.services.dictionary import DictionaryResult, explain_dictionary, read_dictionary

router = APIRouter(prefix="/api/dictionary")


@router.get("/{word}", response_model=DictionaryResult)
def dictionary(word: str, response: Response, user_id: int = Depends(get_current_user_id)):
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return read_dictionary(word, user_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/{word}/explain", response_model=DictionaryResult)
def explanation(word: str, response: Response, session: Session = Depends(get_session),
                user_id: int = Depends(get_current_user_id)):
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return explain_dictionary(word, user_id, session)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
