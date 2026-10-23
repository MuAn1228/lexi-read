"""Durable, user-scoped receipts for reversible vocabulary bulk edits."""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.entities import Base, utcnow


class VocabularyOperation(Base):
    __tablename__ = "vocabulary_operations"
    __table_args__ = (UniqueConstraint("user_id", "request_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(36))
    payload_hash: Mapped[str] = mapped_column(String(64))
    snapshots: Mapped[list[dict]] = mapped_column(JSON)
    undone: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
