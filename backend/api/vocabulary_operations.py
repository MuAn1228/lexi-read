"""Bulk vocabulary actions stay separate from ordinary single-word marking."""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from backend.auth import get_current_user_id
from backend.database import get_session
from backend.repositories.vocabulary import VocabularyRepository
from backend.schemas import Schema, Status, VocabularyOut
from backend.services.vocabulary_operations import VocabularyBatchService


class VocabularyBatchItem(Schema):
    model_config = ConfigDict(from_attributes=True, frozen=True, extra="forbid")
    id: int = Field(strict=True, gt=0, le=9_223_372_036_854_775_807)
    updated_at: datetime

    @field_validator("updated_at", mode="before")
    @classmethod
    def iso_timestamp(cls, value):
        if not isinstance(value, (str, datetime)):
            raise ValueError("请提供词条的 ISO 时间戳。")
        return value


class VocabularyBatchRequest(Schema):
    model_config = ConfigDict(from_attributes=True, frozen=True, extra="forbid")
    request_id: UUID
    status: Status
    items: list[VocabularyBatchItem] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("批量操作不能包含重复词条。")
        return self


class VocabularyBatchOut(Schema):
    operation_id: UUID
    items: list[VocabularyOut]
    changed_count: int = Field(ge=0, le=200)
    undone: bool


router = APIRouter(prefix="/api/vocabulary")


@router.post("/batch", response_model=VocabularyBatchOut)
def batch_mark(payload: VocabularyBatchRequest, response: Response,
               session: Session = Depends(get_session), user_id: int = Depends(get_current_user_id)):
    response.headers["Cache-Control"] = "private, no-store"
    return VocabularyBatchService(VocabularyRepository(session, user_id)).apply(
        payload.request_id, payload.status, [(item.id, item.updated_at) for item in payload.items],
    )


@router.post("/batch/{operation_id}/undo", response_model=VocabularyBatchOut)
def undo_batch(operation_id: UUID, response: Response,
               session: Session = Depends(get_session), user_id: int = Depends(get_current_user_id)):
    response.headers["Cache-Control"] = "private, no-store"
    return VocabularyBatchService(VocabularyRepository(session, user_id)).undo(operation_id)
