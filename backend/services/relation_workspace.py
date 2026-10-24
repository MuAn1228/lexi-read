"""Read-only saved associations with verified recommendation explanations.

Graph weights describe a feedback rule, not knowledge or correctness. This view
uses current vocabulary statuses and immutable offered items instead. A batch
never supplies an explanation without an actual same-user, same-pair feedback
event that belongs to the saved edge.
"""
from collections import Counter
from dataclasses import dataclass
from math import ceil

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from pydantic import ValidationError

from backend.models import Document, LearningEvent, Vocabulary, VocabularyRelation, WordOccurrence
from backend.models.entities import utcnow
from backend.schemas import Recommendation
from backend.schemas_relations import (
    RelationContext, RelationFilter, RelationPagination, RelationScope, RelationSource,
    RelationWord, RelationWorkspaceFilters, RelationWorkspaceItem, RelationWorkspaceOut,
    RelationWorkspaceSummary,
)
from backend.services.lemmatization import normalize_word
from backend.services.review_context import review_target_range
from backend.services.scheduling import utc


RELATIONS = {"WORD_FAMILY", "SYNONYM", "ANTONYM", "CONCEPT", "COLLOCATION", "DOMAIN_RELATED"}
FEEDBACK_EVENTS = {
    "VIEW_RELATED", "MARK_UNKNOWN", "MARK_KNOWN",
    "RECOMMENDATION_USEFUL", "RECOMMENDATION_NOT_USEFUL",
}
UTILITY_EVENTS = {"RECOMMENDATION_USEFUL", "RECOMMENDATION_NOT_USEFUL"}


def _normalized(value):
    if not isinstance(value, str):
        return None
    try:
        return normalize_word(value)
    except ValueError:
        return None


def _text(value):
    return value.strip() or None if isinstance(value, str) else None


def _chunks(values):
    values = list(values)
    for index in range(0, len(values), 500):
        yield values[index:index + 500]


@dataclass
class _Snapshot:
    batch_id: int
    meaning: str | None
    reason: str | None
    provenance: str
    document_id: int | None
    actionable: bool


class RelationWorkspaceService:
    def __init__(self, session: Session, user_id: int = 1):
        self.session, self.user_id = session, user_id

    def read(self, *, q: str = "", relation: RelationFilter = "all", scope: RelationScope = "all",
             include_dismissed: bool = False, page: int = 1, page_size: int = 12) -> RelationWorkspaceOut:
        if (len(q) > 100 or relation not in RELATIONS | {"all"}
                or scope not in {"all", "unfamiliar"} or type(include_dismissed) is not bool
                or page < 1 or not 1 <= page_size <= 50):
            raise HTTPException(422, "词汇关联筛选条件无效。")
        # GET must not flush pending vocabulary edits from a shared session.
        with self.session.no_autoflush:
            return self._read(q.strip(), relation, scope, include_dismissed, page, page_size)

    def _read(self, q, relation, scope, include_dismissed, page, page_size):
        now = utcnow()
        edges = list(self.session.scalars(select(VocabularyRelation).where(
            VocabularyRelation.user_id == self.user_id,
        ).order_by(VocabularyRelation.updated_at.desc(), VocabularyRelation.id.desc())))
        snapshots, utility = self._snapshots(edges, now)
        words = {word for edge in edges for word in (edge.source_word, edge.target_word)}
        vocabulary = {}
        for chunk in _chunks(words):
            for entry in self.session.execute(select(
                Vocabulary.id, Vocabulary.word, Vocabulary.lemma, Vocabulary.status,
            ).where(Vocabulary.user_id == self.user_id, Vocabulary.lemma.in_(chunk))):
                vocabulary[entry.lemma] = entry

        active = [edge for edge in edges if utility.get(edge.id) is not False]
        source_counts = Counter(edge.source_word for edge in active)
        # Stable ties follow the most recently changed association, then word.
        source_order = {edge.source_word: index for index, edge in reversed(list(enumerate(active)))}
        sources = sorted(source_counts, key=lambda word: (-source_counts[word], source_order[word], word))[:8]
        search = q.casefold()
        filtered = [edge for edge in edges if (
            (include_dismissed or utility.get(edge.id) is not False)
            and (relation == "all" or edge.relation_type == relation)
            and (scope == "all" or edge.target_word in vocabulary
                 and vocabulary[edge.target_word].status in {"UNKNOWN", "UNCERTAIN"})
            and (not search or search in edge.source_word.casefold() or search in edge.target_word.casefold())
        )]
        total = len(filtered)
        total_pages = max(1, ceil(total / page_size))
        page = min(page, total_pages)
        visible = filtered[(page - 1) * page_size:page * page_size]
        contexts = self._contexts(visible, snapshots, now)
        meanings = {}

        def word_info(word):
            entry = vocabulary.get(word)
            if word not in meanings:
                meanings[word] = self._meaning(entry.word if entry else word, word)
            return RelationWord(vocabulary_id=entry.id if entry else None,
                                status=entry.status if entry else None, meaning_summary=meanings[word])

        def item(edge):
            snapshot = snapshots.get(edge.id)
            return RelationWorkspaceItem(
                id=edge.id, source_word=edge.source_word, target_word=edge.target_word,
                relation_type=edge.relation_type, source=word_info(edge.source_word),
                target=word_info(edge.target_word), meaning=snapshot.meaning if snapshot else None,
                reason=snapshot.reason if snapshot else None,
                provenance=snapshot.provenance if snapshot else "unavailable",
                batch_id=snapshot.batch_id if snapshot and snapshot.actionable else None, useful=utility.get(edge.id),
                context=contexts.get(edge.id), updated_at=utc(edge.updated_at))

        return RelationWorkspaceOut(
            summary=RelationWorkspaceSummary(
                total_relations=len(active), source_words=len(source_counts),
                unfamiliar_targets=len({edge.target_word for edge in active if edge.target_word in vocabulary
                                        and vocabulary[edge.target_word].status in {"UNKNOWN", "UNCERTAIN"}}),
                practice_pairs=sum(bool(snapshots.get(edge.id) and snapshots[edge.id].meaning) for edge in active)),
            sources=[RelationSource(word=word, meaning_summary=word_info(word).meaning_summary,
                                    status=word_info(word).status, relation_count=source_counts[word])
                     for word in sources],
            items=[item(edge) for edge in visible],
            pagination=RelationPagination(page=page, page_size=page_size, total=total, total_pages=total_pages),
            filters=RelationWorkspaceFilters(q=q, relation=relation, scope=scope,
                                             include_dismissed=include_dismissed),
        )

    def _snapshots(self, edges, now):
        if not edges:
            return {}, {}
        by_pair = {(edge.source_word, edge.target_word, edge.relation_type): edge for edge in edges}
        attached = []
        batch_ids = set()
        # Only intentional relation feedback is relevant. Import/lookup/reading
        # history is never loaded, and there is no arbitrary 100/200-row cap.
        events = self.session.scalars(select(LearningEvent).where(
            LearningEvent.user_id == self.user_id,
            LearningEvent.event_type.in_(FEEDBACK_EVENTS),
            LearningEvent.created_at <= now,
            LearningEvent.details["source"].as_string() == "recommendation_feedback",
        ).order_by(LearningEvent.id.desc()))
        for event in events:
            details = event.details
            if not isinstance(details, dict):
                continue
            source, target = _normalized(details.get("source_lemma")), _normalized(details.get("target_lemma"))
            relation = details.get("relation")
            if not isinstance(relation, str):
                continue
            edge = by_pair.get((source, target, relation.strip().upper()))
            batch_id = details.get("batch_id")
            if (edge is None or event.id > edge.last_event_id or type(batch_id) is not int or batch_id < 1
                    or _normalized(event.lemma) != target or _normalized(event.word) != target):
                continue
            attached.append((edge, event, batch_id))
            batch_ids.add(batch_id)
        batches = {}
        for chunk in _chunks(batch_ids):
            for batch in self.session.scalars(select(LearningEvent).where(
                LearningEvent.user_id == self.user_id, LearningEvent.id.in_(chunk),
                LearningEvent.event_type == "RECOMMENDATION_GENERATED", LearningEvent.created_at <= now,
            )):
                batches[batch.id] = batch
        snapshots, utility = {}, {}
        usable_documents = set()
        for chunk in _chunks({batch.document_id for batch in batches.values() if batch.document_id is not None}):
            usable_documents.update(self.session.scalars(select(Document.id).where(
                Document.id.in_(chunk), Document.user_id == self.user_id,
                Document.deleted_at.is_(None), Document.created_at <= now,
            )))
        for edge, event, batch_id in attached:
            batch = batches.get(batch_id)
            if (batch is None or batch.id >= event.id or not isinstance(batch.details, dict)
                    or _normalized(batch.lemma) != edge.source_word
                    or _normalized(batch.word) != edge.source_word):
                continue
            offered = batch.details.get("items")
            if not isinstance(offered, list) or not all(isinstance(candidate, dict) for candidate in offered):
                continue
            matches = [candidate for candidate in offered if isinstance(candidate, dict)
                       and _normalized(candidate.get("word")) == edge.target_word
                       and isinstance(candidate.get("relation"), str)
                       and candidate["relation"].strip().upper() == edge.relation_type]
            # Ambiguous legacy duplicate items cannot be attributed safely.
            if len(matches) != 1:
                continue
            candidate = matches[0]
            # The feedback endpoint consumes the same immutable fields. Do not
            # offer an action for a legacy snapshot it cannot safely accept.
            try:
                validated = Recommendation.model_validate(candidate)
            except ValidationError:
                continue
            if validated.word != edge.target_word:
                continue
            provenance = batch.details.get("source")
            if not isinstance(provenance, str) or provenance not in {"llm", "offline"}:
                provenance = "unavailable"
            snapshot = _Snapshot(batch.id, _text(validated.meaning), _text(validated.reason),
                                 provenance, batch.document_id,
                                 batch.document_id is None or batch.document_id in usable_documents)
            # Prefer the batch of the latest explicit utility choice so that
            # changing that choice updates the same receipt-backed state. An
            # unrelated batch's already-true state would make restoring a pair
            # a no-op. Without utility, use the newest attached feedback event.
            if edge.id not in snapshots:
                snapshots[edge.id] = snapshot
            # Later VIEWED/status feedback must not undo an explicit dismissal.
            # An older batch's newest explicit utility choice still wins.
            if edge.id not in utility and event.event_type in UTILITY_EVENTS:
                utility[edge.id] = event.event_type == "RECOMMENDATION_USEFUL"
                snapshots[edge.id] = snapshot
        return snapshots, utility

    def _meaning(self, word, lemma):
        from backend.services.dictionary import cached_summary
        from backend.services.offline_dictionary import OfflineDictionaryUnavailable, summary
        try:
            result = summary(word, lemma)
            if result:
                return result
        except OfflineDictionaryUnavailable:
            pass
        return cached_summary(word, self.user_id)

    def _contexts(self, edges, snapshots, now):
        desired = {(snapshot.document_id, edge.source_word): edge.id for edge in edges
                   if (snapshot := snapshots.get(edge.id)) and snapshot.document_id is not None}
        if not desired:
            return {}
        by_key = {}
        for chunk in _chunks({key[0] for key in desired}):
            rows = self.session.execute(select(WordOccurrence, Document).join(
                Document, Document.id == WordOccurrence.document_id,
            ).where(Document.id.in_(chunk), Document.user_id == self.user_id,
                    Document.deleted_at.is_(None), Document.created_at <= now,
                    WordOccurrence.lemma.in_({edge.source_word for edge in edges}))
                .order_by(WordOccurrence.id))
            for occurrence, document in rows:
                key = (document.id, occurrence.lemma)
                if key not in desired or key in by_key:
                    continue
                try:
                    valid = review_target_range(document, occurrence) is not None
                except (AttributeError, TypeError, ValueError):
                    valid = False
                if valid:
                    by_key[key] = RelationContext(document_id=document.id, document_title=document.title,
                                                  occurrence_id=occurrence.id, sentence=occurrence.sentence)
        # Several saved relations can share the same verified source occurrence.
        return {edge.id: by_key[(snapshot.document_id, edge.source_word)] for edge in edges
                if (snapshot := snapshots.get(edge.id))
                and (snapshot.document_id, edge.source_word) in by_key}
