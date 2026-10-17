"""A small, auditable personal relation graph derived from explicit feedback."""
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from backend.models import LearningEvent, RecommendationFeedback, VocabularyRelation
from backend.schemas import RecommendationMetrics, Relation, RelationsOut
from backend.services.lemmatization import normalize_word


class RelationService:
    DELTAS = {"VIEW_RELATED": .05, "MARK_UNKNOWN": .08, "MARK_KNOWN": -.02,
              "RECOMMENDATION_USEFUL": .15, "RECOMMENDATION_NOT_USEFUL": -.20}

    def __init__(self, session: Session, user_id: int = 1):
        self.session, self.user_id = session, user_id

    def apply_event(self, event: LearningEvent) -> VocabularyRelation | None:
        """Apply a flushed event inside its caller's transaction; never commit."""
        details = event.details
        if (event.user_id != self.user_id or event.id is None or event.created_at is None
                or event.event_type not in self.DELTAS or not isinstance(details, dict)
                or details.get("source") != "recommendation_feedback"):
            return None
        if not all(isinstance(details.get(key), str) for key in ("source_lemma", "target_lemma", "relation")):
            return None
        try:
            source, target = normalize_word(details["source_lemma"]), normalize_word(details["target_lemma"])
        except ValueError:
            return None
        relation_type = details["relation"].strip().upper()
        if (source == target or max(len(source), len(target)) > 200
                or relation_type not in {relation.value.upper() for relation in Relation}):
            return None
        row = self.session.scalar(select(VocabularyRelation).where(
            VocabularyRelation.user_id == self.user_id,
            VocabularyRelation.source_word == source, VocabularyRelation.target_word == target,
            VocabularyRelation.relation_type == relation_type))
        if row is not None and event.id <= row.last_event_id:
            return row
        if row is None:
            row = VocabularyRelation(user_id=self.user_id, source_word=source, target_word=target,
                                     relation_type=relation_type, weight=.5, created_at=event.created_at,
                                     updated_at=event.created_at, last_event_id=event.id)
            self.session.add(row)
        row.weight = round(max(0.0, min(1.0, row.weight + self.DELTAS[event.event_type])), 6)
        row.updated_at, row.last_event_id = event.created_at, event.id
        return row

    def read(self) -> RelationsOut:
        with self.session.no_autoflush:
            return self._read()

    def _read(self) -> RelationsOut:
        scope = VocabularyRelation.user_id == self.user_id
        total = self.session.scalar(select(func.count()).select_from(VocabularyRelation).where(scope))
        items = list(self.session.scalars(select(VocabularyRelation).where(scope).order_by(
            VocabularyRelation.updated_at.desc(), VocabularyRelation.id.desc()).limit(200)))
        generated = self.session.scalar(select(func.coalesce(func.sum(
            func.json_array_length(LearningEvent.details, "$.items")), 0)).where(
            LearningEvent.user_id == self.user_id, LearningEvent.event_type == "RECOMMENDATION_GENERATED"))
        # Each row represents one offered word in one batch. Repeated clicks and
        # retries do not inflate metrics; useful/not-useful reflect latest choice.
        states = RecommendationFeedback
        counts = self.session.execute(select(*[
            func.coalesce(func.sum(case((condition, 1), else_=0)), 0)
            for condition in (states.shown.is_(True), states.viewed.is_(True),
                              states.added_unknown.is_(True), states.useful.is_(True), states.useful.is_(False))
        ]).where(states.user_id == self.user_id)).one()
        return RelationsOut(items=items, total=total, metrics=RecommendationMetrics(
            generated_items=generated, shown_items=counts[0], viewed_items=counts[1],
            added_unknown_items=counts[2], useful_items=counts[3], not_useful_items=counts[4]))
