from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_serializer


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


class OccurrenceRequest(Schema):
    occurrence_id: int = Field(gt=0)


class AnalysisOut(Schema):
    word: str
    lemma: str
    meaning_in_context: str
    simple_explanation: str
    part_of_speech: str
    source: str
    warning: str | None = None


class Recommendation(Schema):
    word: str
    meaning: str
    reason: str
    score: float


class RecommendationsOut(Schema):
    items: tuple[Recommendation, ...] = Field(max_length=3)
    source: str
    warning: str | None = None
