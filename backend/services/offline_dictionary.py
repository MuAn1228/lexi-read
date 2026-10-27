"""Read-only indexed ECDICT lookup with a small, derived on-disk cache.

The bundled asset is immutable. Its first use extracts a hash-checked copy into
the application's dictionary-cache directory, never into the user's database.
Only SQLite's small page cache and 512 raw rows stay in memory.
"""
from __future__ import annotations

from functools import lru_cache
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
import unicodedata

from backend.services.lemmatization import normalize_word
from backend.services.supplemental_dictionary import lookup_supplement, source_revision as supplement_revision


SOURCE_URL = "https://github.com/skywind3000/ECDICT"
_ASSET_DIR = Path(__file__).resolve().parents[1] / "data_assets" / "dictionary"
_SPELLING = re.compile(r"[a-z]+(?:[' -][a-z]+)*", re.ASCII)
_DIGEST = re.compile(r"[a-f0-9]{64}", re.ASCII)
_PART_OF_SPEECH = re.compile(r"^(?P<value>(?:[a-z]{1,10}\.)+(?:\s*/\s*(?:[a-z]{1,10}\.)+)*)\s*", re.I)
_LOCK = threading.RLock()
_connection_handle: sqlite3.Connection | None = None
_FORM_LABELS = {
    "p": "过去式", "d": "过去分词", "i": "现在分词", "3": "第三人称单数",
    "r": "比较级", "t": "最高级", "s": "复数", "0": "原形",
}


class OfflineDictionaryUnavailable(RuntimeError):
    """An installation/cache issue, rather than an absent dictionary entry."""


def _unavailable() -> OfflineDictionaryUnavailable:
    return OfflineDictionaryUnavailable("离线英汉词典暂时无法加载，请检查安装文件和本机存储空间。")


def _cache_directory() -> Path:
    # Resolve lazily: importing the module must not load settings or create data.
    from backend.config import get_settings
    return get_settings().storage_dir / "dictionary-cache"


def _spelling(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = " ".join(value.strip().lower().replace("’", "'").replace("–", "-").split())
    return value if len(value) <= 150 and _SPELLING.fullmatch(value) else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest() -> dict:
    manifest = json.loads((_ASSET_DIR / "manifest.json").read_text(encoding="utf-8"))
    if (not isinstance(manifest, dict)
            or manifest.get("format_version") != 1
            or not _DIGEST.fullmatch(str(manifest.get("database_sha256", "")))
            or not _DIGEST.fullmatch(str(manifest.get("asset_sha256", "")))
            or type(manifest.get("database_bytes")) is not int
            or not 0 < manifest["database_bytes"] <= 512 * 1024 * 1024):
        raise _unavailable()
    return manifest


@lru_cache(maxsize=1)
def _database_path() -> Path:
    with _LOCK:
        temporary: Path | None = None
        try:
            manifest = _manifest()
            digest = manifest["database_sha256"]
            cache_dir = _cache_directory()
            cache_dir.mkdir(parents=True, exist_ok=True)
            destination = cache_dir / f"{digest}.sqlite3"
            if (destination.is_file() and destination.stat().st_size == manifest["database_bytes"]
                    and _sha256(destination) == digest):
                return destination
            compressed = _ASSET_DIR / "ecdict.sqlite3.gz"
            if _sha256(compressed) != manifest["asset_sha256"]:
                raise _unavailable()
            descriptor, name = tempfile.mkstemp(prefix=".ecdict-", suffix=".tmp", dir=cache_dir)
            temporary = Path(name)
            uncompressed_hash = hashlib.sha256()
            length = 0
            with os.fdopen(descriptor, "wb") as target, gzip.open(compressed, "rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    length += len(chunk)
                    if length > manifest["database_bytes"]:
                        raise _unavailable()
                    uncompressed_hash.update(chunk)
                    target.write(chunk)
            if length != manifest["database_bytes"] or uncompressed_hash.hexdigest() != digest:
                raise _unavailable()
            try:
                os.replace(temporary, destination)
            except PermissionError:
                # A second process may have already published and opened it.
                if not destination.is_file() or _sha256(destination) != digest:
                    raise
            return destination
        except OfflineDictionaryUnavailable:
            raise
        except (OSError, EOFError, ValueError, TypeError, KeyError) as error:
            raise _unavailable() from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def _connection() -> sqlite3.Connection:
    global _connection_handle
    if _connection_handle is None:
        path = _database_path()
        _connection_handle = sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True, check_same_thread=False,
        )
        _connection_handle.execute("PRAGMA query_only=ON")
        _connection_handle.execute("PRAGMA cache_size=-2048")
        _connection_handle.execute("PRAGMA mmap_size=0")
    return _connection_handle


def _reset_caches() -> None:
    """Release the derived connection; useful after storage restore and in tests."""
    global _connection_handle
    with _LOCK:
        if _connection_handle is not None:
            _connection_handle.close()
            _connection_handle = None
        _database_path.cache_clear()
        _row.cache_clear()


@lru_cache(maxsize=512)
def _row(key: str) -> tuple | None:
    with _LOCK:
        for attempt in range(2):
            try:
                return _connection().execute(
                    "SELECT headword, phonetic, chinese, english, exchange FROM entries WHERE spelling=?", (key,),
                ).fetchone()
            except (sqlite3.Error, OSError) as error:
                _reset_caches()
                if attempt:
                    raise _unavailable() from error
    return None


def normalize_spelling(value: object) -> str | None:
    """Normalize literal English spelling without changing word forms."""
    return _spelling(value)


def _suggestion_rows(sql: str, parameters: tuple) -> tuple[tuple[str, str, str, int], ...]:
    # Share lookup's lock, read-only connection, and one reconnect attempt. The
    # queries below select bounded indexed ranges/keys, never all dictionary rows.
    with _LOCK:
        for attempt in range(2):
            try:
                return tuple(_connection().execute(sql, parameters).fetchall())
            except (sqlite3.Error, OSError) as error:
                _reset_caches()
                if attempt:
                    raise _unavailable() from error
    return ()


def prefix_rows(prefix: str, limit: int = 512) -> tuple[tuple[str, str, str, int], ...]:
    """Return a bounded literal-prefix pool in indexed spelling order."""
    key = _spelling(prefix)
    if key is None or sum(character.isalpha() for character in key) < 2:
        return ()
    # Every accepted spelling ends in an ASCII letter. Its next code point is
    # an exclusive bound for every continuation of this exact prefix.
    upper = key[:-1] + chr(ord(key[-1]) + 1)
    return _suggestion_rows(
        "SELECT spelling, headword, chinese, (phonetic<>'' AND english<>'') FROM entries "
        "WHERE spelling>=? AND spelling<? ORDER BY spelling LIMIT ?",
        (key, upper, max(0, min(limit, 512))),
    )


def spelling_rows(spellings: tuple[str, ...]) -> tuple[tuple[str, str, str, int], ...]:
    """Look up a bounded set of generated spellings through the primary key."""
    if len(spellings) > 4096:
        raise ValueError("Too many spelling candidates")
    keys = tuple(sorted({key for value in spellings if (key := _spelling(value)) is not None}))
    rows: list[tuple[str, str, str, int]] = []
    # Stay below SQLite builds with the historical 999 parameter limit. Holding
    # the outer lock also prevents cache resets between this operation's chunks.
    with _LOCK:
        for offset in range(0, len(keys), 400):
            batch = keys[offset:offset + 400]
            placeholders = ",".join("?" for _ in batch)
            rows.extend(_suggestion_rows(
                "SELECT spelling, headword, chinese, (phonetic<>'' AND english<>'') FROM entries "
                f"WHERE spelling IN ({placeholders})", batch,
            ))
    return tuple(rows)


def _senses(value: str) -> list[str]:
    return list(dict.fromkeys(part.strip() for part in value.splitlines() if part.strip()))


def _forms(exchange: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in exchange.split("/"):
        kind, separator, value = item.partition(":")
        if not separator or kind not in _FORM_LABELS:
            continue
        for word in value.split(","):
            word = word.strip()
            if _spelling(word) and (kind, word) not in seen:
                result.append({"label": _FORM_LABELS[kind], "word": word})
                seen.add((kind, word))
    return result


def lookup(word: str, lemma: str | None = None) -> dict | None:
    """Return all stored senses; literal spelling takes priority over a lemma.

    A caller-supplied lemma must agree with the application's own normalizer.
    No approximate spelling, substring matching or invented definition is used.
    Each result is newly constructed so callers cannot mutate the cached row.
    """
    key = _spelling(word)
    if key is None:
        return None
    row = _row(key)
    if row is None:
        try:
            canonical = normalize_word(key)
            if lemma is not None:
                requested = _spelling(lemma)
                if requested is None or normalize_word(requested) != canonical:
                    return None
                if requested != key:
                    row = _row(requested)
        except ValueError:
            return None
        if row is None and canonical != key:
            row = _row(canonical)
    if row is None:
        return lookup_supplement(key)
    return {
        "headword": row[0], "phonetic": row[1],
        "chinese": _senses(row[2]), "english": _senses(row[3]), "forms": _forms(row[4]),
        "source": "ecdict", "source_url": SOURCE_URL,
    }


def _sense_text(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).strip())


def sense_key(source: str, headword: str, meaning: str) -> str:
    """Return an opaque content identity; display order and dictionary revision are not identity."""
    identity = "\0".join(("sense-v1", source, _sense_text(headword).casefold(), _sense_text(meaning)))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def sense_catalog(word: str, lemma: str | None = None) -> list[dict]:
    """Return stable, offline-only learnable senses for one exact dictionary result."""
    entry = lookup(word, lemma)
    if entry is None:
        return []
    source = entry["source"]
    revision = (_manifest()["database_sha256"] if source == "ecdict" else supplement_revision())
    result: list[dict] = []
    keys: set[str] = set()
    for ordinal, raw in enumerate(entry["chinese"]):
        meaning = raw.strip()
        key = sense_key(source, entry["headword"], meaning)
        if not meaning or key in keys:
            continue
        match = _PART_OF_SPEECH.match(meaning)
        result.append({
            "sense_key": key,
            "source": source,
            "source_revision": revision,
            "headword": entry["headword"],
            "source_ordinal": ordinal,
            "part_of_speech": match.group("value").lower() if match else None,
            "meaning": meaning,
        })
        keys.add(key)
    return result


def summary(word: str, lemma: str | None = None) -> str | None:
    """A compact Chinese preview; lookup() always retains the complete entry."""
    entry = lookup(word, lemma)
    if entry is None or not entry["chinese"]:
        return None
    text = "；".join(entry["chinese"])
    return text if len(text) <= 220 else text[:219].rstrip() + "…"
