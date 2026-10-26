from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from backend.auth import get_current_user_id
from backend.database import get_session
from backend.services.ai_stream import stream_response
from backend.services.reading_assistant import AssistantRequest, AssistantResult, respond_to_assistant

router = APIRouter(prefix="/api/assistant")


@router.post("/messages", response_model=AssistantResult)
def message_assistant(payload: AssistantRequest, session: Session = Depends(get_session),
                      user_id: int = Depends(get_current_user_id)):
    return respond_to_assistant(session, payload, user_id)


@router.post("/messages/stream")
async def message_assistant_stream(payload: AssistantRequest, request: Request,
                                   session: Session = Depends(get_session),
                                   user_id: int = Depends(get_current_user_id)):
    return await stream_response(request, session.get_bind(),
        lambda worker_session: respond_to_assistant(worker_session, payload, user_id),
        kind="assistant", preview_enabled=payload.mode != "quiz")
