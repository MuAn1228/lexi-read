from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import UUID
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator


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
    target_start: int | None = None
    target_end: int | None = None


class ReviewItem(Schema):
    vocabulary: VocabularyOut
    occurrence: ReviewOccurrence | None
    last_reviewed_at: datetime | None
    due_at: datetime | None
    known_streak: int = Field(ge=0, le=5)
    next_intervals_seconds: dict[Status, int]


class ReviewOut(Schema):
    total: int = Field(ge=0)
    items: list[ReviewItem]
    reviewed_today: int = Field(ge=0)
    later_today: int = Field(ge=0)
    next_due_at: datetime | None
    as_of: datetime


class WordOut(Schema):
    id: int
    word: str
    lemma: str
    sentence: str
    bbox: tuple[float, float, float, float] | None
    line: int
    confidence: float | None
    bbox_source: str
    text_start: int | None = None
    text_end: int | None = None
    status: Status | None = None


class DocumentSummary(Schema):
    id: int
    title: str
    image_url: str | None
    width: int
    height: int
    source: str
    source_url: str | None = None
    created_at: datetime
    word_count: int
    tags: list[str] = Field(default_factory=list)
    bookmark_occurrence_id: int | None = None
    deleted_at: datetime | None = None
    excerpt: str = ""
    page_count: int = 1
    pdf_url: str | None = None


class DocumentUpdateRequest(Schema):
    model_config = ConfigDict(from_attributes=True, frozen=True, extra="forbid")
    title: str | None = Field(default=None, strict=True, max_length=250)
    tags: list[str] | None = Field(default=None, max_length=10)
    bookmark_occurrence_id: int | None = Field(default=None, strict=True, gt=0, le=9_223_372_036_854_775_807)

    @field_validator("title", mode="before")
    @classmethod
    def validate_title(cls, value):
        if not isinstance(value, str):
            raise ValueError("标题必须是文字。")
        if any(unicodedata.category(char) in {"Cc", "Cs", "Zl", "Zp"} for char in value):
            raise ValueError("标题不能包含换行或控制字符。")
        if not value.strip():
            raise ValueError("标题不能为空。")
        return value.strip()

    @field_validator("tags", mode="before")
    @classmethod
    def validate_tags(cls, value):
        if not isinstance(value, list) or len(value) > 10:
            raise ValueError("最多可以添加 10 个标签。")
        result, seen = [], set()
        for tag in value:
            if not isinstance(tag, str):
                raise ValueError("标签必须是文字。")
            if any(unicodedata.category(char) in {"Cc", "Cs", "Zl", "Zp"} for char in tag):
                raise ValueError("标签不能包含换行或控制字符。")
            tag = tag.strip()
            if not 1 <= len(tag) <= 20:
                raise ValueError("每个标签需要 1 到 20 个字符。")
            if tag.casefold() not in seen:
                result.append(tag)
                seen.add(tag.casefold())
        return result

    @model_validator(mode="after")
    def validate_patch(self):
        if not self.model_fields_set:
            raise ValueError("请提供要修改的文章信息。")
        return self


class DocumentPage(Schema):
    number: int = Field(ge=1)
    text_start: int = Field(ge=0)
    text_end: int = Field(ge=0)


class DocumentOut(DocumentSummary):
    ocr_text: str
    words: tuple[WordOut, ...]
    pages: list[DocumentPage] = Field(default_factory=list)


class ProofreadPage(Schema):
    model_config = ConfigDict(from_attributes=True, frozen=True, extra="forbid")
    number: int = Field(strict=True, ge=1, le=100)
    text: str = Field(strict=True, max_length=200_000)

    @field_validator("text", mode="before")
    @classmethod
    def valid_text(cls, value):
        if isinstance(value, str):
            value = value.replace("\r\n", "\n").replace("\r", "\n")
            if any(unicodedata.category(char) in {"Cc", "Cs"} and char not in "\n\t" for char in value):
                raise ValueError("校对正文不能包含控制字符，请使用纯文本。")
        return value


class ProofreadOut(Schema):
    document_id: int
    revision: str
    pages: list[ProofreadPage]
    max_characters: int = 200_000


class ProofreadRequest(Schema):
    model_config = ConfigDict(from_attributes=True, frozen=True, extra="forbid")
    request_id: UUID
    base_revision: str = Field(strict=True, pattern=r"^[a-f0-9]{64}$")
    pages: list[ProofreadPage] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def valid_pages(self):
        if [page.number for page in self.pages] != list(range(1, len(self.pages) + 1)):
            raise ValueError("请按原页码顺序提交全部 PDF 页面，不能增加、删除或重复页面。")
        if sum(len(page.text) for page in self.pages) + 2 * (len(self.pages) - 1) > 200_000:
            raise ValueError("校对后的全文不能超过 200,000 个字符（含分页分隔符）。")
        return self


class TextImportRequest(Schema):
    text: str = Field(strict=True, min_length=1, max_length=20_000)
    title: str | None = Field(default=None, strict=True, max_length=250)
    request_id: UUID

    @field_validator("text", mode="before")
    @classmethod
    def normalize_text(cls, value):
        if isinstance(value, str):
            value = value.replace("\r\n", "\n").replace("\r", "\n")
            if any((ord(char) < 32 and char not in "\n\t") or 0xD800 <= ord(char) <= 0xDFFF for char in value):
                raise ValueError("文章包含无效字符，请粘贴纯文本内容。")
        return value

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value):
        if value is not None:
            if any(ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF for char in value):
                raise ValueError("标题不能包含换行或控制字符。")
            return value.strip() or None
        return value


class UrlImportRequest(Schema):
    model_config = ConfigDict(from_attributes=True, frozen=True, extra="forbid")
    url: str = Field(strict=True, min_length=1, max_length=2048)
    request_id: UUID

    @field_validator("url", mode="before")
    @classmethod
    def normalize_url(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value


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
