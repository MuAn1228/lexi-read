"""Real bundled dictionary coverage and isolated cache/build regression tests."""
from concurrent.futures import ThreadPoolExecutor
import csv
import gzip
import hashlib
import json
import sqlite3

import pytest

from backend.services import offline_dictionary as dictionary
from scripts.build_dictionary import build


@pytest.fixture(scope="module")
def cache_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("offline-dictionary")


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch, cache_dir):
    dictionary._reset_caches()
    monkeypatch.setattr(dictionary, "_cache_directory", lambda: cache_dir)
    yield
    monkeypatch.undo()
    dictionary._reset_caches()


@pytest.mark.parametrize("word,meanings", [
    ("light", ["光", "轻的", "点燃"]),
    ("study", ["学习", "研究", "书房"]),
    ("run", ["奔跑", "管理", "运行"]),
    ("set", ["集合", "设定", "固定的"]),
    ("bank", ["银行", "岸"]),
])
def test_real_common_words_retain_multiple_senses_and_metadata(word, meanings):
    entry = dictionary.lookup(word)
    assert entry["headword"] == word
    chinese = "\n".join(entry["chinese"])
    assert all(meaning in chinese for meaning in meanings)
    assert len(entry["english"]) >= 3
    assert entry["phonetic"]
    assert entry["source"] == "ecdict"
    assert entry["source_url"] == "https://github.com/skywind3000/ECDICT"
    assert entry["forms"]


def test_case_and_phrase_spelling_are_normalized_without_losing_source_headword():
    assert dictionary.lookup("  LIGHT  ") == dictionary.lookup("light")
    phrase = dictionary.lookup("  Look   After ")
    assert phrase["headword"] == "look after"
    assert "照顾" in phrase["chinese"][0]
    assert dictionary.lookup("mother–in–law") == dictionary.lookup("mother-in-law")


def test_literal_spelling_wins_over_lemmatization(monkeypatch):
    def forbidden(_word):
        pytest.fail("An exact dictionary spelling must not be replaced by a lemma")
    monkeypatch.setattr(dictionary, "normalize_word", forbidden)
    assert dictionary.lookup("studies", "study")["headword"] == "studies"
    assert dictionary.lookup("went", "go")["chinese"] == ["go的过去式"]


def test_only_consistent_lemma_can_supply_a_missing_inflection(monkeypatch):
    raw = ("study", "stʌdi", "n. 学习\nv. 研究", "n. learning", "p:studied")
    monkeypatch.setattr(dictionary, "_row", lambda word: raw if word == "study" else None)
    assert dictionary.lookup("studies", "study")["headword"] == "study"
    assert dictionary.lookup("studies")["headword"] == "study"
    assert dictionary.lookup("studies", "bank") is None
    assert dictionary.lookup("zzzzqwertyword", "study") is None
    assert dictionary.lookup("studies", "invalid;") is None


@pytest.mark.parametrize("word", [None, 42, "", "  ", "中文", "a" * 151, "bank1", "bank;DROP TABLE entries",
                                       "'bank", "bank_", "%bank", "bank\x00", "<script>", "bank/shore"])
def test_invalid_spelling_never_opens_dictionary(monkeypatch, word):
    def forbidden(_word):
        pytest.fail("Invalid input reached dictionary storage")
    monkeypatch.setattr(dictionary, "_row", forbidden)
    assert dictionary.lookup(word) is None


def test_absent_word_does_not_return_an_approximate_match():
    assert dictionary.lookup("zzzzqwertyunrecognizedword") is None
    assert dictionary.summary("zzzzqwertyunrecognizedword") is None


def test_unstable_normalization_does_not_invent_a_fallback(monkeypatch):
    monkeypatch.setattr(dictionary, "_row", lambda _word: None)
    def unstable(_word):
        raise ValueError("synthetic normalization failure")
    monkeypatch.setattr(dictionary, "normalize_word", unstable)
    assert dictionary.lookup("unknown") is None


def test_returned_lists_and_forms_do_not_mutate_cached_entries():
    original = dictionary.lookup("study")
    changed = dictionary.lookup("study")
    changed["chinese"].append("应当不被缓存")
    changed["english"].clear()
    changed["forms"][0]["word"] = "injected"
    assert dictionary.lookup("study") == original


def test_forms_keep_separate_tenses_and_ignore_exchange_metadata():
    assert dictionary._forms("p:went/d:gone/i:going/3:goes/1:p/0:go/x:bad/p:went/s:bad42") == [
        {"label": "过去式", "word": "went"}, {"label": "过去分词", "word": "gone"},
        {"label": "现在分词", "word": "going"}, {"label": "第三人称单数", "word": "goes"},
        {"label": "原形", "word": "go"},
    ]
    assert {"label": "过去式", "word": "ran"} in dictionary.lookup("run")["forms"]


def test_summary_is_bounded_while_full_entry_is_not_truncated(monkeypatch):
    raw = ("word", "", "详细释义" * 100 + "\n第二个含义", "", "")
    monkeypatch.setattr(dictionary, "_row", lambda _word: raw)
    text = dictionary.summary("word")
    assert len(text) == 220 and text.endswith("…")
    assert len(dictionary.lookup("word")["chinese"][0]) == 400


def test_indexed_connection_and_lru_are_read_only_and_bounded():
    assert dictionary.lookup("bank")
    connection = dictionary._connection()
    assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
    assert connection.execute("PRAGMA cache_size").fetchone()[0] == -2048
    assert connection.execute("PRAGMA mmap_size").fetchone()[0] == 0
    with pytest.raises(sqlite3.OperationalError):
        connection.execute("DELETE FROM entries")
    plan = connection.execute("EXPLAIN QUERY PLAN SELECT headword FROM entries WHERE spelling=?", ("bank",)).fetchone()
    assert "PRIMARY KEY" in plan[-1]
    assert dictionary._row.cache_info().maxsize == 512


def test_first_use_concurrency_publishes_one_valid_database(monkeypatch, tmp_path):
    monkeypatch.setattr(dictionary, "_cache_directory", lambda: tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        entries = list(pool.map(dictionary.lookup, ["light", "bank", "study", "run"] * 4))
    assert all(entry is not None for entry in entries)
    paths = list(tmp_path.glob("*.sqlite3"))
    assert len(paths) == 1
    assert not list(tmp_path.glob("*.tmp"))
    assert hashlib.sha256(paths[0].read_bytes()).hexdigest() == dictionary._manifest()["database_sha256"]


@pytest.mark.parametrize("corruption", ["missing", "manifest", "not-an-object", "asset-sha", "db-sha", "too-large"])
def test_installation_failures_are_safe_and_leave_no_partial_cache(monkeypatch, tmp_path, corruption):
    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    manifest = dictionary._manifest()
    if corruption == "asset-sha":
        (asset_dir / "ecdict.sqlite3.gz").write_bytes(b"damaged-asset")
    elif corruption in {"db-sha", "too-large"}:
        # Small self-contained payload proves decompression is bounded/hash checked.
        payload = gzip.compress(b"not-the-expected-database", mtime=0)
        (asset_dir / "ecdict.sqlite3.gz").write_bytes(payload)
        manifest["asset_sha256"] = hashlib.sha256(payload).hexdigest()
        manifest["database_bytes"] = 1 if corruption == "too-large" else 100
    if corruption != "missing":
        text = "[" if corruption == "manifest" else "[]" if corruption == "not-an-object" else json.dumps(manifest)
        (asset_dir / "manifest.json").write_text(text, encoding="utf-8")
    monkeypatch.setattr(dictionary, "_ASSET_DIR", asset_dir)
    monkeypatch.setattr(dictionary, "_cache_directory", lambda: tmp_path / "derived-cache")
    with pytest.raises(dictionary.OfflineDictionaryUnavailable, match="离线英汉词典"):
        dictionary.lookup("bank")
    assert not list(tmp_path.rglob("*.tmp"))
    assert not list(tmp_path.rglob("*.sqlite3"))


def test_corrupt_existing_cache_is_repaired_from_immutable_asset(monkeypatch, tmp_path):
    manifest = dictionary._manifest()
    destination = tmp_path / f"{manifest['database_sha256']}.sqlite3"
    destination.write_bytes(b"interrupted-write")
    monkeypatch.setattr(dictionary, "_cache_directory", lambda: tmp_path)
    assert dictionary.lookup("light")["headword"] == "light"
    assert hashlib.sha256(destination.read_bytes()).hexdigest() == manifest["database_sha256"]


def test_lost_read_connection_can_be_reopened():
    assert dictionary.lookup("study")
    dictionary._connection().close()
    assert dictionary.lookup("light")["headword"] == "light"


def test_builder_filters_invalid_spellings_and_merges_case_senses_deterministically(tmp_path):
    source = tmp_path / "fixture.csv"
    with source.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["word", "phonetic", "definition", "translation", "exchange"])
        writer.writeheader()
        writer.writerows([
            {"word": "Bank", "phonetic": "bæŋk", "definition": "n. money", "translation": "n. 银行\\nn. 岸", "exchange": "s:banks"},
            {"word": "bank", "translation": "n. 岸\\nv. 存款", "definition": "v. deposit", "exchange": "p:banked"},
            {"word": "bad42", "translation": "不应导入"},
            {"word": "empty", "definition": "English only"},
            {"word": "look after", "translation": "照顾"},
        ])
    one, two = tmp_path / "one", tmp_path / "two"
    manifest = build(source, one, verify_source=False)
    repeated = build(source, two, verify_source=False)
    assert manifest == repeated
    assert manifest["entries"] == 2 and manifest["merged_case_duplicates"] == 1
    assert manifest["omitted_records"] == 2
    assert (one / "ecdict.sqlite3.gz").read_bytes() == (two / "ecdict.sqlite3.gz").read_bytes()
    database = tmp_path / "fixture.sqlite3"
    database.write_bytes(gzip.decompress((one / "ecdict.sqlite3.gz").read_bytes()))
    with sqlite3.connect(database) as connection:
        value = connection.execute("SELECT headword, chinese, english, exchange FROM entries WHERE spelling='bank'").fetchone()
    assert value == ("Bank", "n. 银行\nn. 岸\nv. 存款", "n. money\nv. deposit", "s:banks/p:banked")
    with pytest.raises(ValueError, match="checksum"):
        build(source, tmp_path / "untrusted")
