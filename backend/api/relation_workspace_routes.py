from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.auth import get_current_user_id
from backend.database import get_session
from backend.schemas_relations import RelationFilter, RelationScope, RelationWorkspaceOut
from backend.services.relation_workspace import RelationWorkspaceService


router = APIRouter(prefix="/api")


@router.get("/relation-workspace", response_model=RelationWorkspaceOut)
def relation_workspace(
    q: str = Query("", max_length=100),
    relation: RelationFilter = "all",
    scope: RelationScope = "all",
    include_dismissed: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=50),
    session: Session = Depends(get_session),
    user_id: int = Depends(get_current_user_id),
):
    return RelationWorkspaceService(session, user_id).read(
        q=q, relation=relation, scope=scope, include_dismissed=include_dismissed,
        page=page, page_size=page_size,
    )
