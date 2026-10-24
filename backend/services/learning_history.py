"""Turn recorded actions into a useful, scoped, read-only learning journal.

Only the selected local-calendar period is read. Passive recommendations,
imports and undone marks are not learning actions. Counts describe choices;
they never estimate time spent, accuracy, or durable mastery.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from math import ceil

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from backend.models import Document, LearningEvent, Vocabulary, WordOccurrence
from backend.models.entities import utcnow
from backend.schemas_history import (
    HistoryContext, HistoryDay, HistoryEntry, HistoryFilters, HistoryFocusWord,
    HistoryKind, HistoryPagination, HistoryRange, HistorySummary, LearningHistoryOut,
)
from backend.services.review import ReviewService
from backend.services.review_context import review_target_range
from backend.services.scheduling import utc


MARK_TYPES = ("MARK_KNOWN", "MARK_UNKNOWN", "MARK_UNCERTAIN")
RELATED_TYPES = (
    "VIEW_RELATED", "RECOMMENDATION_GENERATED",
    "RECOMMENDATION_USEFUL", "RECOMMENDATION_NOT_USEFUL",
)


@dataclass
class _Counts:
    actions: int = 0
    review: int = 0
    lookup: int = 0
    mark: int = 0
    related: int = 0
    unfamiliar: int = 0
    review_unfamiliar: int = 0
    words: set[str] = field(default_factory=set)
    reviewed_words: set[str] = field(default_factory=set)

    def add(self, lemma: str, kind: str, event_type: str):
        self.actions += 1
        setattr(self, kind, getattr(self, kind) + 1)
        if lemma:
            self.words.add(lemma)
            if kind == "review":
                self.reviewed_words.add(lemma)
        if event_type in {"MARK_UNKNOWN", "MARK_UNCERTAIN"}:
            self.unfamiliar += 1
            self.review_unfamiliar += int(kind == "review")


@dataclass
class _WordGroup:
    lemma: str
    word: str
    local_date: date
    last_at: datetime
    last_id: int
    last_event_type: str
    last_was_review: bool
    counts: _Counts = field(default_factory=_Counts)
    document_ids: list[int] = field(default_factory=list)

    def add(self, lemma: str, kind: str, event_type: str, document_id: int | None):
        self.counts.add(lemma, kind, event_type)
        # Rows arrive newest first, so a later matching learning document is
        # preferred, without ever treating an event's source as evidence.
        if document_id is not None and len(self.document_ids) < 5 and document_id not in self.document_ids:
            self.document_ids.append(document_id)


class LearningHistoryService:
    def __init__(self, session: Session, user_id: int = 1, *, now: datetime | None = None):
        self.session, self.user_id = session, user_id
        self.now = utc(now) if now is not None else None

    def read(self, *, days: int = 30, tz_offset_minutes: int = 480,
             day: date | None = None, kind: HistoryKind = "all", q: str = "",
             page: int = 1, page_size: int = 20) -> LearningHistoryOut:
        if (days not in {7, 30, 90} or not -720 <= tz_offset_minutes <= 840
                or kind not in {"all", "review", "lookup", "mark", "related"}
                or page < 1 or not 1 <= page_size <= 50 or len(q) > 100):
            raise HTTPException(422, "学习记录筛选条件无效。")
        # Reusing a session with pending edits must never turn this GET into a
        # learning write. The summary is independent of row filters/pagination.
        with self.session.no_autoflush:
            return self._read(days, tz_offset_minutes, day, kind, q.strip(), page, page_size)

    def _read(self, days, tz_offset_minutes, day, kind, q, page, page_size):
        now = self.now or utcnow()
        zone = timezone(timedelta(minutes=tz_offset_minutes))
        end_date = now.astimezone(zone).date()
        start_date = end_date - timedelta(days=days - 1)
        if day is not None and not start_date <= day <= end_date:
            raise HTTPException(422, "所选日期不在当前统计范围内。")
        start = datetime.combine(start_date, time.min, zone).astimezone(timezone.utc)
        marks = LearningEvent.event_type.in_(MARK_TYPES)
        related = LearningEvent.event_type.in_(RELATED_TYPES)
        # SQLite is the app's supported data store. json_type distinguishes an
        # explicit JSON true from legacy strings/numbers such as "true" or 1.
        lookup = and_(LearningEvent.event_type == "LOOKUP",
                      func.json_type(LearningEvent.details, "$.intentional") == "true")
        query = select(
            LearningEvent.id, LearningEvent.word, LearningEvent.lemma,
            LearningEvent.event_type, LearningEvent.document_id, LearningEvent.created_at,
            LearningEvent.details["origin"].as_string().label("origin"),
        ).where(
            LearningEvent.user_id == self.user_id,
            LearningEvent.created_at >= start, LearningEvent.created_at <= now,
            or_(marks, related, lookup),
        ).order_by(LearningEvent.created_at.desc(), LearningEvent.id.desc())

        totals = _Counts()
        calendar = {start_date + timedelta(days=index): _Counts() for index in range(days)}
        grouped: dict[tuple[date, str], _WordGroup] = {}
        period_words: dict[str, _WordGroup] = {}
        search = q.casefold()
        # Fetch narrow columns for one bounded period, not full historical
        # recommendation JSON or an arbitrary newest-100 event sample.
        for row in self.session.execute(query):
            lemma = (row.lemma or "").strip()
            word = (row.word or "").strip() or lemma
            at = utc(row.created_at)
            local_date = at.astimezone(zone).date()
            event_kind = ("review" if row.origin == "review" else "mark") if row.event_type in MARK_TYPES else (
                "lookup" if row.event_type == "LOOKUP" else "related")
            totals.add(lemma, event_kind, row.event_type)
            calendar[local_date].add(lemma, event_kind, row.event_type)
            if not lemma:
                continue
            if lemma not in period_words:
                period_words[lemma] = _WordGroup(
                    lemma, word, local_date, at, row.id, row.event_type, event_kind == "review")
            period_words[lemma].add(lemma, event_kind, row.event_type, row.document_id)
            if (day is not None and day != local_date or kind != "all" and kind != event_kind
                    or search and search not in word.casefold() and search not in lemma.casefold()):
                continue
            key = local_date, lemma
            if key not in grouped:
                grouped[key] = _WordGroup(
                    lemma, word, local_date, at, row.id, row.event_type, event_kind == "review")
            grouped[key].add(lemma, event_kind, row.event_type, row.document_id)

        ordered = sorted(grouped.values(), key=lambda item: (
            -item.last_at.timestamp(), -item.last_id, item.lemma))
        total = len(ordered)
        total_pages = max(1, ceil(total / page_size))
        page = min(page, total_pages)
        visible = ordered[(page - 1) * page_size:page * page_size]

        candidates = [item for item in period_words.values()
                      if item.counts.unfamiliar >= 2 or item.counts.lookup >= 2]
        # Scope the current vocabulary lookup too; an identical lemma in
        # another visitor's collection must never provide a status or action ID.
        wanted = {item.lemma for item in [*visible, *candidates]}
        vocabulary = {}
        if wanted:
            rows = self.session.execute(select(
                Vocabulary.id, Vocabulary.word, Vocabulary.lemma, Vocabulary.status,
            ).where(Vocabulary.user_id == self.user_id, Vocabulary.lemma.in_(wanted)))
            vocabulary = {entry.lemma: entry for entry in rows}
        focus = [item for item in candidates if item.lemma in vocabulary
                 and vocabulary[item.lemma].status in {"UNKNOWN", "UNCERTAIN"}]
        focus.sort(key=lambda item: (
            -item.counts.review_unfamiliar, -item.counts.unfamiliar, -item.counts.lookup,
            -item.last_at.timestamp(), item.lemma))
        focus = focus[:6]

        bounded = [*visible, *focus]
        contexts = self._contexts(bounded, now)
        meanings: dict[str, str | None] = {}

        def meaning(item):
            if item.lemma not in meanings:
                entry = vocabulary.get(item.lemma)
                meanings[item.lemma] = self._meaning(entry.word if entry else item.word, item.lemma)
            return meanings[item.lemma]

        def context(item):
            matches = contexts.get(item.lemma, {})
            for document_id in item.document_ids:
                if document_id in matches:
                    return matches[document_id]
            return next(iter(matches.values()), None)

        # This existing queue counts scheduled due words and legacy unfamiliar
        # words consistently; it is itself read-only and receives the same now.
        due_total = ReviewService(self.session, self.user_id, now=now).read(
            limit=1, tz_offset_minutes=tz_offset_minutes).total
        return LearningHistoryOut(
            as_of=now,
            range=HistoryRange(days=days, start_date=start_date, end_date=end_date),
            summary=HistorySummary(
                active_days=sum(item.actions > 0 for item in calendar.values()),
                studied_words=len(totals.words), reviewed_words=len(totals.reviewed_words),
                lookup_count=totals.lookup, mark_count=totals.mark,
                total_actions=totals.actions, due_total=due_total),
            days=[HistoryDay(
                date=local_date, words=len(counts.words), reviews=len(counts.reviewed_words),
                lookups=counts.lookup, marks=counts.mark, actions=counts.actions)
                for local_date, counts in calendar.items()],
            focus_words=[HistoryFocusWord(
                vocabulary_id=vocabulary[item.lemma].id, word=vocabulary[item.lemma].word,
                lemma=item.lemma, status=vocabulary[item.lemma].status, meaning_summary=meaning(item),
                unfamiliar_count=item.counts.unfamiliar, lookup_count=item.counts.lookup,
                review_unfamiliar_count=item.counts.review_unfamiliar, last_at=item.last_at,
                context=context(item)) for item in focus],
            entries=[HistoryEntry(
                key=f"{item.local_date.isoformat()}:{item.lemma}", date=item.local_date,
                word=item.word, lemma=item.lemma,
                vocabulary_id=vocabulary[item.lemma].id if item.lemma in vocabulary else None,
                status=vocabulary[item.lemma].status if item.lemma in vocabulary else None,
                meaning_summary=meaning(item), last_at=item.last_at, actions=item.counts.actions,
                review_count=item.counts.review, lookup_count=item.counts.lookup,
                mark_count=item.counts.mark, related_count=item.counts.related,
                last_event_type=item.last_event_type, last_was_review=item.last_was_review,
                context=context(item)) for item in visible],
            pagination=HistoryPagination(page=page, page_size=page_size, total=total, total_pages=total_pages),
            filters=HistoryFilters(day=day, kind=kind, q=q),
        )

    def _meaning(self, word: str, lemma: str) -> str | None:
        from backend.services.dictionary import cached_summary
        from backend.services.offline_dictionary import OfflineDictionaryUnavailable, summary
        try:
            result = summary(word, lemma)
            if result:
                return result
        except OfflineDictionaryUnavailable:
            pass
        return cached_summary(word, self.user_id)

    def _contexts(self, groups: list[_WordGroup], now: datetime) -> dict[str, dict[int, HistoryContext]]:
        if not groups:
            return {}
        lemmas = {item.lemma for item in groups}
        preferred = {document_id for item in groups for document_id in item.document_ids}
        ranking = select(
            WordOccurrence.id.label("id"), WordOccurrence.lemma.label("lemma"),
            WordOccurrence.document_id.label("document_id"),
            func.row_number().over(partition_by=WordOccurrence.lemma, order_by=(
                Document.created_at.desc(), Document.id.desc(), WordOccurrence.id.desc(),
            )).label("position"),
            func.row_number().over(
                partition_by=(WordOccurrence.lemma, WordOccurrence.document_id),
                order_by=WordOccurrence.id.desc(),
            ).label("document_position"),
        ).join(Document, Document.id == WordOccurrence.document_id).where(
            Document.user_id == self.user_id, Document.deleted_at.is_(None),
            Document.created_at <= now, WordOccurrence.lemma.in_(lemmas),
        ).subquery()
        # Up to five recent candidates per visible word, plus its actual event
        # documents. A same-lemma occurrence is required even for preferred docs.
        query = select(WordOccurrence, Document).join(
            Document, Document.id == WordOccurrence.document_id,
        ).join(ranking, ranking.c.id == WordOccurrence.id).where(
            or_(ranking.c.position <= 5, and_(ranking.c.document_id.in_(preferred),
                                             ranking.c.document_position <= 3)),
        ).order_by(Document.created_at.desc(), Document.id.desc(), WordOccurrence.id.desc())
        contexts: dict[str, dict[int, HistoryContext]] = {}
        for occurrence, document in self.session.execute(query):
            by_document = contexts.setdefault(occurrence.lemma, {})
            if document.id in by_document:
                continue
            # Corrected text offsets and original OCR token rules are checked
            # by the same helper used for real sentence fill-in review cards.
            if review_target_range(document, occurrence) is None:
                continue
            by_document[document.id] = HistoryContext(
                document_id=document.id, document_title=document.title,
                occurrence_id=occurrence.id, sentence=occurrence.sentence)
        return contexts
