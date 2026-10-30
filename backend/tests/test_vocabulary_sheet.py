"""Article vocabulary sheets are ordered, local, isolated, and side-effect free."""

from sqlalchemy import select

from backend.models import Document, LearningEvent, ReviewSchedule, Vocabulary
from backend.services.offline_dictionary import OfflineDictionaryUnavailable
from backend.tests.test_text_import import payload, post, text_app  # noqa: F401


def learning_snapshot(factory):
    with factory() as session:
        vocabulary = [
            (item.id, item.word, item.lemma, item.status, item.mastery_score,
             item.seen_count, item.lookup_count, item.updated_at)
            for item in session.scalars(select(Vocabulary).order_by(Vocabulary.id))
        ]
        events = [(item.id, item.event_type, item.lemma, item.details)
                  for item in session.scalars(select(LearningEvent).order_by(LearningEvent.id))]
        schedules = [(item.vocabulary_id, item.due_at, item.known_streak)
                     for item in session.scalars(select(ReviewSchedule).order_by(ReviewSchedule.vocabulary_id))]
        return vocabulary, events, schedules


def test_sheet_deduplicates_in_article_order_and_keeps_complete_local_meanings(text_app, monkeypatch):
    client, factory, _ = text_app
    document = post(client, payload("Models apple models mystery. Apple!", "Portable words"))
    rows = {
        "model": {"headword": "model", "phonetic": "\u02c8m\u0252dl", "chinese": ["n. \u6a21\u578b", "v. \u5efa\u6a21"]},
        "apple": {"headword": "apple", "phonetic": "\u02c8\u00e6pl", "chinese": ["n. \u82f9\u679c"]},
    }

    def dictionary(word, lemma):
        assert word == lemma
        return rows.get(word)

    monkeypatch.setattr("backend.services.vocabulary_sheet.lookup", dictionary)
    before = learning_snapshot(factory)
    response = client.get(f"/api/documents/{document['id']}/vocabulary-sheet")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    sheet = response.json()
    assert sheet["document_id"] == document["id"] and sheet["title"] == "Portable words"
    assert sheet["item_count"] == 3 and sheet["occurrence_count"] == 5
    assert [item["lemma"] for item in sheet["items"]] == ["model", "apple", "mystery"]
    assert sheet["items"][0] == {
        "word": "model", "lemma": "model", "article_word": "Models", "phonetic": "\u02c8m\u0252dl",
        "meanings": ["n. \u6a21\u578b", "v. \u5efa\u6a21"], "count": 2,
        "first_occurrence_id": document["words"][0]["id"],
    }
    assert sheet["items"][1]["count"] == 2
    assert sheet["items"][2]["word"] == "mystery"
    assert sheet["items"][2]["meanings"] == [] and sheet["items"][2]["phonetic"] is None
    assert sheet["dictionary_warning"] is None
    assert learning_snapshot(factory) == before


def test_sheet_only_contains_unassessed_words_and_never_uses_ai(text_app, monkeypatch):
    client, factory, _ = text_app
    document = post(client, payload("Model apple mystery fresh."))
    client.put("/api/vocabulary/model", json={"status": "KNOWN", "document_id": document["id"]})
    client.put("/api/vocabulary/apple", json={"status": "UNKNOWN", "document_id": document["id"]})
    client.put("/api/vocabulary/mystery", json={"status": "UNCERTAIN", "document_id": document["id"]})
    monkeypatch.setattr("backend.services.vocabulary_sheet.lookup", lambda word, lemma: None)
    monkeypatch.setattr("backend.services.llm.LLMService._complete",
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("AI must not run")))
    before = learning_snapshot(factory)
    sheet = client.get(f"/api/documents/{document['id']}/vocabulary-sheet").json()
    assert [item["lemma"] for item in sheet["items"]] == ["fresh"]
    assert sheet["item_count"] == sheet["occurrence_count"] == 1
    assert learning_snapshot(factory) == before


def test_sheet_keeps_words_when_dictionary_asset_is_unavailable(text_app, monkeypatch):
    client, _, _ = text_app
    document = post(client, payload("Unlisted second third."))
    calls = 0

    def unavailable(*_args):
        nonlocal calls
        calls += 1
        raise OfflineDictionaryUnavailable("offline")

    monkeypatch.setattr("backend.services.vocabulary_sheet.lookup", unavailable)
    response = client.get(f"/api/documents/{document['id']}/vocabulary-sheet")
    assert response.status_code == 200
    sheet = response.json()
    assert sheet["item_count"] == 3 and calls == 1
    assert sheet["items"][0]["meanings"] == []
    assert "离线词典" in sheet["dictionary_warning"]


def test_sheet_omits_repeated_sentence_payload_from_long_unpunctuated_articles(text_app, monkeypatch):
    client, _, _ = text_app
    # Every occurrence shares one long sentence. Returning that sentence once
    # per unique word would make the response grow quadratically.
    words = ["term" + chr(97 + first) + chr(97 + second)
             for first in range(10) for second in range(10)]
    document = post(client, payload(" ".join(words), "Long glossary"))
    monkeypatch.setattr("backend.services.vocabulary_sheet.lookup", lambda *_args: None)
    response = client.get(f"/api/documents/{document['id']}/vocabulary-sheet")
    assert response.status_code == 200
    assert len(response.content) < 30_000
    assert len(response.json()["items"]) == len(words)
    assert all("sentence" not in item for item in response.json()["items"])


def test_sheet_rejects_deleted_or_other_users_documents(text_app, monkeypatch):
    client, factory, _ = text_app
    deleted = post(client, payload("Deleted words."))
    assert client.delete(f"/api/documents/{deleted['id']}").status_code == 200
    assert client.get(f"/api/documents/{deleted['id']}/vocabulary-sheet").status_code == 404

    private = post(client, payload("Private words."))
    with factory() as session:
        document = session.get(Document, private["id"])
        document.user_id = 2
        session.commit()
    monkeypatch.setattr("backend.services.vocabulary_sheet.lookup", lambda word, lemma: None)
    assert client.get(f"/api/documents/{private['id']}/vocabulary-sheet").status_code == 404
