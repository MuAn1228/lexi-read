"""Personal word collections, independent of marks and review scheduling."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.entities import Base, utcnow


class VocabularyGroup(Base):
    __tablename__ = "vocabulary_groups"
    __table_args__ = (UniqueConstraint("user_id", "name_key"), {"sqlite_autoincrement": True})

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(30))
    name_key: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VocabularyGroupMember(Base):
    __tablename__ = "vocabulary_group_members"

    group_id: Mapped[int] = mapped_column(
        ForeignKey("vocabulary_groups.id", ondelete="CASCADE"), primary_key=True)
    vocabulary_id: Mapped[int] = mapped_column(
        ForeignKey("vocabulary.id", ondelete="CASCADE"), primary_key=True, index=True)
