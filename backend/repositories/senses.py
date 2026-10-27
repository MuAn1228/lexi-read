"""User-scoped persistence helpers for dictionary-sense learning."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import SenseReviewReceipt, VocabularySense


class SenseRepository:
    def __init__(self, session: Session, user_id: int = 1):
        self.session, self.user_id = session, user_id

    def for_vocabulary(self, vocabulary_id: int) -> list[VocabularySense]:
        return list(self.session.scalars(select(VocabularySense).where(
            VocabularySense.user_id == self.user_id,
            VocabularySense.vocabulary_id == vocabulary_id,
        ).order_by(VocabularySense.source_ordinal, VocabularySense.id)))

    def by_key(self, vocabulary_id: int, sense_key: str) -> VocabularySense | None:
        return self.session.scalar(select(VocabularySense).where(
            VocabularySense.user_id == self.user_id,
            VocabularySense.vocabulary_id == vocabulary_id,
            VocabularySense.sense_key == sense_key,
        ).execution_options(populate_existing=True))

    def by_id(self, sense_id: int) -> VocabularySense | None:
        return self.session.scalar(select(VocabularySense).where(
            VocabularySense.user_id == self.user_id,
            VocabularySense.id == sense_id,
        ).execution_options(populate_existing=True))

    def receipt(self, request_id: str) -> SenseReviewReceipt | None:
        return self.session.scalar(select(SenseReviewReceipt).where(
            SenseReviewReceipt.user_id == self.user_id,
            SenseReviewReceipt.request_id == request_id,
        ))
