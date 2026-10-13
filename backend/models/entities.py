from datetime import datetime, timezone

from sqlalchemy import JSON, CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
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
    document: Mapped[Document] = relationship(back_populates="words")


class LearningEvent(Base):
    __tablename__ = "learning_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), default=1, index=True)
    word: Mapped[str] = mapped_column(String(200))
    event_type: Mapped[str] = mapped_column(String(30))
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
