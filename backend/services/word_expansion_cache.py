"""Reusable linguistic candidates, never recommendation batches or learning state.

Each request still ranks against current knowledge and issues its own immutable
feedback snapshot. Only successful, nonempty model results may reach this cache.
"""
from concurrent.futures import Future
from copy import deepcopy
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import re
from threading import Lock
from time import time
from typing import Callable
import unicodedata
from uuid import uuid4

from pydantic import ValidationError

from backend.config import Settings
from backend.schemas import Relation
from backend.services.lemmatization import normalize_word
from backend.services.llm import Candidate, CandidateBatch, GeneratedCandidates

logger = logging.getLogger(__name__)
PROMPT_VERSION = 1
CACHE_TTL_SECONDS = 30 * 86400
MAX_CACHE_BYTES = 64 * 1024
_registry_lock = Lock()
_inflight: dict[Path, Future] = {}


def identity(settings: Settings, user_id: int, word: str, lemma: str, relation: Relation) -> tuple[dict, Path]:
    value = {"version": PROMPT_VERSION, "user": user_id,
             "word": re.sub(r"\s+", " ", word.strip().lower().replace("’", "'").replace("–", "-")),
             "lemma": lemma, "relation": relation.value, "provider": settings.llm_provider,
             "model": settings.llm_model, "endpoint": settings.llm_base_url.rstrip("/"),
             "credential": sha256(settings.llm_api_key.encode()).hexdigest(),
             "json_mode": settings.llm_json_mode}
    digest = sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    return value, settings.storage_dir / "word-expansion-candidates" / f"{digest}.json"


def _validated(candidates: list[Candidate], lemma: str, relation: Relation) -> list[Candidate]:
    clean, seen = [], {lemma}
    for raw in candidates:
        try:
            candidate = Candidate.model_validate(raw.model_dump())
            normalized = normalize_word(candidate.word)
        except (ValidationError, ValueError, TypeError, AttributeError):
            continue
        if (candidate.relation != relation or normalized in seen or any(
                not re.search(r"[\u3400-\u9fff]", value) for value in (candidate.meaning, candidate.reason))
                or any(unicodedata.category(char) in {"Cc", "Cs"}
                       for value in (candidate.word, candidate.meaning, candidate.reason) for char in value)):
            continue
        seen.add(normalized)
        clean.append(candidate.model_copy(update={"word": normalized}))
        if len(clean) == 5:
            break
    return clean


def _read(path: Path, value: dict) -> GeneratedCandidates | None:
    try:
        if path.stat().st_size > MAX_CACHE_BYTES:
            return None
        raw = path.read_bytes()
        if len(raw) > MAX_CACHE_BYTES:
            return None
        record = json.loads(raw)
        if not isinstance(record, dict) or record.get("identity") != value:
            return None
        created = record["created_at"]
        now = time()
        if type(created) is not int or not 0 <= now - created < CACHE_TTL_SECONDS:
            return None
        batch = CandidateBatch.model_validate(record["batch"])
        candidates = _validated(batch.candidates, value["lemma"], Relation(value["relation"]))
        if not candidates or len(candidates) != len(batch.candidates):
            return None
        return GeneratedCandidates(candidates, "llm")
    except (OSError, ValueError, TypeError, KeyError, ValidationError, RecursionError):
        return None


def _save(path: Path, value: dict, generated: GeneratedCandidates) -> None:
    temporary = path.with_name(f"{path.stem}-{uuid4().hex}.tmp")
    try:
        raw = json.dumps({"identity": value, "created_at": int(time()),
                          "batch": CandidateBatch(candidates=generated.candidates).model_dump(mode="json")},
                         ensure_ascii=False, separators=(",", ":")).encode()
        if len(raw) > MAX_CACHE_BYTES:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("xb") as stream:
            stream.write(raw)
        os.replace(temporary, path)
    except OSError:
        # A full/read-only disk must not discard an otherwise valid model answer.
        logger.warning("Word expansion cache could not be saved")
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def candidates(settings: Settings, user_id: int, word: str, lemma: str, relation: Relation,
               generate: Callable[[], GeneratedCandidates], *, refresh: bool = False
               ) -> tuple[GeneratedCandidates, bool]:
    value, path = identity(settings, user_id, word, lemma, relation)
    if not refresh:
        cached = _read(path, value)
        if cached is not None:
            return cached, True
    with _registry_lock:
        pending = _inflight.get(path)
        owner = pending is None
        if owner:
            pending = _inflight[path] = Future()
    if not owner:
        shared = deepcopy(pending.result())
        return shared, shared.source == "llm" and bool(shared.candidates)
    try:
        # A previous caller may have finished between the disk read and registry
        # registration. Forced refresh intentionally bypasses that second read.
        cached = None if refresh else _read(path, value)
        if cached is not None:
            pending.set_result(cached)
            return deepcopy(cached), True
        generated = generate()
        if generated.source == "llm":
            generated = GeneratedCandidates(_validated(generated.candidates, lemma, relation),
                                            generated.source, generated.warning)
            if generated.candidates:
                _save(path, value, generated)
        pending.set_result(generated)
        return deepcopy(generated), False
    except BaseException as exc:
        pending.set_exception(exc)
        raise
    finally:
        with _registry_lock:
            if _inflight.get(path) is pending:
                del _inflight[path]
