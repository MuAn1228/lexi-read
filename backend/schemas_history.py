"""Read-only, descriptive learning history responses."""
from datetime import date, datetime
from typing import Literal

from pydantic import Field

from backend.schemas import Schema, Status


HistoryKind = Literal["all", "review", "lookup", "mark", "related"]


class HistoryRange(Schema):
    days: int
    start_date: date
    end_date: date


class HistorySummary(Schema):
    active_days: int = 0
    studied_words: int = 0
    reviewed_words: int = 0
    lookup_count: int = 0
    mark_count: int = 0
    total_actions: int = 0
    due_total: int = 0


class HistoryDay(Schema):
    date: date
    words: int = 0
    reviews: int = 0
    lookups: int = 0
    marks: int = 0
    actions: int = 0


class HistoryContext(Schema):
    document_id: int
    document_title: str
    occurrence_id: int
    sentence: str


class HistoryFocusWord(Schema):
    vocabulary_id: int
    word: str
    lemma: str
    status: Status
    meaning_summary: str | None = None
    unfamiliar_count: int
    lookup_count: int
    review_unfamiliar_count: int
    last_at: datetime
    context: HistoryContext | None = None


class HistoryEntry(Schema):
    key: str
    date: date
    word: str
    lemma: str
    vocabulary_id: int | None = None
    status: Status | None = None
    meaning_summary: str | None = None
    last_at: datetime
    actions: int
    review_count: int
    lookup_count: int
    mark_count: int
    related_count: int
    last_event_type: str
    last_was_review: bool
    context: HistoryContext | None = None


class HistoryPagination(Schema):
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=50)
    total: int = Field(ge=0)
    total_pages: int = Field(ge=1)


class HistoryFilters(Schema):
    day: date | None = None
    kind: HistoryKind = "all"
    q: str = ""


class LearningHistoryOut(Schema):
    as_of: datetime
    range: HistoryRange
    summary: HistorySummary
    days: list[HistoryDay]
    focus_words: list[HistoryFocusWord]
    entries: list[HistoryEntry]
    pagination: HistoryPagination
    filters: HistoryFilters
