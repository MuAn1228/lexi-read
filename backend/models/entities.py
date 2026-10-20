from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GuestCredential(Base):
    """Only a hash is retained; the bearer credential is returned once to its browser."""
    __tablename__ = "guest_credentials"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Vocabulary(Base):
    __tablename__ = "vocabulary"
    __table_args__ = (
        UniqueConstraint("user_id", "lemma"),
        CheckConstraint("status IS NULL OR status IN ('KNOWN','UNKNOWN','UNCERTAIN')"),
        CheckConstraint("mastery_score IS NULL OR (mastery_score >= 0 AND mastery_score <= 1)"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), default=1, index=True)
    word: Mapped[str] = mapped_column(String(200))
    lemma: Mapped[str] = mapped_column(String(200), index=True)
    # NULL means observed but never assessed; it must not imply uncertainty.
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    mastery_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    seen_count: Mapped[int] = mapped_column(Integer, default=0)
    lookup_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), default=1)
    title: Mapped[str] = mapped_column(String(250))
    image_path: Mapped[str] = mapped_column(Text)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    ocr_text: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(30), default="upload")
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    pages: Mapped[list[dict]] = mapped_column(JSON, default=list, server_default="[]")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    bookmark_occurrence_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    words: Mapped[list["WordOccurrence"]] = relationship(back_populates="document", cascade="all, delete-orphan", order_by="WordOccurrence.id")


class WordOccurrence(Base):
    __tablename__ = "word_occurrences"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    word: Mapped[str] = mapped_column(String(200))
    lemma: Mapped[str] = mapped_column(String(200), index=True)
    sentence: Mapped[str] = mapped_column(Text)
    bbox: Mapped[list[float]] = mapped_column(JSON)
    line: Mapped[int] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(Float)
    bbox_source: Mapped[str] = mapped_column(String(40))
    # Unicode code point offsets for text/PDF imports and corrected image text.
    text_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    document: Mapped[Document] = relationship(back_populates="words")


class LearningEvent(Base):
    __tablename__ = "learning_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), default=1, index=True)
    word: Mapped[str] = mapped_column(String(200))
    lemma: Mapped[str] = mapped_column(String(200), index=True)
    event_type: Mapped[str] = mapped_column(String(30))
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RecommendationFeedback(Base):
    __tablename__ = "recommendation_feedback"
    __table_args__ = (UniqueConstraint("user_id", "batch_id", "word"),
                     CheckConstraint("status IS NULL OR status IN ('KNOWN','UNKNOWN')"))
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("learning_events.id"), index=True)
    word: Mapped[str] = mapped_column(String(200))
    shown: Mapped[bool] = mapped_column(Boolean, default=False)
    viewed: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    useful: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    added_unknown: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class FeedbackReceipt(Base):
    __tablename__ = "feedback_receipts"
    __table_args__ = (UniqueConstraint("user_id", "request_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(36))
    batch_id: Mapped[int] = mapped_column(ForeignKey("learning_events.id"))
    word: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReviewReceipt(Base):
    __tablename__ = "review_receipts"
    __table_args__ = (
        UniqueConstraint("user_id", "request_id"),
        CheckConstraint("status IN ('KNOWN','UNKNOWN','UNCERTAIN')"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(36))
    lemma: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20))
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TextImportReceipt(Base):
    __tablename__ = "text_import_receipts"
    __table_args__ = (UniqueConstraint("user_id", "request_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(36))
    payload_hash: Mapped[str] = mapped_column(String(64))
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RestoreReceipt(Base):
    __tablename__ = "restore_receipts"
    token: Mapped[str] = mapped_column(String(32), primary_key=True)
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReviewSchedule(Base):
    __tablename__ = "review_schedules"
    __table_args__ = (CheckConstraint("known_streak >= 0 AND known_streak <= 5"),)
    vocabulary_id: Mapped[int] = mapped_column(ForeignKey("vocabulary.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    known_streak: Mapped[int] = mapped_column(Integer, default=0)


class VocabularyRelation(Base):
    __tablename__ = "vocabulary_relations"
    __table_args__ = (
        UniqueConstraint("user_id", "source_word", "target_word", "relation_type"),
        CheckConstraint("weight >= 0 AND weight <= 1"),
        CheckConstraint("relation_type IN ('WORD_FAMILY','SYNONYM','ANTONYM','CONCEPT','COLLOCATION','DOMAIN_RELATED')"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source_word: Mapped[str] = mapped_column(String(200))
    target_word: Mapped[str] = mapped_column(String(200))
    relation_type: Mapped[str] = mapped_column(String(30))
    weight: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_event_id: Mapped[int] = mapped_column(ForeignKey("learning_events.id"))
