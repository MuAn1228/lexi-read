"""Saved relations are actionable without fabricating explanations or learning."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.api.relation_workspace_routes import router
from backend.auth import token_digest
from backend.config import Settings
from backend.database import get_session
from backend.models import (
    Base, Document, GuestCredential, LearningEvent, User, Vocabulary, VocabularyRelation, WordOccurrence,
)
from backend.repositories.vocabulary import VocabularyRepository
from backend.schemas import FeedbackRequest
from backend.services.feedback import FeedbackService
from backend.services.relation_workspace import RelationWorkspaceService


NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)


@pytest.fixture
def session(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(RelationWorkspaceService, "_meaning", lambda _self, _word, _lemma: None)
    monkeypatch.setattr("backend.services.relation_workspace.utcnow", lambda: NOW)
    with Session(engine, expire_on_commit=False) as db:
        db.add_all([User(id=1), User(id=2), User(id=3)])
        db.commit()
        yield db
    engine.dispose()


@pytest.fixture
def client(session, monkeypatch):
    settings = Settings(app_mode="local", llm_provider="offline", _env_file=None)
    monkeypatch.setattr("backend.auth.get_settings", lambda: settings)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as connection:
        yield connection


def offered(word="alpha", relation="concept", **fields):
    return {"word": word, "meaning": "详细中文释义", "reason": "来自这对词的说明",
            "relation": relation, "priority": .8, "score": .8, **fields}


def batch(session, source="generalization", target="alpha", relation="CONCEPT", *,
          user_id=1, document_id=None, details=None, at=NOW):
    row = LearningEvent(user_id=user_id, word=source, lemma=source,
                        event_type="RECOMMENDATION_GENERATED", document_id=document_id,
                        details=details if details is not None else {
                            "source": "llm", "items": [offered(target, relation.lower())]}, created_at=at)
    session.add(row)
    session.flush()
    return row


def attach(session, issued, target="alpha", relation="CONCEPT", *,
           event_type="VIEW_RELATED", user_id=None, edge=None, details=None):
    uid = issued.user_id if user_id is None else user_id
    metadata = {"source": "recommendation_feedback", "source_lemma": issued.lemma,
                "target_lemma": target, "relation": relation.lower(), "batch_id": issued.id}
    row = LearningEvent(user_id=uid, word=target, lemma=target, event_type=event_type,
                        details=details if details is not None else metadata,
                        document_id=issued.document_id, created_at=NOW)
    session.add(row)
    session.flush()
    if edge is None:
        edge = VocabularyRelation(user_id=uid, source_word=issued.lemma, target_word=target,
                                  relation_type=relation, weight=.5, created_at=NOW,
                                  updated_at=NOW, last_event_id=row.id)
        session.add(edge)
    else:
        edge.last_event_id = row.id
    session.flush()
    return edge, row


def save(session, source="generalization", target="alpha", relation="CONCEPT", **kwargs):
    issued = batch(session, source, target, relation, **kwargs)
    edge, row = attach(session, issued, target, relation)
    return edge, issued, row


def vocabulary(session, word, status, user_id=1):
    row = Vocabulary(user_id=user_id, word=word, lemma=word, status=status,
                     created_at=NOW, updated_at=NOW)
    session.add(row)
    session.flush()
    return row


def document(session, source="generalization", *, user_id=1, deleted=False, valid=True):
    sentence = f"We study {source} today."
    row = Document(user_id=user_id, title="Owned article", image_path="", width=0, height=0,
                   ocr_text=sentence, source="text", created_at=NOW, deleted_at=NOW if deleted else None)
    session.add(row)
    session.flush()
    occurrence = WordOccurrence(document_id=row.id, word=source, lemma=source, sentence=sentence,
                                bbox=[0, 0, 1, 1], line=0, confidence=1, bbox_source="text",
                                text_start=sentence.index(source) if valid else 0,
                                text_end=sentence.index(source) + len(source))
    session.add(occurrence)
    session.flush()
    return row, occurrence


def read(session, **kwargs):
    return RelationWorkspaceService(session).read(**kwargs)


def test_empty_is_explicit_and_has_safe_pagination(session):
    result = read(session, page=999)
    assert result.summary.model_dump() == {
        "total_relations": 0, "source_words": 0, "unfamiliar_targets": 0, "practice_pairs": 0}
    assert result.items == result.sources == []
    assert result.pagination.model_dump() == {"page": 1, "page_size": 12, "total": 0, "total_pages": 1}


def test_saved_item_uses_immutable_explanation_current_status_and_utc(session):
    source = vocabulary(session, "generalization", "KNOWN")
    target = vocabulary(session, "alpha", "UNCERTAIN")
    edge, issued, _ = save(session)
    session.commit()
    item = read(session).items[0]
    assert (item.id, item.batch_id, item.provenance) == (edge.id, issued.id, "llm")
    assert item.meaning == "详细中文释义" and item.reason == "来自这对词的说明"
    assert item.source.vocabulary_id == source.id and item.source.status == "KNOWN"
    assert item.target.vocabulary_id == target.id and item.target.status == "UNCERTAIN"
    assert item.useful is None and item.updated_at.utcoffset() == timedelta(0)
    target.status = "KNOWN"
    session.commit()
    assert read(session).items[0].target.status == "KNOWN"


def test_all_rows_after_200_are_searchable_paginated_and_counted(session):
    alphabet = "abcdefghijklmnopqrstuvwxyz"
    targets = ["term " + alphabet[index // 26] + alphabet[index % 26] for index in range(241)]
    for target in targets:
        save(session, target=target)
    session.commit()
    first = read(session, page_size=50)
    last = read(session, page=999, page_size=50)
    assert first.summary.total_relations == first.summary.practice_pairs == 241
    assert first.pagination.total_pages == 5 and len(first.items) == 50
    assert last.pagination.page == 5 and len(last.items) == 41
    assert targets[0] in {item.target_word for item in last.items}
    assert read(session, q=targets[0]).pagination.total == 1
    ids = [item.id for page in range(1, 6) for item in read(session, page=page, page_size=50).items]
    assert len(ids) == len(set(ids)) == 241


def test_filter_before_pagination_and_summary_sources_are_independent(session):
    save(session, target="alpha", relation="CONCEPT")
    save(session, target="beta", relation="SYNONYM")
    save(session, source="other", target="gamma", relation="SYNONYM")
    vocabulary(session, "alpha", "UNKNOWN")
    vocabulary(session, "beta", "KNOWN")
    vocabulary(session, "gamma", "UNCERTAIN")
    session.commit()
    all_rows = read(session)
    result = read(session, relation="SYNONYM", scope="unfamiliar", q="  GAM  ", page=100, page_size=1)
    assert result.summary == all_rows.summary and result.sources == all_rows.sources
    assert result.summary.unfamiliar_targets == 2 and result.summary.source_words == 2
    assert result.pagination.total == result.pagination.page == 1
    assert result.items[0].target_word == "gamma" and result.filters.q == "GAM"
    assert read(session, q="GENERaliZation").pagination.total == 2


def test_unfamiliar_scope_does_not_imply_unassessed_or_unsaved_words_are_unknown(session):
    for target, status in [("alpha", "UNKNOWN"), ("beta", "UNCERTAIN"), ("gamma", "KNOWN"), ("delta", None)]:
        save(session, target=target)
        vocabulary(session, target, status)
    save(session, target="epsilon")
    session.commit()
    assert {item.target_word for item in read(session, scope="unfamiliar").items} == {"alpha", "beta"}
    assert read(session).summary.unfamiliar_targets == 2


def test_duplicate_targets_count_once_but_practice_pairs_count_relations(session):
    save(session, source="generalization", target="alpha")
    save(session, source="other", target="alpha")
    vocabulary(session, "alpha", "UNKNOWN")
    session.commit()
    assert read(session).summary.model_dump() == {
        "total_relations": 2, "source_words": 2, "unfamiliar_targets": 1, "practice_pairs": 2}


def test_latest_explicit_dismissal_survives_newer_view_and_returns_actionable_batch(session):
    first = batch(session)
    edge, _ = attach(session, first, event_type="RECOMMENDATION_USEFUL")
    newer = batch(session, details={"source": "offline", "items": [offered(meaning="较新释义")]})
    attach(session, newer, edge=edge, event_type="RECOMMENDATION_USEFUL")
    attach(session, first, edge=edge, event_type="RECOMMENDATION_NOT_USEFUL")
    attach(session, newer, edge=edge, event_type="VIEW_RELATED")
    session.commit()
    hidden = read(session)
    assert hidden.summary.total_relations == 0 and hidden.items == [] and hidden.sources == []
    dismissed = read(session, include_dismissed=True)
    assert dismissed.pagination.total == 1 and dismissed.summary.total_relations == 0
    item = dismissed.items[0]
    assert item.useful is False and item.batch_id == first.id
    assert item.meaning == "详细中文释义" and item.provenance == "llm"
    attach(session, first, edge=edge, event_type="RECOMMENDATION_USEFUL")
    session.commit()
    assert read(session).items[0].useful is True


def test_real_feedback_can_restore_cross_batch_dismissal_using_returned_batch(session):
    old = batch(session)
    new = batch(session)
    session.commit()
    service = FeedbackService(VocabularyRepository(session, 1))

    def apply(issued, action):
        return service.apply(issued.id, FeedbackRequest(word="alpha", action=action, request_id=uuid4()))

    apply(old, "USEFUL")
    apply(new, "USEFUL")
    apply(old, "NOT_USEFUL")
    apply(new, "VIEWED")
    item = read(session, include_dismissed=True).items[0]
    assert item.useful is False and item.batch_id == old.id
    session.rollback()  # Close GET transaction before existing BEGIN IMMEDIATE feedback service.
    service.apply(item.batch_id, FeedbackRequest(word=item.target_word, action="USEFUL", request_id=uuid4()))
    restored = read(session)
    assert restored.summary.total_relations == 1 and restored.items[0].useful is True


def test_generation_alone_does_not_supply_edge_explanation_or_utility(session):
    edge, issued, attached = save(session)
    unattached = batch(session, details={"source": "llm", "items": [offered(meaning="Never accepted")]})
    # An event above the edge's last applied ID is not backing that saved edge.
    ignored = LearningEvent(user_id=1, word="alpha", lemma="alpha", event_type="RECOMMENDATION_NOT_USEFUL",
                            details={"source": "recommendation_feedback", "source_lemma": "generalization",
                                     "target_lemma": "alpha", "relation": "concept", "batch_id": unattached.id},
                            created_at=NOW)
    session.add(ignored)
    session.commit()
    item = read(session).items[0]
    assert item.batch_id == issued.id and item.meaning != "Never accepted" and item.useful is None


@pytest.mark.parametrize("bad_details", [
    None, [], "legacy", {"items": None}, {"items": "not-list"}, {"items": [None]},
    {"items": [{"word": "alpha", "relation": "concept"}]},
    {"items": [offered("other")]}, {"items": [offered(relation="synonym")]},
    {"items": [offered(), offered(meaning="ambiguous")]},
    {"items": [offered(priority="invalid")]}, {"items": [offered(word="Alpha")]},
])
def test_malformed_or_nonmatching_snapshot_never_gets_fabricated_explanation(session, bad_details):
    edge, issued, _ = save(session)
    issued.details = bad_details
    session.commit()
    item = read(session).items[0]
    assert item.meaning is item.reason is item.batch_id is None and item.provenance == "unavailable"
    assert read(session).summary.practice_pairs == 0


@pytest.mark.parametrize("field,value", [
    ("source_lemma", "other"), ("target_lemma", "other"), ("relation", "synonym"),
    ("batch_id", True), ("batch_id", "1"), ("source", "legacy"),
])
def test_feedback_metadata_must_match_exact_pair_and_real_batch(session, field, value):
    edge, issued, attached = save(session)
    attached.details = {**attached.details, field: value}
    session.commit()
    assert read(session).items[0].batch_id is None


def test_source_and_target_meanings_are_bounded_to_visible_words_and_sources(session, monkeypatch):
    for target in ["alpha", "beta", "gamma"]:
        save(session, target=target)
    session.commit()
    calls = []
    monkeypatch.setattr(RelationWorkspaceService, "_meaning", lambda self, word, lemma: calls.append(lemma) or lemma)
    result = read(session, page_size=1)
    assert len(calls) == len(set(calls)) == 2
    assert set(calls) == {"generalization", "gamma"}
    assert result.items[0].source.meaning_summary == result.sources[0].meaning_summary == "generalization"


def test_unknown_provenance_does_not_invent_ai_label_or_fail_on_legacy_object(session):
    edge, issued, _ = save(session)
    issued.details = {**issued.details, "source": {"legacy": True}}
    session.commit()
    item = read(session).items[0]
    assert item.provenance == "unavailable" and item.meaning == "详细中文释义"


def test_verified_source_context_opens_actual_matching_occurrence(session):
    doc, occurrence = document(session)
    # The context must select the source word, not another first token/target.
    unrelated = WordOccurrence(document_id=doc.id, word="alpha", lemma="alpha", sentence=doc.ocr_text,
                                bbox=[0, 0, 1, 1], line=0, confidence=1, bbox_source="text")
    session.add(unrelated)
    edge, _, _ = save(session, document_id=doc.id)
    session.commit()
    context = read(session).items[0].context
    assert context.document_id == doc.id and context.occurrence_id == occurrence.id
    assert context.sentence == doc.ocr_text and context.document_title == doc.title


@pytest.mark.parametrize("case", ["foreign", "deleted", "invalid", "missing_source", "future"])
def test_false_context_is_never_attributed_and_invalid_document_disables_feedback(session, case):
    doc, occurrence = document(session, source="other" if case == "missing_source" else "generalization",
                               user_id=2 if case == "foreign" else 1, deleted=case == "deleted",
                               valid=case != "invalid")
    if case == "future":
        doc.created_at = NOW + timedelta(days=1)
    save(session, document_id=doc.id)
    session.commit()
    item = read(session).items[0]
    assert item.context is None and item.meaning == "详细中文释义"
    if case in {"foreign", "deleted", "future"}:
        assert item.batch_id is None


def test_every_edge_can_share_its_source_context_without_losing_pair(session):
    doc, occurrence = document(session)
    save(session, target="alpha", document_id=doc.id)
    save(session, target="beta", document_id=doc.id)
    session.commit()
    assert {item.context.occurrence_id for item in read(session).items} == {occurrence.id}


def test_other_users_cannot_supply_snapshot_vocabulary_or_feedback(session):
    edge, issued, attached = save(session)
    issued.user_id = 2
    vocabulary(session, "alpha", "UNKNOWN", user_id=2)
    save(session, source="private", target="secret", user_id=2)
    session.commit()
    result = read(session)
    assert result.summary.total_relations == 1 and result.summary.unfamiliar_targets == 0
    assert result.items[0].target.vocabulary_id is None and result.items[0].batch_id is None
    assert "secret" not in result.model_dump_json() and "private" not in result.model_dump_json()


def test_read_is_only_selects_and_does_not_flush_pending_learning_writes(session):
    save(session)
    session.commit()
    before = {table.name: list(session.execute(select(table)).mappings()) for table in Base.metadata.sorted_tables}
    pending = Vocabulary(user_id=1, word="pending", lemma="pending", status="UNKNOWN")
    session.add(pending)
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lstrip().split()[0].upper())

    event.listen(session.bind, "before_cursor_execute", capture)
    try:
        result = read(session)
    finally:
        event.remove(session.bind, "before_cursor_execute", capture)
    assert result.pagination.total == 1 and pending.id is None
    assert set(statements) == {"SELECT"}
    session.expunge(pending)
    after = {table.name: list(session.execute(select(table)).mappings()) for table in Base.metadata.sorted_tables}
    assert before == after


@pytest.mark.parametrize("query", [
    "page=0", "page_size=0", "page_size=51", "relation=synonym", "relation=wrong", "scope=known",
    "include_dismissed=wrong", "q=" + "a" * 101,
])
def test_route_rejects_invalid_filters(client, query):
    assert client.get("/api/relation-workspace?" + query).status_code == 422


def test_route_accepts_all_types_and_echoes_typed_filters(client, session):
    for relation in ["WORD_FAMILY", "SYNONYM", "ANTONYM", "CONCEPT", "COLLOCATION", "DOMAIN_RELATED"]:
        save(session, relation=relation)
    session.commit()
    for relation in ["WORD_FAMILY", "SYNONYM", "ANTONYM", "CONCEPT", "COLLOCATION", "DOMAIN_RELATED"]:
        response = client.get("/api/relation-workspace", params={"relation": relation, "page_size": 1})
        assert response.status_code == 200, response.text
        assert response.json()["items"][0]["relation_type"] == relation
        assert response.json()["summary"]["total_relations"] == 6


def test_public_api_auth_isolates_each_visitors_full_workspace(client, session, monkeypatch):
    save(session, source="localprivate", target="localword")
    save(session, source="second", target="alpha", user_id=2)
    save(session, source="third", target="beta", user_id=3)
    session.add_all([GuestCredential(token_hash=token_digest("a" * 43), user_id=2),
                     GuestCredential(token_hash=token_digest("b" * 43), user_id=3)])
    session.commit()
    settings = Settings(app_mode="public", llm_provider="offline", _env_file=None)
    monkeypatch.setattr("backend.auth.get_settings", lambda: settings)
    assert client.get("/api/relation-workspace").status_code == 401
    first = client.get("/api/relation-workspace", headers={"Authorization": "Bearer " + "a" * 43})
    assert first.status_code == 200, first.text
    assert first.json()["summary"]["total_relations"] == 1
    assert first.json()["items"][0]["source_word"] == "second"
    assert "localprivate" not in first.text and "third" not in first.text
    second = client.get("/api/relation-workspace", headers={"Authorization": "Bearer " + "b" * 43})
    assert second.json()["items"][0]["source_word"] == "third"
