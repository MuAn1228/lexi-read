"""Useful, descriptive views of the user's saved vocabulary associations."""
from datetime import datetime
from typing import Literal

from pydantic import Field

from backend.schemas import Schema, Status


StoredRelation = Literal[
    "WORD_FAMILY", "SYNONYM", "ANTONYM", "CONCEPT", "COLLOCATION", "DOMAIN_RELATED",
]
RelationFilter = Literal[
    "all", "WORD_FAMILY", "SYNONYM", "ANTONYM", "CONCEPT", "COLLOCATION", "DOMAIN_RELATED",
]
RelationScope = Literal["all", "unfamiliar"]


class RelationWord(Schema):
    vocabulary_id: int | None = None
    status: Status | None = None
    meaning_summary: str | None = None


class RelationContext(Schema):
    document_id: int
    document_title: str
    occurrence_id: int
    sentence: str


class RelationWorkspaceItem(Schema):
    id: int
    source_word: str
    target_word: str
    relation_type: StoredRelation
    source: RelationWord
    target: RelationWord
    meaning: str | None = None
    reason: str | None = None
    provenance: Literal["llm", "offline", "unavailable"] = "unavailable"
    batch_id: int | None = None
    useful: bool | None = None
    context: RelationContext | None = None
    updated_at: datetime


class RelationWorkspaceSummary(Schema):
    total_relations: int = Field(ge=0)
    source_words: int = Field(ge=0)
    unfamiliar_targets: int = Field(ge=0)
    practice_pairs: int = Field(ge=0)


class RelationSource(Schema):
    word: str
    meaning_summary: str | None = None
    status: Status | None = None
    relation_count: int = Field(ge=1)


class RelationPagination(Schema):
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=50)
    total: int = Field(ge=0)
    total_pages: int = Field(ge=1)


class RelationWorkspaceFilters(Schema):
    q: str = ""
    relation: RelationFilter = "all"
    scope: RelationScope = "all"
    include_dismissed: bool = False


class RelationWorkspaceOut(Schema):
    summary: RelationWorkspaceSummary
    sources: list[RelationSource]
    items: list[RelationWorkspaceItem]
    pagination: RelationPagination
    filters: RelationWorkspaceFilters
