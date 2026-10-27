"""Durable, user-scoped learning state for individual dictionary senses."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.entities import Base, utcnow


class VocabularySense(Base):
    __tablename__ = "vocabulary_senses"
    __table_args__ = (
        UniqueConstraint("user_id", "vocabulary_id", "sense_key"),
        CheckConstraint("status IN ('KNOWN','UNKNOWN','UNCERTAIN')"),
        CheckConstraint("mastery_score >= 0 AND mastery_score <= 1"),
        CheckConstraint("known_streak >= 0 AND known_streak <= 5"),
        CheckConstraint("source_ordinal >= 0"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    vocabulary_id: Mapped[int] = mapped_column(ForeignKey("vocabulary.id"), index=True)
    sense_key: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(30))
    source_revision: Mapped[str] = mapped_column(String(64))
    headword: Mapped[str] = mapped_column(String(200))
    source_ordinal: Mapped[int] = mapped_column(Integer)
    part_of_speech: Mapped[str | None] = mapped_column(String(80), nullable=True)
    meaning: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))
    mastery_score: Mapped[float] = mapped_column(Float)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    known_streak: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class SenseReviewReceipt(Base):
    __tablename__ = "sense_review_receipts"
    __table_args__ = (
        UniqueConstraint("user_id", "request_id"),
        CheckConstraint("status IN ('KNOWN','UNKNOWN','UNCERTAIN')"),
        CheckConstraint("response_ms IS NULL OR (response_ms >= 500 AND response_ms <= 600000)"),
        CheckConstraint("plan_minutes IS NULL OR plan_minutes IN (10,20,30)"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(36))
    sense_id: Mapped[int] = mapped_column(ForeignKey("vocabulary_senses.id"), index=True)
    status: Mapped[str] = mapped_column(String(20))
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), nullable=True)
    response_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plan_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
