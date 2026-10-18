from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.auth import get_current_user_id
from backend.database import get_session
from backend.schemas import OccurrenceRequest
from backend.services.sentences import SentenceResult, analyze_sentence

router = APIRouter(prefix="/api")


@router.post("/sentences/analyze", response_model=SentenceResult)
def sentence_assistance(payload: OccurrenceRequest, session: Session = Depends(get_session),
                        user_id: int = Depends(get_current_user_id)):
    return analyze_sentence(session, payload.occurrence_id, user_id)
