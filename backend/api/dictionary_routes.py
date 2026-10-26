from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, StrictBool
from sqlalchemy.orm import Session

from backend.auth import get_current_user_id
from backend.config import get_settings
from backend.database import get_session
from backend.services.ai_stream import stream_response
from backend.services.dictionary import (DictionaryResult, explain_dictionary, read_dictionary,
                                         fetch_dictionary_annotations)
from backend.services.dictionary_suggestions import DictionarySuggestions, read_suggestions

router = APIRouter(prefix="/api/dictionary")


class AnnotationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh: StrictBool = False


@router.get("/suggestions/query", response_model=DictionarySuggestions)
def suggestions(response: Response, q: str = "", mode: str = "prefix",
                user_id: int = Depends(get_current_user_id)):
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return read_suggestions(q, mode)
    except ValueError as exc:
        raise HTTPException(422, str(exc), headers={"Cache-Control": "private, no-store"}) from exc


@router.get("/{word}", response_model=DictionaryResult)
def dictionary(word: str, response: Response, user_id: int = Depends(get_current_user_id)):
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return read_dictionary(word, user_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/{word}/annotations", response_model=DictionaryResult)
def annotations(word: str, response: Response, payload: AnnotationRequest | None = None,
                user_id: int = Depends(get_current_user_id)):
    response.headers["Cache-Control"] = "private, no-store"
    if get_settings().app_mode != "local":
        raise HTTPException(404, "个人墨墨注解仅在本地版开放。")
    try:
        return fetch_dictionary_annotations(word, user_id, refresh=payload.refresh if payload else False)
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


@router.post("/{word}/explain-stream")
async def explanation_stream(word: str, request: Request, session: Session = Depends(get_session),
                             user_id: int = Depends(get_current_user_id)):
    def operation(worker_session):
        try:
            return explain_dictionary(word, user_id, worker_session)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return await stream_response(request, session.get_bind(), operation, kind="dictionary")
