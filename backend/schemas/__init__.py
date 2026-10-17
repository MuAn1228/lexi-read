from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator


class Schema(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    @field_serializer("*", when_used="json", check_fields=False)
    def serialize_dates(self, value):
        # SQLite returns naive UTC timestamps. The browser must not treat them as local time.
        if isinstance(value, datetime):
            return value.replace(tzinfo=value.tzinfo or timezone.utc).isoformat()
        return value


class Status(str, Enum):
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"
    UNCERTAIN = "UNCERTAIN"


class VocabularyOut(Schema):
    id: int
    user_id: int
    word: str
    lemma: str
    status: Status | None
    mastery_score: float | None
    seen_count: int
    lookup_count: int
    created_at: datetime
    updated_at: datetime
    first_seen_at: datetime | None
    last_seen_at: datetime | None


class MarkRequest(Schema):
    status: Status
    document_id: int | None = None
    origin: Literal["review"] | None = None
    request_id: UUID | None = None

    @model_validator(mode="after")
    def validate_review_request_id(self):
        if self.request_id is not None and self.origin != "review":
            raise ValueError("request_id 仅适用于 origin 为 review 的复习标记。")
        return self


class ReviewOccurrence(Schema):
    id: int
    document_id: int
    document_title: str
    word: str
    sentence: str


class ReviewItem(Schema):
    vocabulary: VocabularyOut
    occurrence: ReviewOccurrence | None
    last_reviewed_at: datetime | None


class ReviewOut(Schema):
    total: int = Field(ge=0)
    items: list[ReviewItem]


class WordOut(Schema):
    id: int
    word: str
    lemma: str
    sentence: str
    bbox: tuple[float, float, float, float]
    line: int
    confidence: float
    bbox_source: str
    status: Status | None = None


class DocumentSummary(Schema):
    id: int
    title: str
    image_url: str
    width: int
    height: int
    source: str
    created_at: datetime
    word_count: int


class DocumentOut(DocumentSummary):
    ocr_text: str
    words: tuple[WordOut, ...]


class EventOut(Schema):
    id: int
    user_id: int
    word: str
    lemma: str
    event_type: str
    document_id: int | None
    details: dict
    created_at: datetime


class WordHistoryOut(Schema):
    vocabulary: VocabularyOut
    last_mark: EventOut | None
    events: list[EventOut]


class DashboardSummary(Schema):
    total: int = Field(ge=0)
    known: int = Field(ge=0)
    unknown: int = Field(ge=0)
    uncertain: int = Field(ge=0)
    unassessed: int = Field(ge=0)
    new_unknown_7d: int = Field(ge=0)
    lookups_7d: int = Field(ge=0)
    document_count: int = Field(ge=0)
    scored_count: int = Field(ge=0)


class DashboardWord(Schema):
    vocabulary: VocabularyOut
    recent_seen_count: int = Field(ge=0)
    first_unfamiliar_at: datetime | None


class LearningProfile(Schema):
    assessed_count: int = Field(ge=0)
    assessment_coverage: float = Field(ge=0, le=1)
    unscored_count: int = Field(ge=0)


class DashboardOut(Schema):
    summary: DashboardSummary
    recent_frequent_unknown: list[DashboardWord]
    repeated_lookups: list[DashboardWord]
    recent_unknown: list[DashboardWord]
    longstanding_unknown: list[DashboardWord]
    lowest_mastery: list[DashboardWord]
    most_seen_unknown: list[DashboardWord]
    most_queried: list[DashboardWord]
    profile: LearningProfile


class OccurrenceRequest(Schema):
    occurrence_id: int = Field(gt=0)


class AnalysisOut(Schema):
    word: str
    lemma: str
    meaning_in_context: str
    simple_explanation: str
    part_of_speech: str
    importance: float | None = Field(ge=0, le=1, allow_inf_nan=False)
    source: str
    warning: str | None = None


class Relation(str, Enum):
    WORD_FAMILY = "word_family"
    SYNONYM = "synonym"
    ANTONYM = "antonym"
    CONCEPT = "concept"
    COLLOCATION = "collocation"
    DOMAIN_RELATED = "domain_related"


class Recommendation(Schema):
    word: str
    meaning: str
    reason: str
    relation: Relation
    priority: float = Field(ge=0, le=1, allow_inf_nan=False)
    score: float = Field(ge=0, le=1, allow_inf_nan=False)


class RecommendationsOut(Schema):
    batch_id: int
    items: tuple[Recommendation, ...] = Field(max_length=3)
    source: str
    warning: str | None = None


class FeedbackRequest(Schema):
    word: str = Field(min_length=1, max_length=200)
    action: Literal["SHOWN", "VIEWED", "KNOWN", "UNKNOWN", "USEFUL", "NOT_USEFUL"]
    request_id: UUID


class FeedbackOut(Schema):
    batch_id: int
    word: str
    shown: bool
    viewed: bool
    status: Literal["KNOWN", "UNKNOWN"] | None
    useful: bool | None
    vocabulary: VocabularyOut | None


class VocabularyRelationOut(Schema):
    id: int
    source_word: str
    target_word: str
    relation_type: Literal["WORD_FAMILY", "SYNONYM", "ANTONYM", "CONCEPT", "COLLOCATION", "DOMAIN_RELATED"]
    weight: float = Field(ge=0, le=1)
    created_at: datetime
    updated_at: datetime


class RecommendationMetrics(Schema):
    generated_items: int = Field(ge=0)
    shown_items: int = Field(ge=0)
    viewed_items: int = Field(ge=0)
    added_unknown_items: int = Field(ge=0)
    useful_items: int = Field(ge=0)
    not_useful_items: int = Field(ge=0)


class RelationsOut(Schema):
    items: list[VocabularyRelationOut]
    total: int = Field(ge=0)
    metrics: RecommendationMetrics
