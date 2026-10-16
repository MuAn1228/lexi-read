import json

import pytest

from backend import migrations
from backend.ocr.service import OCRResult, OCRWord
from backend.services import documents, lemmatization
from backend.tests.test_learning import client, image_bytes
from backend.tests.test_migrations import date, legacy, rows


@pytest.mark.parametrize("forms,expected", [
    (("meetings", "meeting", "meet"), "meet"),
    (("saws", "sawed", "saw", "see"), "see"),
    (("bacteria", "bacterium"), "bacteria"),
    (("laying", "lay", "lie"), "lie"),
    (("beings", "being", "be"), "be"),
    (("generalized", "generalizing", "generalizes", "generalize"), "generalize"),
    (("learning",), "learning"),
    (("training",), "training"),
    (("Unseen   Data", "unseen data"), "unseen data"),
])
def test_dictionary_chains_and_cycles_have_idempotent_identity(forms, expected):
    for word in forms:
        lemma = lemmatization.normalize_word(word)
        assert lemma == expected
        assert lemmatization.normalize_word(lemma) == lemma


def test_protected_term_is_checked_at_each_step(monkeypatch):
    lemmatization.normalize_word.cache_clear()
    monkeypatch.setattr(lemmatization, "lemmatize", lambda word, **_: "learning" if word == "learnings" else "learn")
    try:
        assert lemmatization.normalize_word("learnings") == "learning"
        assert lemmatization.normalize_word("learning") == "learning"
    finally:
        lemmatization.normalize_word.cache_clear()


def test_pathological_chain_fails_instead_of_storing_an_unstable_key(monkeypatch):
    lemmatization.normalize_word.cache_clear()
    monkeypatch.setattr(lemmatization, "lemmatize", lambda word, **_: word + "a")
    try:
        for _ in range(2):
            with pytest.raises(ValueError, match="稳定归一化"):
                lemmatization.normalize_word("pathological")
    finally:
        lemmatization.normalize_word.cache_clear()


@pytest.mark.parametrize("surface,forms", [
    ("meetings", ("meetings", "meeting", "meet")),
    ("bacteria", ("bacteria", "bacterium")),
])
def test_import_mark_lookup_and_history_share_the_same_identity(client, monkeypatch, surface, forms):
    class DictionaryOCR:
        def recognize(self, path):
            return OCRResult(f"{surface}.", 100, 80, [OCRWord(surface, [1, 2, 80, 15], 0, .96)])

    monkeypatch.setattr(documents, "get_ocr_service", lambda: DictionaryOCR())
    response = client.post("/api/documents", files={"file": ("page.png", image_bytes())})
    assert response.status_code == 201, response.text
    doc = response.json()
    occurrence = doc["words"][0]
    marked = client.put(f"/api/vocabulary/{surface}", json={"status": "UNKNOWN", "document_id": doc["id"]})
    assert marked.status_code == 200
    assert client.post("/api/words/analyze", json={"occurrence_id": occurrence["id"]}).status_code == 200
    vocabulary = client.get("/api/vocabulary").json()
    assert len(vocabulary) == 1
    entry = vocabulary[0]
    assert entry["lemma"] == occurrence["lemma"]
    assert (entry["seen_count"], entry["lookup_count"], entry["status"]) == (1, 1, "UNKNOWN")
    assert entry["mastery_score"] == .27
    for word in (*forms, occurrence["lemma"]):
        history = client.get(f"/api/vocabulary/{word}/history")
        assert history.status_code == 200, history.text
        assert history.json()["vocabulary"] == entry
        assert history.json()["last_mark"]["event_type"] == "MARK_UNKNOWN"
        assert [event["event_type"] for event in history.json()["events"]] == ["LOOKUP", "MARK_UNKNOWN"]
    assert client.get(f"/api/documents/{doc['id']}").json()["words"][0]["status"] == "UNKNOWN"


def test_fresh_legacy_chain_and_cycle_keys_do_not_collide(legacy):
    path, engine, connection = legacy
    connection.executemany("INSERT INTO vocabulary VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
        (7, 1, "meetings", "meetings", "KNOWN", .65, 1, 1, date(1), date(2), date(1)),
        (8, 1, "meeting", "meeting", "UNKNOWN", .3, 2, 2, date(2), date(3), date(2)),
        (9, 1, "bacteria", "bacteria", None, None, 3, 0, date(1), date(1), date(1)),
        (10, 1, "bacterium", "bacterium", None, None, 4, 0, date(2), date(2), date(2)),
    ])
    connection.commit()
    migrations.migrate_database(engine)
    merged = rows(path, "SELECT lemma, status, seen_count, lookup_count FROM vocabulary WHERE id >= 7 ORDER BY id")
    assert merged == [
        {"lemma": "meet", "status": "UNKNOWN", "seen_count": 3, "lookup_count": 3},
        {"lemma": "bacteria", "status": None, "seen_count": 7, "lookup_count": 0},
    ]


def test_upgrade_existing_split_keys_preserves_learning_evidence(legacy, monkeypatch):
    path, engine, connection = legacy
    all_migrations = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations[:3])
    migrations.migrate_database(engine)
    # Simulate the old app's once-normalized mark row and twice-normalized
    # observation row after 001-003 had already been installed.
    connection.executemany(
        "INSERT INTO vocabulary (id,user_id,word,lemma,status,mastery_score,seen_count,lookup_count,"
        "created_at,updated_at,first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
            (7, 1, "meetings", "meet", None, None, 2, 0, date(1), date(6), date(1), date(6)),
            (8, 1, "meetings", "meeting", "UNKNOWN", .3, 0, 1, date(2), date(3), None, None),
            (9, 1, "bacteria", "bacteria", "KNOWN", .65, 2, 0, date(1), date(6), date(1), date(6)),
            (10, 1, "bacteria", "bacterium", "UNKNOWN", .3, 3, 0, date(2), date(3), date(2), date(3)),
        ])
    connection.executemany(
        "INSERT INTO learning_events (id,user_id,word,lemma,event_type,document_id,details,created_at) VALUES (?,?,?,?,?,?,?,?)", [
            (5, 1, "meetings", "meeting", "MARK_UNKNOWN", 1, '{"mastery_after":0.3}', date(3)),
            (6, 1, "bacterium", "bacteria", "MARK_KNOWN", 1, '{}', date(2)),
            (7, 1, "bacteria", "bacterium", "MARK_UNKNOWN", 1, '{}', date(3)),
        ])
    connection.executemany("INSERT INTO word_occurrences VALUES (?,?,?,?)", [
        (2, 1, "meetings", "meeting"), (3, 1, "bacteria", "bacterium")])
    connection.commit()
    before_events = rows(path, "SELECT * FROM learning_events ORDER BY id")
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations)
    backup = migrations.migrate_database(engine)
    assert "before-004_stable_vocabulary_identity" in backup.name
    assert len(rows(backup, "SELECT * FROM vocabulary WHERE id >= 7")) == 4
    merged = rows(path, "SELECT * FROM vocabulary WHERE id >= 7 ORDER BY id")
    assert [(v["lemma"], v["status"], v["seen_count"], v["lookup_count"]) for v in merged] == [
        ("meet", "UNKNOWN", 2, 1), ("bacteria", "UNKNOWN", 5, 0)]
    assert merged[0]["mastery_score"] == .3
    assert (merged[0]["created_at"], merged[0]["first_seen_at"], merged[0]["last_seen_at"], merged[0]["updated_at"]) == (date(1), date(1), date(6), date(6))
    after_events = rows(path, "SELECT * FROM learning_events ORDER BY id")
    assert len(after_events) == len(before_events)
    for before, after in zip(before_events, after_events):
        assert after["lemma"] == lemmatization.normalize_word(after["word"])
        assert json.loads(before.pop("details")) == json.loads(after.pop("details"))
        before.pop("lemma")
        after.pop("lemma")
        assert before == after  # No event, raw word, time, or document is invented.
    assert [row["lemma"] for row in rows(path, "SELECT lemma FROM word_occurrences WHERE id >= 2 ORDER BY id")] == ["meet", "bacteria"]
    assert migrations.migrate_database(engine) is None
    assert rows(path, "SELECT * FROM vocabulary WHERE id >= 7 ORDER BY id") == merged
    assert rows(path, "PRAGMA foreign_key_check") == []
