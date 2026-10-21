"""Bounded, read-only access to Maimemo study records.

The endpoint has no offset/cursor and its list order cannot be relied upon.
Counted date buckets overlap at their split date. Never skip a date to escape
a full page of ties: that would silently omit vocabulary.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import re
from threading import Lock
from time import monotonic, sleep

import httpx


BASE_URL = "https://open.maimemo.com/open"
PRIMARY_PATH = "/api/v1/memo/study/query_study_records"
FALLBACK_PATH = "/api/v1/study/query_study_records"
PAGE_SIZE = 1000
MAX_REQUESTS = 32
MAX_RECORDS = 30000
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
HTTP_TIMEOUT = 15.0
MAX_FETCH_SECONDS = 150.0
_BEIJING = timezone(timedelta(hours=8))
_DATE = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?"
    r"(?:Z|[+-]\d{2}:\d{2})?)?\Z"
)
_RESPONSES = frozenset({"FAMILIAR", "VAGUE", "FORGET", "WELL_FAMILIAR", "CANCEL_WELL_FAMILIAR"})
_TAGS = frozenset({"STICKING", "WELL_FAMILIAR"})
_request_lock = Lock()
_last_request_at: float | None = None


class MaimemoError(Exception):
    """Only locally authored messages may cross the API boundary."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


class _RequestLimitReached(Exception):
    pass


class _ReadDeadlineReached(Exception):
    pass


class _RecordLimitReached(Exception):
    pass


@dataclass
class _Run:
    path: str = PRIMARY_PATH
    requests: int = 0
    deadline: float = 0


def _throttle(interval: float) -> None:
    global _last_request_at
    if interval == 0:
        return
    # Shared by every client in this process, including fallback requests.
    with _request_lock:
        if _last_request_at is not None:
            delay = interval - (monotonic() - _last_request_at)
            if delay > 0:
                sleep(delay)
        _last_request_at = monotonic()


def _invalid_record() -> MaimemoError:
    return MaimemoError("墨墨返回了格式不正确的学习记录，已停止读取，请稍后重试。")


def _short_string(value, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise _invalid_record()
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise _invalid_record()
    return value.strip()


def _iso_date(value, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or len(value) > 64 or not _DATE.fullmatch(value):
        raise _invalid_record()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise _invalid_record() from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_BEIJING)
    return parsed.isoformat()


def _date_key(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=_BEIJING)


def _record(value) -> dict:
    if not isinstance(value, dict):
        raise _invalid_record()
    study_count = value.get("study_count")
    if type(study_count) is not int or study_count < 0:
        raise _invalid_record()
    last_response = value.get("last_response")
    # The live API uses this protobuf sentinel for "no feedback" even though
    # its published schema omits the value. It is not a learning response.
    if last_response == "STUDY_RESPONSE_UNSPECIFIED":
        last_response = None
    if last_response is not None and (not isinstance(last_response, str) or last_response not in _RESPONSES):
        raise _invalid_record()
    tags = value.get("tags")
    if isinstance(tags, str):
        tags = [tags]
    if not isinstance(tags, list) or any(not isinstance(tag, str) or tag not in _TAGS for tag in tags):
        raise _invalid_record()
    # Explicit construction prevents unknown server fields reaching persistence.
    return {
        "voc_id": _short_string(value.get("voc_id"), 128),
        "voc_spelling": _short_string(value.get("voc_spelling"), 200),
        "study_count": study_count,
        "add_date": _iso_date(value.get("add_date"), required=True),
        "first_study_date": _iso_date(value.get("first_study_date")),
        "last_study_date": _iso_date(value.get("last_study_date")),
        "next_study_date": _iso_date(value.get("next_study_date")),
        "last_response": last_response,
        "tags": list(dict.fromkeys(tags)),
    }


class MaimemoClient:
    def __init__(self, token: str, *, transport=None, request_interval: float = 1.6):
        if (not isinstance(token, str) or not token.strip() or len(token) > 4096
                or any(ord(char) < 33 or ord(char) > 126 for char in token.strip())):
            raise MaimemoError("墨墨 API 凭证格式无效，请检查本机配置。")
        if (isinstance(request_interval, bool) or not isinstance(request_interval, (int, float))
                or not math.isfinite(request_interval) or request_interval < 0):
            raise MaimemoError("墨墨请求间隔配置无效。")
        self._token = token.strip()
        self._transport = transport
        self._request_interval = request_interval

    def _query(self, client: httpx.Client, run: _Run, query: dict) -> dict:
        while True:
            if run.requests >= MAX_REQUESTS:
                raise _RequestLimitReached()
            if monotonic() >= run.deadline:
                raise _ReadDeadlineReached()
            _throttle(self._request_interval)
            remaining = run.deadline - monotonic()
            if remaining <= 0:
                raise _ReadDeadlineReached()
            run.requests += 1
            try:
                with client.stream("POST", BASE_URL + run.path, json=query,
                                   timeout=min(HTTP_TIMEOUT, remaining)) as response:
                    if response.status_code == 404 and run.path == PRIMARY_PATH:
                        run.path = FALLBACK_PATH
                        continue
                    if response.status_code in (401, 403):
                        raise MaimemoError("墨墨凭证无效、已过期或无权读取学习记录，请检查本机配置。")
                    if response.status_code == 429:
                        raise MaimemoError("墨墨请求过于频繁，请稍后重新读取。")
                    if response.status_code < 200 or response.status_code >= 300:
                        raise MaimemoError("墨墨服务暂时无法读取学习记录，请稍后重试。")
                    length = response.headers.get("Content-Length")
                    if length is not None and (not length.isascii() or not length.isdecimal()
                                               or len(length) > 12 or int(length) > MAX_RESPONSE_BYTES):
                        raise MaimemoError("墨墨响应超过安全大小或长度无效，已停止读取。")
                    # Request identity encoding so the bounded read does not
                    # need to decompress a potentially unbounded payload.
                    if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                        raise MaimemoError("墨墨响应编码不受支持，已停止读取。")
                    body = bytearray()
                    # Preserve transport chunk boundaries: buffering tiny chunks
                    # until 64KiB would postpone the overall deadline check.
                    for chunk in response.iter_bytes():
                        if monotonic() >= run.deadline:
                            raise _ReadDeadlineReached()
                        if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise MaimemoError("墨墨响应超过安全大小，已停止读取。")
                        body.extend(chunk)
                    payload = json.loads(body)
            except (MaimemoError, _ReadDeadlineReached):
                raise
            except httpx.TimeoutException:
                if monotonic() >= run.deadline:
                    raise _ReadDeadlineReached() from None
                raise MaimemoError("墨墨连接超时，请稍后重试。") from None
            except Exception:
                # Do not include URLs, headers, provider messages or exception
                # strings; providers can echo the credential in all of them.
                raise MaimemoError("墨墨连接失败或响应格式无效，请稍后重试。") from None
            if not isinstance(payload, dict):
                raise MaimemoError("墨墨响应格式无效，请稍后重试。")
            if "success" in payload and payload["success"] is not True:
                raise MaimemoError("墨墨未能返回学习记录，请检查凭证或稍后重试。")
            if payload.get("errors"):
                raise MaimemoError("墨墨未能返回学习记录，请检查凭证或稍后重试。")
            data = payload.get("data", payload)
            if not isinstance(data, dict):
                raise MaimemoError("墨墨响应格式无效，请稍后重试。")
            return data

    def _count(self, client: httpx.Client, run: _Run, bounds: dict | None = None) -> int:
        query = {"as_count": True}
        if bounds:
            query["next_study_date"] = bounds
        count = self._query(client, run, query).get("count")
        if type(count) is not int or count < 0:
            raise MaimemoError("墨墨返回的记录总数无效，无法核对读取结果。")
        return count

    def fetch_records(self) -> dict:
        try:
            with httpx.Client(
                headers={"Authorization": "Bearer " + self._token,
                         "Accept": "application/json", "Accept-Encoding": "identity"},
                transport=self._transport, timeout=HTTP_TIMEOUT,
                follow_redirects=False, trust_env=False,
            ) as client:
                return self._fetch(client)
        except MaimemoError:
            raise
        except Exception:
            raise MaimemoError("墨墨连接失败或响应格式无效，请稍后重试。") from None

    def _fetch(self, client: httpx.Client) -> dict:
        run = _Run(deadline=monotonic() + MAX_FETCH_SECONDS)
        try:
            initial_total = self._count(client, run)
        except _ReadDeadlineReached:
            raise MaimemoError("墨墨读取超时，请稍后重试。") from None
        records: dict[str, dict] = {}
        warnings: list[str] = []

        def reserve_final_count():
            # Keep one request for the final unfiltered count verification.
            if run.requests >= MAX_REQUESTS - 1:
                raise _RequestLimitReached()

        def page(bounds: dict, expected: int):
            reserve_final_count()
            query = {"limit": PAGE_SIZE}
            if bounds:
                query["next_study_date"] = bounds
            data = self._query(client, run, query)
            rows = data.get("records")
            if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
                raise MaimemoError("墨墨返回的学习记录列表无效，已停止读取。")
            rows = [_record(row) for row in rows]
            if len({row["voc_id"] for row in rows}) != min(expected, PAGE_SIZE):
                warnings.append("日期分组返回的记录数与分组总数不一致，结果可能不完整。")
            for row in rows:
                record_id = row["voc_id"]
                if record_id in records:
                    if records[record_id] != row:
                        warnings.append("读取期间学习记录发生变化，无法确认完整性，请重新预览。")
                elif len(records) < MAX_RECORDS:
                    records[record_id] = row
                if row["next_study_date"] is None:
                    warnings.append("部分记录缺少下次学习日期，无法确认完整性。")
                if len(records) >= MAX_RECORDS:
                    raise _RecordLimitReached()
            return rows

        def pivot(bounds: dict) -> str | None:
            low = _date_key(bounds["start"]) if "start" in bounds else None
            high = _date_key(bounds["end"]) if "end" in bounds else None
            if low is not None and high is not None:
                if low >= high:
                    return None
                midpoint = low + (high - low) / 2
                if low < midpoint < high:
                    return midpoint.isoformat()
                return None
            dates = {}
            for row in records.values():
                value = row["next_study_date"]
                if value is None:
                    continue
                key = _date_key(value)
                if (low is None or key > low) and (high is None or key < high):
                    dates[key] = value
            if not dates:
                return None
            return dates[sorted(dates)[len(dates) // 2]]

        try:
            page({}, initial_total)
            # Count first, then fetch an entire bucket only when <=1000. Samples
            # guide splitting; they never establish which IDs belong to a bucket.
            pending = [({}, initial_total)] if initial_total > PAGE_SIZE else []
            while pending and len(records) < initial_total:
                bounds, total = pending.pop()
                if total == 0:
                    continue
                if total <= PAGE_SIZE:
                    page(bounds, total)
                    continue
                split = pivot(bounds)
                if split is None:
                    previous_size = len(records)
                    page(bounds, total)
                    if len(records) >= initial_total:
                        break
                    split = pivot(bounds)
                    if split is None:
                        warnings.append("日期分组无法继续缩小或没有新增记录，可能存在同日超过分页上限的记录。")
                        continue
                    if len(records) == previous_size:
                        warnings.append("分页未返回新增记录，无法确认已读取全部记录。")
                # Both children include the SAME boundary, preserving the union
                # even when the provider's filtering date differs from its field.
                left = {**bounds, "end": split}
                right = {**bounds, "start": split}
                reserve_final_count()
                left_total = self._count(client, run, left)
                reserve_final_count()
                right_total = self._count(client, run, right)
                if left_total > total or right_total > total or left_total + right_total < total:
                    warnings.append("读取期间日期分组的记录总数发生变化，无法确认完整性。")
                if left_total >= total and right_total >= total:
                    warnings.append("日期分组未能缩小，可能存在同日超过分页上限的记录。")
                    continue
                pending.extend([(right, right_total), (left, left_total)])
        except _RequestLimitReached:
            warnings.append("达到单次读取的请求上限，无法确认已获取全部记录。")
        except _ReadDeadlineReached:
            warnings.append("达到单次读取的时间上限，结果可能不完整，请重新预览。")
        except _RecordLimitReached:
            warnings.append("达到单次读取的记录上限，无法确认已获取全部记录。")
        final_total = None
        try:
            final_total = self._count(client, run)
        except _RequestLimitReached:
            warnings.append("达到单次读取的请求上限，未能完成最终总数核对。")
        except _ReadDeadlineReached:
            warnings.append("达到单次读取的时间上限，未能完成最终总数核对。")
        if final_total is not None and final_total != initial_total:
            warnings.append("读取期间远端记录总数发生变化，请重新预览。")
        if len(records) != initial_total or (final_total is not None and len(records) != final_total):
            warnings.append("已读取的唯一记录数与远端总数不一致，结果可能不完整。")
        complete = not warnings and final_total == initial_total == len(records)
        return {"records": list(records.values()), "remote_total": initial_total,
                "complete": complete, "warning": " ".join(dict.fromkeys(warnings)) or None}
