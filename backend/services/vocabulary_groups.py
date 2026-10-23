"""Atomic, user-scoped word collections; these never change learning evidence."""
import unicodedata
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from backend.models import Vocabulary
from backend.models.vocabulary_groups import VocabularyGroup, VocabularyGroupMember


MAX_GROUPS = 50
VocabularyId = Annotated[int, Field(strict=True, gt=0, le=2**63 - 1)]


def normalize_group_name(value: str) -> str:
    # Check before stripping, so trailing newlines and invisible controls do not
    # disappear and silently change the meaning of a submitted name.
    if not isinstance(value, str) or any(
        unicodedata.category(char).startswith("C") or unicodedata.category(char) in {"Zl", "Zp"}
        for char in value
    ):
        raise ValueError("分组名称不能包含换行、控制字符或不可见字符。")
    name = unicodedata.normalize("NFKC", value).strip()
    if not 1 <= len(name) <= 30:
        raise ValueError("分组名称需要 1～30 个字符。")
    return name


class GroupNameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(strict=True, max_length=200)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return normalize_group_name(value)


class GroupMembersRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vocabulary_ids: list[VocabularyId] = Field(min_length=1, max_length=200)
    action: Literal["add", "remove"]


class GroupOut(BaseModel):
    id: int
    name: str
    vocabulary_ids: list[int]


class GroupsOut(BaseModel):
    groups: list[GroupOut]


def require_group(session: Session, group_id: int, user_id: int) -> VocabularyGroup:
    group = session.scalar(select(VocabularyGroup).where(
        VocabularyGroup.id == group_id, VocabularyGroup.user_id == user_id))
    if group is None:
        raise HTTPException(404, "未找到这个词库分组。")
    return group


def group_out(session: Session, group: VocabularyGroup) -> GroupOut:
    ids = session.scalars(select(VocabularyGroupMember.vocabulary_id).join(
        Vocabulary, Vocabulary.id == VocabularyGroupMember.vocabulary_id,
    ).where(VocabularyGroupMember.group_id == group.id,
            Vocabulary.user_id == group.user_id).order_by(VocabularyGroupMember.vocabulary_id)).all()
    return GroupOut(id=group.id, name=group.name, vocabulary_ids=list(ids))


def list_groups(session: Session, user_id: int) -> GroupsOut:
    groups = session.scalars(select(VocabularyGroup).where(
        VocabularyGroup.user_id == user_id).order_by(VocabularyGroup.id)).all()
    # Two queries for the complete list, instead of one per group.
    memberships = session.execute(select(
        VocabularyGroupMember.group_id, VocabularyGroupMember.vocabulary_id,
    ).join(VocabularyGroup, VocabularyGroup.id == VocabularyGroupMember.group_id).join(
        Vocabulary, Vocabulary.id == VocabularyGroupMember.vocabulary_id,
    ).where(VocabularyGroup.user_id == user_id, Vocabulary.user_id == user_id)
      .order_by(VocabularyGroupMember.vocabulary_id)).all()
    members: dict[int, list[int]] = {}
    for group_id, vocabulary_id in memberships:
        members.setdefault(group_id, []).append(vocabulary_id)
    return GroupsOut(groups=[GroupOut(id=group.id, name=group.name,
                                    vocabulary_ids=members.get(group.id, [])) for group in groups])


def create_group(session: Session, user_id: int, name: str) -> tuple[GroupOut, bool]:
    name = normalize_group_name(name)
    key = name.casefold()
    try:
        session.execute(text("BEGIN IMMEDIATE"))
        existing = session.scalar(select(VocabularyGroup).where(
            VocabularyGroup.user_id == user_id, VocabularyGroup.name_key == key))
        if existing is not None:
            result = group_out(session, existing)
            session.commit()
            return result, False
        count = session.scalar(select(func.count()).select_from(VocabularyGroup).where(
            VocabularyGroup.user_id == user_id))
        if count >= MAX_GROUPS:
            raise HTTPException(409, "最多创建 50 个词库分组，请先整理已有分组。")
        group = VocabularyGroup(user_id=user_id, name=name, name_key=key)
        session.add(group)
        session.flush()
        result = GroupOut(id=group.id, name=group.name, vocabulary_ids=[])
        session.commit()
        return result, True
    except Exception:
        session.rollback()
        raise


def rename_group(session: Session, group_id: int, user_id: int, name: str) -> GroupOut:
    name = normalize_group_name(name)
    key = name.casefold()
    try:
        session.execute(text("BEGIN IMMEDIATE"))
        group = require_group(session, group_id, user_id)
        duplicate = session.scalar(select(VocabularyGroup.id).where(
            VocabularyGroup.user_id == user_id, VocabularyGroup.name_key == key,
            VocabularyGroup.id != group.id))
        if duplicate is not None:
            raise HTTPException(409, "已有同名分组，请换一个名称。")
        group.name, group.name_key = name, key
        session.flush()
        result = group_out(session, group)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise


def delete_group(session: Session, group_id: int, user_id: int) -> None:
    try:
        session.execute(text("BEGIN IMMEDIATE"))
        group = require_group(session, group_id, user_id)
        # Explicit deletion also works with old SQLite connections that have not
        # enabled foreign keys; neither operation touches the vocabulary table.
        session.execute(delete(VocabularyGroupMember).where(VocabularyGroupMember.group_id == group.id))
        session.delete(group)
        session.commit()
    except Exception:
        session.rollback()
        raise


def update_members(session: Session, group_id: int, user_id: int, payload: GroupMembersRequest) -> GroupOut:
    ids = set(payload.vocabulary_ids)
    try:
        session.execute(text("BEGIN IMMEDIATE"))
        group = require_group(session, group_id, user_id)
        owned = set(session.scalars(select(Vocabulary.id).where(
            Vocabulary.user_id == user_id, Vocabulary.id.in_(ids))).all())
        if owned != ids:
            raise HTTPException(404, "部分单词不存在或不属于当前词库，请刷新后重试。")
        if payload.action == "remove":
            session.execute(delete(VocabularyGroupMember).where(
                VocabularyGroupMember.group_id == group.id, VocabularyGroupMember.vocabulary_id.in_(ids)))
        else:
            present = set(session.scalars(select(VocabularyGroupMember.vocabulary_id).where(
                VocabularyGroupMember.group_id == group.id, VocabularyGroupMember.vocabulary_id.in_(ids))).all())
            session.add_all(VocabularyGroupMember(group_id=group.id, vocabulary_id=entry_id)
                            for entry_id in sorted(ids - present))
        session.flush()
        result = group_out(session, group)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
