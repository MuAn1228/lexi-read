"""Manage personal vocabulary collections without changing learning state."""
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response
from sqlalchemy.orm import Session

from backend.auth import get_current_user_id
from backend.database import get_session
from backend.services.vocabulary_groups import (
    GroupMembersRequest, GroupNameRequest, GroupOut, GroupsOut,
    create_group, delete_group, list_groups, rename_group, update_members,
)


router = APIRouter(prefix="/api/vocabulary-groups")
GroupId = Annotated[int, Path(gt=0, le=2**63 - 1)]


@router.get("", response_model=GroupsOut)
def get_groups(session: Session = Depends(get_session), user_id: int = Depends(get_current_user_id)):
    return list_groups(session, user_id)


@router.post("", response_model=GroupOut, status_code=201)
def post_group(payload: GroupNameRequest, response: Response,
               session: Session = Depends(get_session), user_id: int = Depends(get_current_user_id)):
    result, created = create_group(session, user_id, payload.name)
    if not created:
        response.status_code = 200
    return result


@router.put("/{group_id}", response_model=GroupOut)
def put_group(group_id: GroupId, payload: GroupNameRequest,
              session: Session = Depends(get_session), user_id: int = Depends(get_current_user_id)):
    return rename_group(session, group_id, user_id, payload.name)


@router.delete("/{group_id}", status_code=204)
def remove_group(group_id: GroupId, session: Session = Depends(get_session),
                 user_id: int = Depends(get_current_user_id)):
    delete_group(session, group_id, user_id)
    return Response(status_code=204)


@router.post("/{group_id}/members", response_model=GroupOut)
def post_members(group_id: GroupId, payload: GroupMembersRequest,
                 session: Session = Depends(get_session), user_id: int = Depends(get_current_user_id)):
    return update_members(session, group_id, user_id, payload)
