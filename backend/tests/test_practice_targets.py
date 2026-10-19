from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
import pytest

from backend.models import ReviewSchedule, User, Vocabulary
from backend.ocr.service import ENGLISH_WORD
from backend.services.practice_targets import (PracticeTarget, select_practice_targets,
                                               validate_practice_text)
from backend.tests.test_article_library import snapshot
from backend.tests.test_text_import import text_app  # noqa: F401

NOW = datetime(2026, 10, 19, 8, tzinfo=timezone.utc)


def target(lemma="model", index=1):
    return PracticeTarget(id=index, word=lemma, lemma=lemma, status="UNKNOWN",
                          reason="unknown", mastery_score=None, due_at=None)


def passage(prefix="Models learn.", count=90):
    filler = "A quiet reader enjoys a simple story in the local library after lunch every day".split() * 30
    return prefix + " " + " ".join(filler[:count - len(list(ENGLISH_WORD.finditer(prefix)))])


def seed(factory):
    with factory() as session:
        entries = {}
        for word, status, score, owner, due in (
            ("explore", "UNKNOWN", .2, 1, -2), ("remember", "KNOWN", .9, 1, -1),
            ("journey", "UNKNOWN", .2, 1, 1), ("garden", "UNKNOWN", .6, 1, 1),
            ("patient", "UNCERTAIN", .3, 1, 1), ("familiar", "KNOWN", .95, 1, 1),
            ("observe", None, None, 1, -5), ("secret", "UNKNOWN", .1, 2, -5),
        ):
            entry = Vocabulary(word=word, lemma=word, status=status, mastery_score=score, user_id=owner)
            session.add(entry)
            session.flush()
            session.add(ReviewSchedule(vocabulary_id=entry.id, user_id=owner, due_at=NOW + timedelta(days=due)))
            entries[word] = entry.id
        session.commit()
    return entries


def test_selection_uses_real_due_words_then_weak_unknown_and_uncertain_read_only(text_app):
    _, factory, _ = text_app
    ids = seed(factory)
    before = snapshot(factory)
    with factory() as session:
        selected = select_practice_targets(session, 1, now=NOW)
    assert [item.lemma for item in selected] == ["explore", "remember", "journey", "garden", "patient"]
    assert [item.reason for item in selected] == ["due", "due", "unknown", "unknown", "uncertain"]
    assert selected[1].status == "KNOWN" and selected[0].id == ids["explore"]
    assert snapshot(factory) == before


def test_owned_explicit_targets_reject_foreign_and_now_ineligible(text_app):
    _, factory, _ = text_app
    ids = seed(factory)
    with factory() as session:
        for requested, expected in (([ids["secret"]], 404), ([999999], 404),
                                    ([ids["familiar"]], 409), ([ids["observe"]], 409)):
            with pytest.raises(HTTPException) as failure:
                select_practice_targets(session, 1, vocabulary_ids=requested, now=NOW)
            assert failure.value.status_code == expected
        assert select_practice_targets(session, 1, vocabulary_ids=[ids["patient"]], now=NOW)[0].lemma == "patient"


def test_empty_selection_never_fabricates_vocabulary_or_flushes_pending_changes(text_app):
    _, factory, _ = text_app
    with factory() as session:
        pending = User(id=1234)
        session.add(pending)
        assert select_practice_targets(session, 1, now=NOW) == []
        assert pending in session.new
        session.rollback()
    ids = seed(factory)
    with factory() as session:
        entry = session.get(Vocabulary, ids["explore"])
        entry.status = "KNOWN"
        assert select_practice_targets(session, 1, now=NOW)[0].status == "UNKNOWN"
        assert entry in session.dirty and entry.status == "KNOWN"
        session.rollback()


def test_real_inflections_and_overlapping_phrase_targets_have_exact_unicode_ranges():
    content = passage("🙂 Models learned from training\tdata.")
    result = validate_practice_text("A Useful Reading", content,
                                   [target("model"), target("learn", 2), target("training data", 3), target("data", 4)], "short")
    assert result["word_count"] == 90
    assert {item["vocabulary_id"] for item in result["matches"]} == {1, 2, 3, 4}
    assert result["matches"][0]["word"] == "Models" and result["matches"][0]["text_start"] == 2
    for item in result["matches"]:
        assert content[item["text_start"]:item["text_end"]] == item["word"]
    assert next(item for item in result["matches"] if item["vocabulary_id"] == 3)["word"] == "training\tdata"


@pytest.mark.parametrize("prefix,lemma", [("A remodeling project.", "model"),
                                           ("We use training, data.", "training data"),
                                           ("We use training. Data is useful.", "training data"),
                                           ("We use trained data.", "training data")])
def test_target_coverage_does_not_guess_substrings_or_join_across_punctuation(prefix, lemma):
    with pytest.raises(ValueError):
        validate_practice_text("A Useful Reading", passage(prefix), [target(lemma)], "short")


@pytest.mark.parametrize("prefix,lemma", [("We can't stop.", "can't"), ("We can’t stop.", "can't"),
                                           ("Use cross-validation carefully.", "cross-validation")])
def test_apostrophe_and_hyphenated_targets_remain_complete_tokens(prefix, lemma):
    result = validate_practice_text("A Useful Reading", passage(prefix), [target(lemma)], "short")
    assert result["matches"]


@pytest.mark.parametrize("length,count,valid", [("short", 79, False), ("short", 80, True),
                                               ("short", 120, True), ("short", 121, False),
                                               ("medium", 149, False), ("medium", 150, True),
                                               ("medium", 200, True), ("long", 250, True),
                                               ("long", 300, True), ("long", 301, False)])
def test_chosen_length_is_verified_from_english_tokens(length, count, valid):
    if valid:
        assert validate_practice_text("A Useful Reading", passage(count=count), [target()], length)["word_count"] == count
    else:
        with pytest.raises(ValueError):
            validate_practice_text("A Useful Reading", passage(count=count), [target()], length)


@pytest.mark.parametrize("kind", ["chinese", "html", "fence", "control", "surrogate", "bad-title"])
def test_invalid_model_passages_are_never_repaired_as_fake_english(kind):
    content, title = passage(), "A Useful Reading"
    if kind == "chinese":
        content += "中文内容" * 100
    elif kind == "html":
        content = passage("<script>Models learn.</script>")
    elif kind == "fence":
        content = "```\n" + content + "\n```"
    elif kind == "control":
        content += "\x00"
    elif kind == "surrogate":
        content += "\ud800"
    else:
        title = "中文标题"
    with pytest.raises(ValueError):
        validate_practice_text(title, content, [target()], "short")
