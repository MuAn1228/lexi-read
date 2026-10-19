"""Personalized previews use real vocabulary and never assess or observe it."""
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Lock
from uuid import uuid4

from fastapi import HTTPException
import httpx
import pytest
from sqlalchemy import select

from backend.auth import token_digest
from backend.models import GuestCredential, ReviewSchedule, User, Vocabulary
from backend.public_limits import PublicUsage
from backend.services import practice_generation as practice
from backend.tests.test_article_library import snapshot
from backend.tests.test_text_import import payload, post, text_app  # noqa: F401


STORY = (
    "On a quiet morning, a reader visited a small library with great curiosity. "
    "She noticed a wooden model of a bridge beside the window and asked the librarian about its story. "
    "He explained that local children had built it together during the summer. "
    "Each child had offered a different idea, and they had tested every piece before adding it. "
    "The visitor smiled and borrowed a book about bridges. "
    "On her way home, she watched the river and thought about how small questions could lead to wonderful discoveries."
)


@pytest.fixture
def generation(text_app, monkeypatch):
    client, factory, settings = text_app
    monkeypatch.setattr(practice, "get_settings", lambda: settings)
    settings.llm_provider = "openai_compatible"
    settings.llm_api_key = "synthetic-test-key"
    settings.llm_model = "synthetic-model"
    document = post(client, payload("A model invites a reader to explore with curiosity.", "Source"))
    ids = []
    for lemma in ("model", "reader", "curiosity"):
        response = client.put(f"/api/vocabulary/{lemma}", json={"status": "UNKNOWN", "document_id": document["id"]})
        assert response.status_code == 200
        ids.append(response.json()["id"])
    calls = []

    def complete(_self, instruction, data, schema):
        calls.append(data)
        multiplier = {"short": 1, "medium": 2, "long": 3}[data["length"]]
        return schema.model_validate({"title": "A Small Discovery", "text": "\n\n".join([STORY] * multiplier)})

    monkeypatch.setattr(practice.LLMService, "_complete", complete)
    return client, factory, settings, ids, calls


def body(ids, **changes):
    return {"request_id": str(uuid4()), "topic": "A visit to the library", "difficulty": "B1",
            "length": "short", "vocabulary_ids": ids, **changes}


def run(factory, request, user=1):
    with factory() as session:
        return practice.generate_practice(session, practice.PracticeRequest.model_validate(request), user)


def test_targets_are_read_only_and_model_runs_only_after_generate(generation):
    client, factory, _, ids, calls = generation
    before = snapshot(factory)
    response = client.get("/api/practice/targets")
    assert response.status_code == 200
    assert response.json()["limit"] == 5
    assert set(row["id"] for row in response.json()["targets"]) == set(ids)
    assert all(row["reason"] in {"due", "unknown"} for row in response.json()["targets"])
    assert not calls and snapshot(factory) == before


def test_preview_has_verified_target_spans_and_does_not_write_learning_data(generation):
    client, factory, _, ids, calls = generation
    before = snapshot(factory)
    request = body(ids)
    response = client.post("/api/practice/generate", json=request)
    assert response.status_code == 200
    result = response.json()
    assert result["source"] == "llm" and result["title"] == "A Small Discovery"
    assert result["text"] == STORY and 80 <= result["word_count"] <= 120
    assert result["difficulty"] == "B1" and result["length"] == "short"
    assert result["topic"] == request["topic"] and result["request_id"] == request["request_id"]
    assert not result["cached"] and result["warning"] is None
    assert set(match["vocabulary_id"] for match in result["matches"]) == set(ids)
    for match in result["matches"]:
        assert STORY[match["text_start"]:match["text_end"]] == match["word"]
    assert len(calls) == 1
    assert set(calls[0]) == {"topic", "difficulty", "length", "word_count_min", "word_count_max", "target_words"}
    assert {target["lemma"] for target in calls[0]["target_words"]} == {"model", "reader", "curiosity"}
    assert snapshot(factory) == before


@pytest.mark.parametrize("length,minimum,maximum", [("short", 80, 120), ("medium", 150, 200), ("long", 250, 300)])
def test_requested_length_has_exact_server_word_count(generation, length, minimum, maximum):
    _, factory, _, ids, calls = generation
    result = run(factory, body(ids, length=length))
    assert result.source == "llm" and minimum <= result.word_count <= maximum
    assert calls[0]["word_count_min"] == minimum and calls[0]["word_count_max"] == maximum


def test_successful_uuid_receipt_survives_new_sessions_and_new_process_locks(generation, monkeypatch):
    _, factory, settings, ids, calls = generation
    before = snapshot(factory)
    request = body(ids)
    first = run(factory, request)
    monkeypatch.setattr(practice, "_receipt_locks", [Lock() for _ in range(64)])
    second = run(factory, request)
    assert second.model_dump(mode="json") == first.model_copy(update={"cached": True}).model_dump(mode="json")
    assert len(calls) == 1 and snapshot(factory) == before
    path, = (settings.storage_dir / "practice-generation-cache").glob("*.json")
    assert "synthetic-test-key" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("change", [{"topic": "Another subject"}, {"difficulty": "B2"}, {"length": "medium"},
                                  {"vocabulary_ids": "subset"}])
def test_same_uuid_cannot_be_reused_with_other_parameters(generation, change):
    _, factory, _, ids, calls = generation
    request = body(ids)
    run(factory, request)
    if change.get("vocabulary_ids") == "subset":
        change = {"vocabulary_ids": ids[:1]}
    with pytest.raises(HTTPException) as failure:
        run(factory, {**request, **change})
    assert failure.value.status_code == 409 and len(calls) == 1


def test_reordered_target_selection_reuses_same_uuid_result(generation):
    _, factory, _, ids, calls = generation
    request = body(ids)
    run(factory, request)
    assert run(factory, {**request, "vocabulary_ids": list(reversed(ids))}).cached
    assert len(calls) == 1


def test_new_uuid_intentionally_generates_again_and_never_saves_implicitly(generation):
    _, factory, _, ids, calls = generation
    before = snapshot(factory)
    run(factory, body(ids))
    run(factory, body(ids))
    assert len(calls) == 2 and snapshot(factory) == before


def test_cached_preview_is_reused_after_status_or_provider_changes(generation):
    _, factory, settings, ids, calls = generation
    request = body(ids)
    first = run(factory, request)
    with factory() as session:
        for identifier in ids:
            session.get(Vocabulary, identifier).status = "KNOWN"
        session.commit()
    settings.llm_provider, settings.llm_model, settings.llm_api_key = "offline", "different", ""
    before = snapshot(factory)
    second = run(factory, request)
    assert second.cached and second.text == first.text and len(calls) == 1
    assert snapshot(factory) == before


@pytest.mark.parametrize("mutation", ["word", "lemma", "owner", "created_at", "user_identity"])
def test_cached_preview_rechecks_ownership_and_original_vocabulary_identity(generation, mutation):
    _, factory, _, ids, calls = generation
    request = body(ids)
    run(factory, request)
    with factory() as session:
        entry = session.get(Vocabulary, ids[0])
        if mutation == "word":
            entry.word = "changed"
        elif mutation == "lemma":
            entry.lemma = "changed"
        elif mutation == "owner":
            entry.user_id = 2
        elif mutation == "created_at":
            entry.created_at = entry.created_at.replace(year=2020)
        else:
            session.get(User, 1).created_at = entry.created_at.replace(year=2020)
        session.commit()
    with pytest.raises(HTTPException) as failure:
        run(factory, request)
    assert failure.value.status_code in {404, 409} and len(calls) == 1


def test_service_does_not_flush_or_commit_caller_pending_work(generation):
    _, factory, _, ids, _ = generation
    before = snapshot(factory)
    with factory() as session:
        pending = User(id=12345)
        session.add(pending)
        result = practice.generate_practice(session, practice.PracticeRequest.model_validate(body(ids)), 1)
        assert result.source == "llm" and pending in session.new
        with factory() as other:
            assert other.get(User, 12345) is None
        session.rollback()
    assert snapshot(factory) == before


def test_preview_does_not_overwrite_unsaved_edits_to_target_or_user(generation):
    _, factory, _, ids, _ = generation
    before = snapshot(factory)
    with factory() as session:
        target = session.get(Vocabulary, ids[0])
        user = session.get(User, 1)
        target.word = "An unsaved title"
        target.status = "KNOWN"
        target.lookup_count += 123
        user.created_at = user.created_at.replace(year=2001)
        changed_date = user.created_at
        result = practice.generate_practice(session, practice.PracticeRequest.model_validate(body(ids)), 1)
        assert result.source == "llm"
        assert target.word == "An unsaved title" and target.status == "KNOWN"
        assert target.lookup_count == 123 and user.created_at == changed_date
        assert target in session.dirty and user in session.dirty
        session.rollback()
    assert snapshot(factory) == before


@pytest.mark.parametrize("bad", ["missing", "substring", "too_short", "too_long", "chinese", "markup",
                               "extra", "empty", "surrogate", "control", "title_newline", "title_long",
                               "body_long", "body_type"])
def test_invalid_model_result_is_never_shown_or_cached(generation, monkeypatch, bad):
    _, factory, settings, ids, calls = generation
    before = snapshot(factory)

    def invalid(_self, instruction, data, schema):
        calls.append(data)
        value = {"title": "A Small Discovery", "text": STORY}
        if bad == "missing":
            value["text"] = STORY.replace("curiosity", "interest")
        elif bad == "substring":
            value["text"] = STORY.replace("model", "remodeling")
        elif bad == "too_short":
            value["text"] = "A model gave a reader curiosity."
        elif bad == "too_long":
            value["text"] = " ".join([STORY] * 2)
        elif bad == "chinese":
            value["text"] = "中文故事" * 50 + STORY
        elif bad == "markup":
            value["text"] = "<script>" + STORY + "</script>"
        elif bad == "extra":
            value["private"] = "secret"
        elif bad == "empty":
            value["text"] = "  "
        elif bad == "surrogate":
            value["text"] = STORY + "\ud800"
        elif bad == "control":
            value["text"] = STORY + "\x00"
        elif bad == "title_newline":
            value["title"] = "Two\nLines"
        elif bad == "title_long":
            value["title"] = "t" * 121
        elif bad == "body_long":
            value["text"] = "t" * 10001
        else:
            value["text"] = 123
        return schema.model_validate(value)

    monkeypatch.setattr(practice.LLMService, "_complete", invalid)
    result = run(factory, body(ids))
    assert result.source == "unavailable" and result.text is None and result.title is None
    assert result.word_count is None and result.matches == [] and result.warning
    assert not list((settings.storage_dir / "practice-generation-cache").glob("*.json"))
    assert snapshot(factory) == before and len(calls) == 1


def test_failed_request_can_retry_without_fake_fallback_or_exception_leaks(generation, monkeypatch, caplog):
    _, factory, settings, ids, calls = generation
    before = snapshot(factory)
    request = body(ids)
    original = practice.LLMService._complete

    def timeout(*args, **kwargs):
        calls.append(1)
        raise httpx.ReadTimeout("synthetic-test-key https://private.invalid secret")

    monkeypatch.setattr(practice.LLMService, "_complete", timeout)
    assert run(factory, request).source == run(factory, request).source == "unavailable"
    assert len(calls) == 2 and "synthetic-test-key" not in caplog.text and "private.invalid" not in caplog.text
    assert not list((settings.storage_dir / "practice-generation-cache").glob("*.json"))
    monkeypatch.setattr(practice.LLMService, "_complete", original)
    assert run(factory, request).source == "llm" and run(factory, request).cached
    assert len(calls) == 3 and snapshot(factory) == before


@pytest.mark.parametrize("provider,key,model", [("offline", "key", "model"),
                                              ("openai_compatible", "", "model"),
                                              ("openai_compatible", "key", "")])
def test_unavailable_config_does_not_call_provider_or_fabricate_article(generation, provider, key, model):
    _, factory, settings, ids, calls = generation
    settings.llm_provider, settings.llm_api_key, settings.llm_model = provider, key, model
    before = snapshot(factory)
    result = run(factory, body(ids))
    assert result.source == "unavailable" and result.text is None and result.title is None
    assert result.matches == [] and result.warning and not calls and snapshot(factory) == before


def test_missing_foreign_and_unassessed_targets_fail_before_model(generation):
    _, factory, _, ids, calls = generation
    with factory() as session:
        unassessed = session.scalar(select(Vocabulary).where(Vocabulary.lemma == "invite"))
        unassessed_id = unassessed.id
    for selected, user, status in (([99999999], 1, 404), (ids, 2, 404), ([unassessed_id], 1, 409)):
        with pytest.raises(HTTPException) as failure:
            run(factory, body(selected), user=user)
        assert failure.value.status_code == status
    assert not calls


@pytest.mark.parametrize("mutation,failed,expected", [("owner", False, 404), ("word", False, 409),
                                                     ("lemma", False, 409), ("status", False, 409),
                                                     ("status", True, 409), ("word", True, 409)])
def test_changed_target_during_generation_blocks_result_and_cache(generation, monkeypatch, mutation, failed, expected):
    _, factory, settings, ids, calls = generation
    original = practice.LLMService._complete

    def delayed(*args, **kwargs):
        with factory() as session:
            entry = session.get(Vocabulary, ids[0])
            if mutation == "owner":
                entry.user_id = 2
            elif mutation == "status":
                entry.status = "KNOWN"
                schedule = session.get(ReviewSchedule, entry.id)
                if schedule:
                    session.delete(schedule)
            else:
                setattr(entry, mutation, "changed")
            session.commit()
        if failed:
            raise httpx.ReadTimeout("private failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(practice.LLMService, "_complete", delayed)
    with pytest.raises(HTTPException) as failure:
        run(factory, body(ids))
    assert failure.value.status_code == expected
    assert not list((settings.storage_dir / "practice-generation-cache").glob("*.json"))


def test_same_uuid_concurrent_requests_call_model_once(generation):
    _, factory, _, ids, calls = generation
    before = snapshot(factory)
    request = body(ids)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(factory, request), range(2)))
    assert len(calls) == 1 and sum(value.cached for value in results) == 1
    assert snapshot(factory) == before


def test_public_budget_is_consumed_on_attempt_only_not_uuid_retry(generation):
    _, factory, settings, ids, calls = generation
    settings.app_mode = "public"
    settings.public_llm_user_daily_limit = 1
    request = body(ids)
    before = snapshot(factory)
    assert run(factory, request).source == "llm"
    with factory() as session:
        usage = {row.bucket: row.count for row in session.scalars(select(PublicUsage))}
    assert len(usage) == 2 and set(usage.values()) == {1}
    assert run(factory, request).cached
    with factory() as session:
        assert {row.bucket: row.count for row in session.scalars(select(PublicUsage))} == usage
    with pytest.raises(HTTPException) as failure:
        run(factory, body(ids))
    assert failure.value.status_code == 429 and len(calls) == 1 and snapshot(factory) == before


def test_public_failed_generation_still_consumes_one_attempt(generation, monkeypatch):
    _, factory, settings, ids, calls = generation
    settings.app_mode = "public"
    settings.public_llm_user_daily_limit = 1
    request = body(ids)

    def timeout(*args, **kwargs):
        calls.append(1)
        raise httpx.ReadTimeout("synthetic failure")

    monkeypatch.setattr(practice.LLMService, "_complete", timeout)
    before = snapshot(factory)
    assert run(factory, request).source == "unavailable"
    with pytest.raises(HTTPException) as failure:
        run(factory, request)
    assert failure.value.status_code == 429 and len(calls) == 1
    assert snapshot(factory) == before


def test_public_http_authentication_and_uuid_isolation(generation):
    client, factory, settings, ids, calls = generation
    settings.app_mode = "public"
    with factory() as session:
        session.add(User(id=3))
        session.flush()
        for identifier in ids:
            session.get(Vocabulary, identifier).user_id = 3
            schedule = session.get(ReviewSchedule, identifier)
            if schedule:
                schedule.user_id = 3
        session.add_all([GuestCredential(token_hash=token_digest("a" * 43), user_id=3),
                        GuestCredential(token_hash=token_digest("b" * 43), user_id=2)])
        session.commit()
    own = {"Authorization": "Bearer " + "a" * 43}
    other = {"Authorization": "Bearer " + "b" * 43}
    request = body(ids)
    assert client.get("/api/practice/targets").status_code == 401
    assert client.post("/api/practice/generate", json=request).status_code == 401
    assert client.get("/api/practice/targets", headers=other).json()["targets"] == []
    assert client.post("/api/practice/generate", json=request, headers=other).status_code == 404
    assert client.post("/api/practice/generate", json=request, headers=own).json()["source"] == "llm"
    assert client.post("/api/practice/generate", json=request, headers=own).json()["cached"]
    assert client.post("/api/practice/generate", json=request, headers=other).status_code == 404
    assert len(calls) == 1


def test_corrupt_receipt_is_revalidated_and_io_failure_is_explicit(generation, monkeypatch):
    _, factory, settings, ids, calls = generation
    request = body(ids)
    run(factory, request)
    path, = (settings.storage_dir / "practice-generation-cache").glob("*.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    value["generation"]["text"] = STORY.replace("curiosity", "interest")
    path.write_text(json.dumps(value), encoding="utf-8")
    assert not run(factory, request).cached and len(calls) == 2
    assert run(factory, request).cached
    path.unlink()

    def fail(*args):
        raise OSError("private path")

    monkeypatch.setattr(practice, "_save", fail)
    result = run(factory, request)
    assert result.source == "llm" and result.warning and "回执" in result.warning
    assert "private path" not in result.warning and len(calls) == 3


def test_saving_preview_uses_existing_idempotent_text_import_once(generation):
    client, factory, _, ids, calls = generation
    before = snapshot(factory)
    result = run(factory, body(ids))
    assert snapshot(factory) == before
    save = payload(result.text, "AI练习 · " + result.title)
    first = post(client, save)
    after = snapshot(factory)
    assert first["source"] == "text" and first["ocr_text"] == result.text
    assert first["title"].startswith("AI练习")
    assert first["words"] and all(result.text[word["text_start"]:word["text_end"]] == word["word"] for word in first["words"])
    second = post(client, save)
    assert second["id"] == first["id"] and snapshot(factory) == after and len(calls) == 1


@pytest.mark.parametrize("change", [{"topic": ""}, {"topic": "  \n\t "}, {"topic": 1}, {"topic": True},
                                  {"topic": "x" * 101}, {"topic": "bad\ud800"}, {"topic": "bad\x00"},
                                  {"difficulty": "C2"}, {"difficulty": None}, {"length": 100},
                                  {"length": "huge"}, {"request_id": "invalid"}, {"request_id": None},
                                  {"vocabulary_ids": []}, {"vocabulary_ids": [1] * 6},
                                  {"vocabulary_ids": [1, 1]}, {"vocabulary_ids": [True]},
                                  {"vocabulary_ids": ["1"]}, {"vocabulary_ids": [0]},
                                  {"vocabulary_ids": [-1]}, {"vocabulary_ids": [2**63]},
                                  {"vocabulary_ids": None}, {"vocabulary_ids": "1"},
                                  {"user_id": 2}, {"target_words": ["invented"]},
                                  {"article_text": "invented"}])
def test_http_strict_request_rejects_untrusted_targets_and_bad_values(generation, change):
    client, factory, _, ids, calls = generation
    before = snapshot(factory)
    response = client.post("/api/practice/generate", content=json.dumps({**body(ids), **change}),
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert not calls and snapshot(factory) == before
