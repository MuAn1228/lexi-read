"""Read-only personal Maimemo notes, separately cached from learning data.

The public API exposes content created by the credential owner, not Maimemo's
built-in dictionary. No endpoint in this module writes to Maimemo or the app DB.
"""
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
from threading import Lock
from time import monotonic, time
from typing import Literal
import unicodedata
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.config import get_settings
from backend.services import maimemo_client as memo

MAX_BYTES = 512_000
MAX_CACHE_BYTES = 2 * 1024 * 1024
MAX_ROWS = 100
MAX_FETCH_SECONDS = 25.0
POSITIVE_TTL = 24 * 3600
EMPTY_TTL = 6 * 3600
ERROR_TTL = 60
_fetch_lock = Lock()
_cache_locks = [Lock() for _ in range(64)]


class AnnotationInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=10_000)
    tags: list[str] = Field(max_length=30)


class AnnotationPhrase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1, max_length=128)
    english: str = Field(min_length=1, max_length=10_000)
    chinese: str = Field(max_length=10_000)
    origin: str | None = Field(default=None, max_length=1000)


class AnnotationNote(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1, max_length=128)
    type: str = Field(max_length=100)
    text: str = Field(min_length=1, max_length=10_000)


class MaimemoAnnotations(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source: Literal["maimemo_personal"] = "maimemo_personal"
    status: Literal["available", "empty", "not_found", "unavailable"]
    word: str = Field(min_length=1, max_length=150)
    interpretations: list[AnnotationInterpretation] = Field(default_factory=list, max_length=MAX_ROWS)
    phrases: list[AnnotationPhrase] = Field(default_factory=list, max_length=MAX_ROWS)
    notes: list[AnnotationNote] = Field(default_factory=list, max_length=MAX_ROWS)
    cached: bool = False
    stale: bool = False
    fetched_at: str | None = None
    warning: str | None = Field(default=None, max_length=1000)


def configured(settings=None) -> bool:
    settings = settings or get_settings()
    return settings.app_mode == "local" and bool(settings.maimemo_api_token.get_secret_value().strip())


def _identity(word: str, user_id: int, settings):
    # Never persist the token, endpoint response envelope, or user source events.
    scope = sha256(settings.maimemo_api_token.get_secret_value().strip().encode()).hexdigest()
    identity = {"version": 1, "user": user_id, "scope": scope, "word": word}
    digest = sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return identity, settings.storage_dir / "maimemo-annotations" / f"{digest}.json"


def _read(path: Path, identity: dict):
    try:
        if path.stat().st_size > MAX_CACHE_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("identity") != identity or any(type(data.get(key)) not in (int, float)
                or not math.isfinite(data[key]) for key in ("expires_at", "saved_at")):
            return None
        result = MaimemoAnnotations.model_validate(data["result"])
        if result.word != identity["word"]:
            return None
        return result.model_copy(update={"cached": True, "stale": result.stale or data["expires_at"] <= time()}), data
    except (OSError, ValueError, TypeError, KeyError, AttributeError, ValidationError):
        return None


def read_annotations(word: str, user_id: int, *, settings=None) -> MaimemoAnnotations | None:
    settings = settings or get_settings()
    if not configured(settings):
        return None
    identity, path = _identity(word, user_id, settings)
    cached = _read(path, identity)
    return cached[0] if cached else None


def _save(path: Path, identity: dict, result: MaimemoAnnotations, ttl: int):
    temporary = path.with_name(f".{path.stem}-{uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"identity": identity, "saved_at": time(), "expires_at": time() + ttl,
                "result": result.model_dump()}
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _text(value, maximum: int, *, empty=False):
    if (not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip())
            or any(unicodedata.category(char) in {"Cs", "Cc"} and char not in "\n\t" for char in value)):
        raise memo.MaimemoError("墨墨个人注解格式不完整，已保留本地词典。")
    return value.strip()


class MaimemoAnnotationClient:
    def __init__(self, token: str, *, transport=None, request_interval=1.6):
        # Reuse the existing credential validation and process-wide throttler.
        checked = memo.MaimemoClient(token, transport=transport, request_interval=request_interval)
        self.token, self.transport, self.interval = checked._token, transport, request_interval

    def _throttle(self, deadline: float):
        if not self.interval:
            return
        remaining = deadline - monotonic()
        if remaining <= 0 or not memo._request_lock.acquire(timeout=remaining):
            raise memo.MaimemoError("读取墨墨个人注解超时，请稍后重试。")
        try:
            delay = 0 if memo._last_request_at is None else self.interval - (monotonic() - memo._last_request_at)
            if delay > 0:
                if delay >= deadline - monotonic():
                    raise memo.MaimemoError("读取墨墨个人注解超时，请稍后重试。")
                memo.sleep(delay)
            memo._last_request_at = monotonic()
        finally:
            memo._request_lock.release()

    def _get(self, client, resource: str, params: dict, deadline: float):
        if monotonic() >= deadline:
            raise memo.MaimemoError("读取墨墨个人注解超时，请稍后重试。")
        self._throttle(deadline)
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise memo.MaimemoError("读取墨墨个人注解超时，请稍后重试。")
        try:
            with client.stream("GET", memo.BASE_URL + "/api/v1/memo/" + resource,
                               params=params, timeout=min(6.0, remaining)) as response:
                if response.status_code in (401, 403):
                    raise memo.MaimemoError("墨墨凭证无效或没有个人内容读取权限，请检查本机配置。")
                if response.status_code == 429:
                    raise memo.MaimemoError("墨墨读取过于频繁，请稍后重试。")
                if response.status_code == 404 and resource == "vocabulary":
                    return {"voc": None}
                if not 200 <= response.status_code < 300:
                    raise memo.MaimemoError("墨墨个人注解暂时无法读取，已保留本地词典。")
                if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise memo.MaimemoError("墨墨注解响应编码不受支持。")
                body = bytearray()
                for chunk in response.iter_bytes():
                    if monotonic() >= deadline or len(body) + len(chunk) > MAX_BYTES:
                        raise memo.MaimemoError("墨墨注解响应超时或超过读取上限。")
                    body.extend(chunk)
                value = json.loads(body)
                if (not isinstance(value, dict) or value.get("success", True) is not True or value.get("errors")):
                    raise memo.MaimemoError("墨墨未能返回个人注解，已保留本地词典。")
                data = value.get("data", value)
                if not isinstance(data, dict):
                    raise memo.MaimemoError("墨墨个人注解格式不完整，已保留本地词典。")
                return data
        except memo.MaimemoError:
            raise
        except Exception:
            # Provider exceptions/URLs/envelopes can echo authorization headers.
            raise memo.MaimemoError("连接墨墨个人注解失败或超时，已保留本地词典。") from None

    def fetch(self, word: str) -> MaimemoAnnotations:
        deadline = monotonic() + MAX_FETCH_SECONDS
        stamp = datetime.now(timezone.utc).isoformat()
        with httpx.Client(headers={"Authorization": "Bearer " + self.token, "Accept": "application/json",
                                   "Accept-Encoding": "identity"}, transport=self.transport,
                          follow_redirects=False, trust_env=False, timeout=6) as client:
            voc = self._get(client, "vocabulary", {"spelling": word}, deadline).get("voc")
            if voc is None:
                return MaimemoAnnotations(word=word, status="not_found", fetched_at=stamp,
                                         warning="墨墨未找到这个拼写，仍可查看离线释义与 AI 讲解。")
            if not isinstance(voc, dict) or _text(voc.get("spelling"), 150).casefold() != word.casefold():
                raise memo.MaimemoError("墨墨返回的词条与当前单词不一致，未使用其注解。")
            voc_id = _text(voc.get("id"), 128)
            values = {"interpretations": [], "phrases": [], "notes": []}
            warnings = []
            for resource in values:
                try:
                    rows = self._get(client, resource, {"voc_id": voc_id}, deadline).get(resource)
                    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
                        raise memo.MaimemoError("墨墨个人注解数量或格式超出读取范围。")
                    converted = []
                    for row in rows:
                        if not isinstance(row, dict) or row.get("status") not in ("PUBLISHED", "UNPUBLISHED", "DELETED"):
                            raise memo.MaimemoError("墨墨个人注解状态无效。")
                        if row["status"] == "DELETED":
                            continue
                        identity = _text(row.get("id"), 128)
                        if resource == "interpretations":
                            tags = row.get("tags", [])
                            if not isinstance(tags, list) or len(tags) > 30:
                                raise memo.MaimemoError("墨墨个人注解标签格式无效。")
                            converted.append(AnnotationInterpretation(id=identity,
                                text=_text(row.get("interpretation"), 10_000),
                                tags=[_text(tag, 100) for tag in tags]))
                        elif resource == "phrases":
                            converted.append(AnnotationPhrase(id=identity, english=_text(row.get("phrase"), 10_000),
                                chinese=_text(row.get("interpretation", ""), 10_000, empty=True),
                                origin=_text(row["origin"], 1000, empty=True) if row.get("origin") is not None else None))
                        else:
                            converted.append(AnnotationNote(id=identity, type=_text(row.get("note_type", ""), 100, empty=True),
                                                            text=_text(row.get("note"), 10_000)))
                    values[resource] = converted
                except (memo.MaimemoError, ValidationError):
                    warnings.append("部分墨墨个人注解未能读取，可以稍后更新；本地词典仍可使用。")
            has_content = any(values.values())
            result = MaimemoAnnotations(word=word, status="available" if has_content else "unavailable" if warnings else "empty",
                fetched_at=stamp, warning=warnings[0] if warnings else None if has_content else
                "这个词没有你在墨墨自己创建的释义、例句或助记；开放 API 不提供墨墨内置词典。", **values)
            # A remote service must not echo the credential back through content.
            if self.token in result.model_dump_json():
                raise memo.MaimemoError("墨墨个人注解内容无法安全显示，已保留本地词典。")
            return result


def fetch_annotations(word: str, user_id: int, *, refresh=False, settings=None) -> MaimemoAnnotations | None:
    settings = settings or get_settings()
    if not configured(settings):
        return None
    identity, path = _identity(word, user_id, settings)
    with _cache_locks[int(path.stem[:8], 16) % len(_cache_locks)]:
        cached = _read(path, identity)
        if cached and (not cached[0].stale and not refresh or time() - cached[1].get("saved_at", 0) < ERROR_TTL):
            return cached[0]
        if not _fetch_lock.acquire(blocking=False):
            return cached[0] if cached else MaimemoAnnotations(word=word, status="unavailable",
                warning="正在读取另一个词的墨墨注解，请稍后重试；本地释义已可查看。")
        try:
            try:
                result = MaimemoAnnotationClient(settings.maimemo_api_token.get_secret_value()).fetch(word)
            except Exception:
                result = MaimemoAnnotations(word=word, status="unavailable",
                    warning="墨墨个人注解暂时无法读取，请稍后重试；本地释义已保留。")
            if result.status == "unavailable" and cached and cached[0].status == "available":
                result = cached[0].model_copy(update={"stale": True, "warning": result.warning})
            ttl = ERROR_TTL if ((result.warning and result.status == "available") or result.status == "unavailable") \
                  else POSITIVE_TTL if result.status == "available" else EMPTY_TTL
            try:
                _save(path, identity, result, ttl)
            except OSError:
                result = result.model_copy(update={"warning": "个人注解已读取，但本机缓存未能保存，下次可能需要重新读取。"})
            return result
        finally:
            _fetch_lock.release()
