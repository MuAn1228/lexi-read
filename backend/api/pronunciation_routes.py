import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, StrictBool, StrictStr

from backend.auth import get_current_user_id
from backend.services.pronunciation import (PronunciationBusy, PronunciationUnavailable, RecordingNotFound,
                                            pronunciation_status, synthesize_with_source)

router = APIRouter(prefix="/api/pronunciation")


class PronunciationRequest(BaseModel):
    text: StrictStr = Field(min_length=1, max_length=4000)
    accent: Literal["en-US", "en-GB"] = "en-US"
    allow_word_synthesis: StrictBool = False


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
        data, voice, source, attribution = synthesize_with_source(
            payload.text, payload.accent, allow_word_synthesis=payload.allow_word_synthesis)
    except RecordingNotFound as exc:
        raise HTTPException(404, detail={"code": "recording_not_found", "message": str(exc)},
                            headers={"Cache-Control": "private, no-store"}) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except PronunciationBusy as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": "1", "Cache-Control": "private, no-store"}) from exc
    except PronunciationUnavailable as exc:
        raise HTTPException(503, str(exc), headers={"Cache-Control": "private, no-store"}) from exc
    return Response(content=data, media_type="audio/wav", headers={
        "X-Pronunciation-Voice": voice, "X-Pronunciation-Source": source,
        "X-Pronunciation-Accent": voice[len("wiktionary-"):] if source == "dictionary" else payload.accent,
        "X-Pronunciation-Attribution": json.dumps(attribution, ensure_ascii=True, separators=(",", ":")) if attribution else "",
        "Cache-Control": "private, no-store",
    })
