from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.auth import get_current_user_id
from backend.database import get_session
from backend.services.reading_assistant import AssistantRequest, AssistantResult, respond_to_assistant

router = APIRouter(prefix="/api/assistant")


@router.post("/messages", response_model=AssistantResult)
def message_assistant(payload: AssistantRequest, session: Session = Depends(get_session),
                      user_id: int = Depends(get_current_user_id)):
    return respond_to_assistant(session, payload, user_id)
