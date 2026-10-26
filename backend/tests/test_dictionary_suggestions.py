"""Indexed literal completions and conservative, read-only spelling suggestions."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from backend.api import dictionary_routes
from backend.services import dictionary_suggestions as suggestions
from backend.services import offline_dictionary as offline
from backend.tests.test_article_library import snapshot
from backend.tests.test_text_import import text_app  # noqa: F401


@pytest.fixture(scope="module")
def cache_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("suggestions-dictionary")


@pytest.fixture(autouse=True)
def isolated_dictionary(monkeypatch, cache_dir):
    offline._reset_caches()
    monkeypatch.setattr(offline, "_cache_directory", lambda: cache_dir)
    yield
    offline._reset_caches()


def forbidden(*args, **kwargs):
    pytest.fail("Suggestions must not perform this operation")


@pytest.mark.parametrize("query,mode", [
    ("", "prefix"), ("  \t ", "prefix"), ("a", "prefix"), ("", "spelling"),
    ("ab", "spelling"), ("a" * 49, "spelling"), ("a" * 150, "spelling"),
])
def test_empty_short_and_overlong_spelling_inputs_do_not_open_storage(monkeypatch, query, mode):
    monkeypatch.setattr(suggestions, "prefix_rows", forbidden)
    monkeypatch.setattr(suggestions, "spelling_rows", forbidden)
    result = suggestions.read_suggestions(query, mode)
    assert not result.items and result.warning is None
    assert result.query == query.strip()


@pytest.mark.parametrize("query", [
    None, 42, "a" * 151, "中文", "bank1", "bank;DROP TABLE entries", "bank_",
    "%bank", "bank\x00", "<script>", "bank/shore", "'bank", "bank--shore",
])
def test_invalid_english_is_rejected_before_storage(monkeypatch, query):
    monkeypatch.setattr(suggestions, "prefix_rows", forbidden)
    monkeypatch.setattr(suggestions, "spelling_rows", forbidden)
    with pytest.raises(ValueError, match="有效英文"):
        suggestions.read_suggestions(query)


@pytest.mark.parametrize("raw,normalized", [
    ("  STUDIES  ", "studies"), ("  Look   After ", "look after"),
    ("mother–in–law", "mother-in-law"), ("can’t", "can't"), ("a" * 150, "a" * 150),
])
def test_spelling_normalizes_case_spaces_and_punctuation_without_stemming(monkeypatch, raw, normalized):
    requested = []
    monkeypatch.setattr(offline, "normalize_word", forbidden)
    monkeypatch.setattr(suggestions, "prefix_rows", lambda key: requested.append(key) or ())
    assert suggestions.read_suggestions(raw).query == normalized
    assert requested == [normalized]


def test_prefix_ranking_prefers_exact_single_short_entries_and_is_bounded(monkeypatch):
    raw = [
        ("bank account", "bank account", "n. 银行账户"),
        ("banker", "banker", "n. 银行家"),
        ("bank", "bank", "n. 银行\nn. 岸"),
        ("banks", "banks", "n. 银行（复数）"),
    ] + [(f"bank{letter}", f"bank{letter}", "存储中的释义") for letter in "zyxwvutrq"]
    monkeypatch.setattr(suggestions, "prefix_rows", lambda key: tuple((*row, 1) for row in raw))
    result = suggestions.read_suggestions("bank")
    assert [item.word for item in result.items] == ["bank", "bankq", "bankr", "banks", "bankt", "banku", "bankv", "bankw"]
    assert result.items[0].meaning == "n. 银行；n. 岸"
    assert len(result.items) == 8
    result.items[0].meaning = "caller mutation"
    result.items.clear()
    assert suggestions.read_suggestions("bank").items[0].meaning == "n. 银行；n. 岸"


def test_meaning_preview_uses_only_stored_chinese_and_is_short(monkeypatch):
    monkeypatch.setattr(suggestions, "prefix_rows", lambda key: (
        ("example", "Example", "详细释义" * 100, 1), ("example empty", "example empty", "  \n", 0),
    ))
    result = suggestions.read_suggestions("exam")
    assert len(result.items) == 1 and result.items[0].word == "Example"
    assert len(result.items[0].meaning) == 140 and result.items[0].meaning.endswith("…")


def test_source_metadata_quality_precedes_sparse_fragments_but_exact_still_wins(monkeypatch):
    monkeypatch.setattr(suggestions, "prefix_rows", lambda key: (
        ("res", "RES", "原词典精确缩写", 0), ("resa", "resa", "稀疏条目", 0),
        ("reset", "reset", "重置", 1), ("rescue", "Rescue", "营救", 1),
        ("resole", "resole", "修鞋底", 1),
    ))
    assert [item.word for item in suggestions.read_suggestions("res").items] == [
        "RES", "reset", "resole", "Rescue", "resa",
    ]
    monkeypatch.setattr(suggestions, "spelling_rows", lambda keys: (
        ("pale", "pale", "苍白", 0), ("apple", "apple", "苹果", 1), ("able", "able", "能够", 0),
    ))
    assert suggestions.read_suggestions("aple", "spelling").items[0].word == "apple"


@pytest.mark.parametrize("query,expected", [
    ("aple", "apple"), ("recieve", "receive"),
    ("resiliant", "resilient"), ("headmster", "headmaster"),
])
def test_real_dictionary_recognizes_typical_single_edit_typos(query, expected):
    result = suggestions.read_suggestions(query, "spelling")
    assert result.query == query and result.mode == "spelling" and result.warning is None
    assert expected in [item.word for item in result.items]
    assert result.items[0].word == expected
    assert query not in [item.word for item in result.items]
    assert len(result.items) <= 8 and all(item.meaning for item in result.items)
    for item in result.items:
        stored = offline._row(offline.normalize_spelling(item.word))
        assert stored and item.meaning.rstrip("…") in "；".join(stored[2].splitlines())


def test_edits_include_all_four_operations_but_never_distant_guesses():
    assert "apple" in suggestions._edits("aple")  # insertion
    assert "apple" in suggestions._edits("appple")  # deletion
    assert "resilient" in suggestions._edits("resiliant")  # substitution
    assert "receive" in suggestions._edits("recieve")  # transposition
    assert "apple" not in suggestions._edits("qzxle")
    assert len(suggestions._edits("abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuv")) < 2900
    assert not suggestions.read_suggestions("zzzzqwertyunrecognizedword", "spelling").items


def test_real_prefix_supports_phrases_and_does_not_return_inflection_lemmas():
    result = suggestions.read_suggestions(" Look   A ")
    assert result.query == "look a"
    assert "look after" in [item.word.lower() for item in result.items]
    assert all(offline.normalize_spelling(item.word).startswith("look a") for item in result.items)
    result = suggestions.read_suggestions("studies")
    assert result.items[0].word == "studies"
    assert "study" not in [item.word for item in result.items]


def test_query_plans_and_actual_sql_use_bounded_primary_key_searches():
    statements = []
    with offline._LOCK:
        connection = offline._connection()
        connection.set_trace_callback(statements.append)
    try:
        assert len(offline.prefix_rows("ba")) <= 512
        assert offline.spelling_rows(tuple(suggestions._edits("aple")))
    finally:
        with offline._LOCK:
            connection.set_trace_callback(None)
    selects = [sql for sql in statements if sql.startswith("SELECT")]
    assert selects and all("LIKE" not in sql for sql in selects)
    assert all("LIMIT 512" in sql or "WHERE spelling IN (" in sql for sql in selects)
    assert max(sql.count(",") for sql in selects) < 403  # bounded IN batches
    with offline._LOCK:
        plans = [connection.execute("EXPLAIN QUERY PLAN " + sql).fetchall() for sql in selects]
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM entries")
    assert all("SEARCH entries USING PRIMARY KEY" in row[-1] for plan in plans for row in plan)


def test_generated_candidate_key_count_is_bounded_before_any_query(monkeypatch):
    monkeypatch.setattr(offline, "_connection", forbidden)
    with pytest.raises(ValueError, match="Too many"):
        offline.spelling_rows(("apple",) * 4097)
    assert offline.spelling_rows(()) == ()
    assert offline.prefix_rows("a") == ()
    assert offline.prefix_rows("not_valid") == ()


def test_shared_connection_recovers_after_close_and_concurrent_reads():
    assert suggestions.read_suggestions("bank").items
    with offline._LOCK:
        offline._connection().close()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda pair: suggestions.read_suggestions(*pair),
                                [("bank", "prefix"), ("recieve", "spelling")] * 6))
    assert all(result.items and result.warning is None for result in results)


@pytest.mark.parametrize("mode,target", [("prefix", "prefix_rows"), ("spelling", "spelling_rows")])
def test_unavailable_dictionary_returns_safe_warning(monkeypatch, mode, target):
    def unavailable(*args):
        raise offline.OfflineDictionaryUnavailable("private-path-and-secret")
    monkeypatch.setattr(suggestions, target, unavailable)
    result = suggestions.read_suggestions("apple", mode)
    assert not result.items and "暂时不可用" in result.warning
    assert "private" not in result.warning


def test_sqlite_fault_retries_once_then_returns_warning(monkeypatch):
    attempts = []

    class BrokenConnection:
        def execute(self, *args):
            attempts.append(args)
            raise sqlite3.DatabaseError("private database path")

    monkeypatch.setattr(offline, "_connection", BrokenConnection)
    monkeypatch.setattr(offline, "_reset_caches", lambda: None)
    result = suggestions.read_suggestions("bank")
    assert len(attempts) == 2 and not result.items and "暂时不可用" in result.warning
    assert "private" not in result.warning


def test_static_api_route_is_selected_before_word_and_never_uses_model_or_learning_writes(text_app, monkeypatch):
    from sqlalchemy import event

    client, factory, _ = text_app
    monkeypatch.setattr(dictionary_routes, "read_dictionary", forbidden)
    monkeypatch.setattr("backend.services.llm.LLMService._complete", forbidden)
    before = snapshot(factory)
    learning_sql = []

    def capture(_connection, _cursor, statement, *_args):
        learning_sql.append(statement)

    event.listen(factory.kw["bind"], "before_cursor_execute", capture)
    try:
        for mode in ("prefix", "spelling"):
            response = client.get("/api/dictionary/suggestions/query", params={"q": "aple", "mode": mode})
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "private, no-store"
            assert set(response.json()) == {"query", "mode", "items", "warning"}
            assert response.json()["query"] == "aple" and response.json()["mode"] == mode
            assert all(set(item) == {"word", "meaning"} for item in response.json()["items"])
    finally:
        event.remove(factory.kw["bind"], "before_cursor_execute", capture)
    assert not learning_sql
    assert snapshot(factory) == before


def test_suggestions_word_still_uses_ordinary_dictionary_result(text_app, monkeypatch):
    from backend.services import dictionary

    client, _, settings = text_app
    monkeypatch.setattr(dictionary, "get_settings", lambda: settings)
    monkeypatch.setattr(dictionary_routes, "read_suggestions", forbidden)
    response = client.get("/api/dictionary/suggestions")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    result = dictionary.DictionaryResult.model_validate(response.json())
    assert result.word == "suggestions"
    assert result.dictionary and result.dictionary.headword == "suggestions"
    assert "items" not in response.json() and "mode" not in response.json()


@pytest.mark.parametrize("parameters,status", [
    ({}, 200), ({"q": " "}, 200), ({"q": "a"}, 200),
    ({"q": "bank", "mode": "other"}, 422), ({"q": "中文"}, 422),
    ({"q": "a" * 151}, 422), ({"q": "bank%"}, 422), ({"q": "<script>"}, 422),
])
def test_api_empty_and_invalid_requests(text_app, parameters, status):
    client, _, _ = text_app
    response = client.get("/api/dictionary/suggestions/query", params=parameters)
    assert response.status_code == status
    assert response.headers["cache-control"] == "private, no-store"
    if status == 200:
        assert not response.json()["items"]


def test_public_suggestions_require_valid_guest_auth_and_do_not_change_either_users_data(text_app):
    from backend.auth import token_digest
    from backend.models import GuestCredential, User
    from backend.public_limits import PublicUsage
    from sqlalchemy import select

    client, factory, settings = text_app
    settings.app_mode = "public"
    tokens = ["a" * 43, "b" * 43]
    with factory() as session:
        session.add(User(id=3))
        session.flush()
        session.add_all(GuestCredential(token_hash=token_digest(token), user_id=index + 2)
                        for index, token in enumerate(tokens))
        session.commit()
    before = snapshot(factory)
    url = "/api/dictionary/suggestions/query?q=bank"
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"Authorization": "Bearer " + "c" * 43}).status_code == 401
    responses = [client.get(url, headers={"Authorization": "Bearer " + token}) for token in tokens]
    assert all(response.status_code == 200 for response in responses)
    assert responses[0].json() == responses[1].json()
    assert snapshot(factory) == before
    with factory() as session:
        assert not session.scalars(select(PublicUsage)).all()
