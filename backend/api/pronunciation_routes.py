from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, StrictStr

from backend.auth import get_current_user_id
from backend.services.pronunciation import (PronunciationBusy, PronunciationUnavailable,
                                            pronunciation_status, synthesize)

router = APIRouter(prefix="/api/pronunciation")


class PronunciationRequest(BaseModel):
    text: StrictStr = Field(min_length=1, max_length=4000)
    accent: Literal["en-US", "en-GB"] = "en-US"


class PronunciationStatus(BaseModel):
    available: bool
    engine: Literal["piper", "system"]
    reason: str | None


@router.get("/status", response_model=PronunciationStatus)
def status(response: Response, user_id: int = Depends(get_current_user_id)):
    response.headers["Cache-Control"] = "private, no-store"
    return pronunciation_status()


@router.post("")
def pronunciation(payload: PronunciationRequest, user_id: int = Depends(get_current_user_id)):
    try:
        data, voice = synthesize(payload.text, payload.accent)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except PronunciationBusy as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": "1", "Cache-Control": "private, no-store"}) from exc
    except PronunciationUnavailable as exc:
        raise HTTPException(503, str(exc), headers={"Cache-Control": "private, no-store"}) from exc
    return Response(content=data, media_type="audio/wav", headers={
        "X-Pronunciation-Voice": voice, "Cache-Control": "private, no-store",
    })
