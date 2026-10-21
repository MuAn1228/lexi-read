"""Offline provider contract tests. All records and HTTP responses are synthetic."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import random

import httpx
import pytest

from backend.services import maimemo_client as memo


TOKEN = "synthetic-offline-token-do-not-log"
FIELDS = {
    "voc_id", "voc_spelling", "study_count", "add_date", "first_study_date",
    "last_study_date", "next_study_date", "last_response", "tags",
}


@pytest.fixture(autouse=True)
def no_real_http(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Maimemo tests must never use a real network transport")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(memo, "_last_request_at", None)


def record(identity=0, *, date="2026-01-01T00:00:00+08:00", **changes):
    row = {
        "voc_id": f"voc-{identity}", "voc_spelling": f"word-{identity}",
        "study_count": 4, "add_date": "2025-01-01T00:00:00+08:00",
        "first_study_date": "2025-01-02T00:00:00+08:00",
        "last_study_date": None, "next_study_date": date,
        "last_response": "FORGET", "tags": ["STICKING"],
    }
    return {**row, **changes}


def dataset(size):
    start = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=8)))
    rows = [record(i, date=(start + timedelta(days=i % 180)).isoformat()) for i in range(size)]
    random.Random(20261021).shuffle(rows)
    return rows


def date_key(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone(timedelta(hours=8)))


class Provider:
    """An unordered, inclusive date-filter API with no offset or cursor support."""

    def __init__(self, rows=(), *, envelope=True):
        self.rows = deepcopy(list(rows))
        self.envelope = envelope
        self.calls = []
        self.root_counts = 0

    def payload(self, data):
        return {"success": True, "errors": [], "data": data} if self.envelope else data

    def selected(self, query):
        bounds = query.get("next_study_date", {})
        rows = self.rows
        if "start" in bounds:
            rows = [r for r in rows if r["next_study_date"] is not None
                    and date_key(r["next_study_date"]) >= date_key(bounds["start"])]
        if "end" in bounds:
            rows = [r for r in rows if r["next_study_date"] is not None
                    and date_key(r["next_study_date"]) <= date_key(bounds["end"])]
        return rows

    def data(self, query):
        selected = self.selected(query)
        if query.get("as_count") is True:
            if not query.get("next_study_date"):
                self.root_counts += 1
            return {"count": len(selected)}
        return {"records": deepcopy(selected[:1000])}

    def __call__(self, request):
        query = json.loads(request.content)
        self.calls.append((request, query))
        assert request.method == "POST"
        assert str(request.url) in {
            "https://open.maimemo.com/open/api/v1/memo/study/query_study_records",
            "https://open.maimemo.com/open/api/v1/study/query_study_records",
        }
        assert request.headers["Authorization"] == "Bearer " + TOKEN
        assert request.headers["Accept-Encoding"] == "identity"
        assert not request.url.query
        assert set(query) <= {"as_count", "limit", "next_study_date"}
        if query.get("as_count") is True:
            assert "limit" not in query
        else:
            assert query["limit"] == 1000
            assert "as_count" not in query
            assert any(previous.get("as_count") is True
                       and previous.get("next_study_date") == query.get("next_study_date")
                       for _, previous in self.calls[:-1]), "Each date bucket needs a count before its list"
        if "next_study_date" in query:
            bounds = query["next_study_date"]
            assert isinstance(bounds, dict) and bounds
            assert set(bounds) <= {"start", "end"}
            for value in bounds.values():
                date_key(value)
        return httpx.Response(200, json=self.payload(self.data(query)))


def fetch(provider):
    return memo.MaimemoClient(
        TOKEN, transport=httpx.MockTransport(provider), request_interval=0,
    ).fetch_records()


def assert_safe_error(error):
    message = str(error)
    assert error.status_code == 502
    assert message and any("\u4e00" <= char <= "\u9fff" for char in message)
    assert TOKEN not in message
    assert "provider-secret" not in message
    assert "https://" not in message


@pytest.mark.parametrize("envelope", [True, False])
@pytest.mark.parametrize("size", [0, 1, 1000])
def test_complete_small_and_empty_snapshots_verify_both_counts(envelope, size):
    provider = Provider(dataset(size), envelope=envelope)
    result = fetch(provider)
    assert result["complete"] is True
    assert result["warning"] is None
    assert result["remote_total"] == size
    assert {row["voc_id"] for row in result["records"]} == {row["voc_id"] for row in provider.rows}
    assert provider.root_counts == 2
    assert all(request.url.path.endswith(memo.PRIMARY_PATH) for request, _ in provider.calls)
    assert provider.calls[0][1] == provider.calls[-1][1] == {"as_count": True}


def test_4544_unordered_records_complete_with_inclusive_date_buckets():
    provider = Provider(dataset(4544))
    result = fetch(provider)
    assert result["complete"] is True, result["warning"]
    assert result["warning"] is None
    assert result["remote_total"] == 4544
    assert len(result["records"]) == 4544
    assert {r["voc_id"] for r in result["records"]} == {r["voc_id"] for r in provider.rows}
    assert len(provider.calls) <= memo.MAX_REQUESTS == 32
    bounds = [query["next_study_date"] for _, query in provider.calls if "next_study_date" in query]
    starts = {bound["start"] for bound in bounds if "start" in bound}
    ends = {bound["end"] for bound in bounds if "end" in bound}
    assert starts & ends, "The two date buckets must include their shared pivot date"
    assert provider.root_counts == 2


def test_inclusive_boundary_overlap_is_deduplicated_by_vocabulary_id():
    rows = dataset(1500)
    # Repeated spellings are still different provider vocabulary identities.
    for row in rows:
        row["voc_spelling"] = "shared"
    provider = Provider(rows)
    result = fetch(provider)
    assert result["complete"] is True, result["warning"]
    assert len(result["records"]) == len({row["voc_id"] for row in result["records"]}) == 1500
    assert all(row["voc_spelling"] == "shared" for row in result["records"])


def test_same_date_over_1000_stops_without_skipping_that_date():
    provider = Provider(record(i) for i in range(1001))
    result = fetch(provider)
    assert result["complete"] is False
    assert result["remote_total"] == 1001
    assert 0 < len(result["records"]) <= 1000
    assert result["warning"]
    assert len(provider.calls) <= memo.MAX_REQUESTS
    pivots = [value for _, query in provider.calls
              for value in query.get("next_study_date", {}).values()]
    assert all(date_key(value) == date_key(provider.rows[0]["next_study_date"]) for value in pivots)


def test_missing_next_date_is_conservative_even_when_unique_count_matches():
    provider = Provider([record(0, date=None)])
    result = fetch(provider)
    assert len(result["records"]) == result["remote_total"] == 1
    assert result["complete"] is False and result["warning"]


def test_provider_ignoring_date_filters_cannot_claim_a_complete_snapshot():
    class IgnoresFilters(Provider):
        def selected(self, query):
            return self.rows

    provider = IgnoresFilters(dataset(1500))
    result = fetch(provider)
    assert result["remote_total"] == 1500
    assert len(result["records"]) <= 1000
    assert result["complete"] is False and result["warning"]
    assert len(provider.calls) <= memo.MAX_REQUESTS


def test_changed_remote_total_is_partial():
    class ChangedCount(Provider):
        def data(self, query):
            data = super().data(query)
            if query.get("as_count") is True and not query.get("next_study_date") and self.root_counts > 1:
                data["count"] += 1
            return data

    result = fetch(ChangedCount([record()]))
    assert result["remote_total"] == 1
    assert len(result["records"]) == 1
    assert result["complete"] is False and result["warning"]


@pytest.mark.parametrize("delta", [-1, 1])
def test_stable_but_inconsistent_count_cannot_claim_completeness(delta):
    class WrongCount(Provider):
        def data(self, query):
            data = super().data(query)
            if "count" in data:
                data["count"] += delta
            return data

    result = fetch(WrongCount(dataset(2)))
    assert result["remote_total"] == 2 + delta
    assert result["complete"] is False and result["warning"]


def test_changed_overlapping_record_is_partial_even_if_counts_match():
    class ChangedRecord(Provider):
        list_calls = 0

        def data(self, query):
            data = super().data(query)
            if "records" in data:
                self.list_calls += 1
                if self.list_calls > 1:
                    for row in data["records"]:
                        if row["voc_id"] == self.rows[0]["voc_id"]:
                            row["study_count"] += 1
            return data

    result = fetch(ChangedRecord(dataset(1500)))
    assert result["remote_total"] == 1500
    assert 0 < len(result["records"]) <= 1500
    assert len(result["records"]) == len({row["voc_id"] for row in result["records"]})
    assert result["complete"] is False and result["warning"]


def test_provider_returned_dates_may_differ_from_filter_boundaries():
    class OffsetDates(Provider):
        def __init__(self, rows):
            super().__init__(rows)
            self.filter_dates = {r["voc_id"]: date_key(r["next_study_date"]) for r in self.rows}
            latest = max(self.rows, key=lambda r: self.filter_dates[r["voc_id"]])
            # The provider's filter date differs from the returned date for
            # this one stable vocabulary record. Its actual bucket is later.
            latest["next_study_date"] = "2020-01-01T00:00:00+08:00"
            self.offset_id = latest["voc_id"]

        def selected(self, query):
            bounds = query.get("next_study_date", {})
            return [row for row in self.rows
                    if ("start" not in bounds or self.filter_dates[row["voc_id"]] >= date_key(bounds["start"]))
                    and ("end" not in bounds or self.filter_dates[row["voc_id"]] <= date_key(bounds["end"]))]

    provider = OffsetDates(dataset(1500))
    result = fetch(provider)
    assert result["complete"] is True, result["warning"]
    assert len(result["records"]) == 1500
    assert next(r for r in result["records"] if r["voc_id"] == provider.offset_id)["next_study_date"].startswith("2020-")


def test_request_budget_returns_partial_and_keeps_http_count_bounded(monkeypatch):
    monkeypatch.setattr(memo, "MAX_REQUESTS", 5)
    provider = Provider(dataset(4544))
    result = fetch(provider)
    assert result["complete"] is False and result["warning"]
    assert len(provider.calls) <= 5
    assert result["remote_total"] == 4544


def test_record_budget_bounds_unique_records(monkeypatch):
    monkeypatch.setattr(memo, "MAX_RECORDS", 3)
    result = fetch(Provider(dataset(10)))
    assert len(result["records"]) <= 3
    assert result["remote_total"] == 10
    assert result["complete"] is False and result["warning"]


def test_total_fetch_deadline_returns_partial_without_extra_http(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(memo, "monotonic", lambda: clock[0])
    monkeypatch.setattr(memo, "MAX_FETCH_SECONDS", 15.0)
    provider = Provider([record()])

    def slow(request):
        response = provider(request)
        clock[0] += 10.0
        return response

    result = fetch(slow)
    assert result["remote_total"] == 1
    assert result["complete"] is False and result["warning"]
    assert len(provider.calls) == 2


def test_deadline_timeout_keeps_already_read_records_and_stops_http(monkeypatch):
    clock = [0.0]
    requests = []
    monkeypatch.setattr(memo, "monotonic", lambda: clock[0])
    provider = Provider(dataset(1500))

    def handle(request):
        requests.append(request)
        query = json.loads(request.content)
        if query.get("as_count") and query.get("next_study_date"):
            clock[0] = memo.MAX_FETCH_SECONDS
            raise httpx.ReadTimeout(TOKEN + " provider-secret")
        return provider(request)

    result = fetch(handle)
    assert result["remote_total"] == 1500
    assert len(result["records"]) == 1000
    assert result["complete"] is False and result["warning"]
    assert TOKEN not in result["warning"] and "provider-secret" not in result["warning"]
    assert len(requests) == 3


def test_trickled_body_checks_deadline_between_small_raw_chunks(monkeypatch):
    clock = [0.0]
    requests = []
    monkeypatch.setattr(memo, "monotonic", lambda: clock[0])

    class Trickle(httpx.SyncByteStream):
        read = 0
        closed = False

        def __iter__(self):
            for _ in range(1000):
                self.read += 1
                clock[0] += 1.0
                yield b" "

        def close(self):
            self.closed = True

    stream = Trickle()

    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, json={"count": 1})
        return httpx.Response(200, stream=stream)

    result = fetch(handle)
    assert result["remote_total"] == 1
    assert result["complete"] is False and result["warning"]
    assert stream.read <= memo.MAX_FETCH_SECONDS
    assert clock[0] <= memo.MAX_FETCH_SECONDS
    assert stream.closed
    assert len(requests) == 2


def test_only_404_uses_fallback_and_sticks_to_it_for_the_run():
    provider = Provider([record()])
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path.endswith(memo.PRIMARY_PATH):
            return httpx.Response(404, json={"error": TOKEN})
        return provider(request)

    result = fetch(handle)
    assert result["complete"] is True
    assert len([r for r in requests if r.url.path.endswith(memo.PRIMARY_PATH)]) == 1
    assert all(r.url.path.endswith(memo.FALLBACK_PATH) for r in requests[1:])
    assert json.loads(requests[0].content) == json.loads(requests[1].content)


@pytest.mark.parametrize("status", [301, 302, 307, 308, 400, 401, 403, 429, 500, 503])
def test_non_404_failures_do_not_fallback_or_follow_redirects(status):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(status, headers={"Location": "https://provider-secret.example/"},
                              json={"message": TOKEN + " provider-secret"})

    with pytest.raises(memo.MaimemoError) as error:
        fetch(handle)
    assert_safe_error(error.value)
    assert len(requests) == 1
    assert requests[0].url.path.endswith(memo.PRIMARY_PATH)


def test_404_on_both_fixed_paths_stops_after_one_fallback():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(404, json={"message": TOKEN + " provider-secret"})

    with pytest.raises(memo.MaimemoError) as error:
        fetch(handle)
    assert_safe_error(error.value)
    assert len(requests) == 2
    assert requests[0].url.path.endswith(memo.PRIMARY_PATH)
    assert requests[1].url.path.endswith(memo.FALLBACK_PATH)


@pytest.mark.parametrize("payload", [
    [], None, "provider-secret", {"data": []}, {"success": False, "message": TOKEN},
    {"success": 1, "data": {"count": 0}}, {"errors": [{"detail": TOKEN}]},
    {"count": True}, {"count": -1}, {"count": "1"}, {"count": 1.5}, {},
])
def test_malformed_envelopes_and_counts_fail_safely(payload):
    with pytest.raises(memo.MaimemoError) as error:
        fetch(lambda _: httpx.Response(200, json=payload))
    assert_safe_error(error.value)


@pytest.mark.parametrize("page", [None, {}, "provider-secret", [None], [record()] * 1001])
def test_malformed_record_lists_fail_safely(page):
    def handle(request):
        query = json.loads(request.content)
        return httpx.Response(200, json={"count": 1} if query.get("as_count") else {"records": page})

    with pytest.raises(memo.MaimemoError) as error:
        fetch(handle)
    assert_safe_error(error.value)


@pytest.mark.parametrize("changes", [
    {"voc_id": None}, {"voc_id": ""}, {"voc_id": "x" * 129}, {"voc_id": "a\nb"},
    {"voc_spelling": None}, {"voc_spelling": " "}, {"voc_spelling": "x" * 201},
    {"study_count": None}, {"study_count": True}, {"study_count": -1}, {"study_count": "1"},
    {"add_date": None}, {"add_date": "not-a-date"}, {"add_date": "2026-02-30"},
    {"next_study_date": "2026-13-01"}, {"last_study_date": 123},
    {"last_response": "UNKNOWN"}, {"last_response": []},
    {"tags": None}, {"tags": ["UNKNOWN"]}, {"tags": [1]}, {"tags": {"STICKING": True}},
])
def test_invalid_required_fields_and_typed_signals_are_rejected(changes):
    with pytest.raises(memo.MaimemoError) as error:
        fetch(Provider([record(**changes)]))
    assert_safe_error(error.value)


@pytest.mark.parametrize("tags,expected", [
    ([], []), ("STICKING", ["STICKING"]), ("WELL_FAMILIAR", ["WELL_FAMILIAR"]),
    (["WELL_FAMILIAR", "STICKING", "WELL_FAMILIAR"], ["WELL_FAMILIAR", "STICKING"]),
])
def test_tags_are_normalized_and_only_whitelisted_fields_leave_client(tags, expected):
    row = record(tags=tags, provider_secret=TOKEN, email="provider-secret@example.invalid",
                 voc_spelling="  Example  ")
    result = fetch(Provider([row]))
    assert result["complete"] is True
    assert len(result["records"]) == 1
    actual = result["records"][0]
    assert set(actual) == FIELDS
    assert actual["tags"] == expected
    assert actual["voc_spelling"] == "Example"
    assert TOKEN not in json.dumps(result)


def test_exact_provider_no_feedback_sentinel_is_normalized_to_none():
    result = fetch(Provider([record(last_response="STUDY_RESPONSE_UNSPECIFIED")]))
    assert result["complete"] is True
    assert result["records"][0]["last_response"] is None


@pytest.mark.parametrize("value", ["UNSPECIFIED", "STUDY_RESPONSE_OTHER", "STUDY_RESPONSE_UNSPECIFIED "])
def test_other_unknown_response_values_are_still_rejected(value):
    with pytest.raises(memo.MaimemoError) as error:
        fetch(Provider([record(last_response=value)]))
    assert_safe_error(error.value)


@pytest.mark.parametrize("field", ["add_date", "first_study_date", "last_study_date", "next_study_date"])
@pytest.mark.parametrize("source,expected", [
    ("2026-05-19", "2026-05-19T00:00:00+08:00"),
    ("2026-05-19T09:30", "2026-05-19T09:30:00+08:00"),
    ("2026-05-19T09:30:45", "2026-05-19T09:30:45+08:00"),
    ("2026-05-19T09:30:45.12", "2026-05-19T09:30:45.120000+08:00"),
    ("2026-05-19T09:30:45Z", "2026-05-19T09:30:45+00:00"),
    ("2026-05-19T09:30:45+08:00", "2026-05-19T09:30:45+08:00"),
    ("2026-05-19T09:30:45-05:00", "2026-05-19T09:30:45-05:00"),
])
def test_all_date_fields_return_explicit_iso_offsets(field, source, expected):
    result = fetch(Provider([record(**{field: source})]))
    assert result["complete"] is True, result["warning"]
    assert result["records"][0][field] == expected


@pytest.mark.parametrize("field", ["first_study_date", "last_study_date", "next_study_date"])
def test_optional_date_fields_preserve_none(field):
    result = fetch(Provider([record(**{field: None})]))
    assert result["records"][0][field] is None
    if field == "next_study_date":
        assert result["complete"] is False and result["warning"]
    else:
        assert result["complete"] is True and result["warning"] is None


def test_naive_dates_can_be_used_for_complete_inclusive_bucket_reads():
    rows = dataset(1500)
    for row in rows:
        for field in ("add_date", "first_study_date", "last_study_date", "next_study_date"):
            if row[field] is not None:
                row[field] = row[field].removesuffix("+08:00")
    result = fetch(Provider(rows))
    assert result["complete"] is True, result["warning"]
    assert len(result["records"]) == 1500
    for row in result["records"]:
        for field in ("add_date", "first_study_date", "last_study_date", "next_study_date"):
            assert row[field] is None or row[field].endswith("+08:00")


class Chunks(httpx.SyncByteStream):
    def __init__(self, count, size=65536):
        self.count = count
        self.size = size
        self.read = 0
        self.closed = False

    def __iter__(self):
        for _ in range(self.count):
            self.read += 1
            yield b" " * self.size

    def close(self):
        self.closed = True


def test_chunked_body_is_stopped_at_the_eight_mb_limit():
    assert memo.MAX_RESPONSE_BYTES == 8 * 1024 * 1024
    stream = Chunks(1000)
    with pytest.raises(memo.MaimemoError) as error:
        fetch(lambda _: httpx.Response(200, stream=stream))
    assert_safe_error(error.value)
    assert stream.read <= memo.MAX_RESPONSE_BYTES // stream.size + 1
    assert stream.closed


@pytest.mark.parametrize("length", [str(8 * 1024 * 1024 + 1), "-1", "invalid", "9" * 30])
def test_unsafe_content_length_is_rejected_before_reading_body(length):
    stream = Chunks(1000)
    with pytest.raises(memo.MaimemoError) as error:
        fetch(lambda _: httpx.Response(200, headers={"Content-Length": length}, stream=stream))
    assert_safe_error(error.value)
    assert stream.read == 0 and stream.closed


def test_compressed_response_is_rejected_before_decompression():
    stream = Chunks(1000)
    with pytest.raises(memo.MaimemoError) as error:
        fetch(lambda _: httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=stream))
    assert_safe_error(error.value)
    assert stream.read == 0 and stream.closed


@pytest.mark.parametrize("failure", [
    httpx.ConnectError(TOKEN + " https://provider-secret.invalid/"),
    httpx.ReadTimeout(TOKEN + " provider-secret"),
    ValueError(TOKEN + " provider-secret"),
])
def test_transport_exceptions_never_echo_provider_or_credentials(failure):
    def handle(_):
        raise failure

    with pytest.raises(memo.MaimemoError) as error:
        fetch(handle)
    assert_safe_error(error.value)
    assert error.value.__cause__ is None


def test_invalid_json_never_echoes_response_body():
    with pytest.raises(memo.MaimemoError) as error:
        fetch(lambda _: httpx.Response(200, content=(TOKEN + " provider-secret").encode()))
    assert_safe_error(error.value)


def test_timeout_is_fifteen_seconds_for_each_http_phase():
    provider = Provider([record()])
    result = fetch(provider)
    assert result["complete"] is True
    for request, _ in provider.calls:
        assert request.extensions["timeout"] == {phase: 15.0 for phase in ("connect", "read", "write", "pool")}


def test_throttle_is_shared_across_clients_and_includes_fallback(monkeypatch):
    clock = [10.0]
    sleeps = []
    requested_at = []
    monkeypatch.setattr(memo, "monotonic", lambda: clock[0])

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(memo, "sleep", sleep)
    provider = Provider([record()])

    def handle(request):
        requested_at.append(clock[0])
        if len(requested_at) == 1:
            return httpx.Response(404)
        return provider(request)

    for _ in range(2):
        result = memo.MaimemoClient(TOKEN, transport=httpx.MockTransport(handle), request_interval=1.6).fetch_records()
        assert result["complete"] is True
    assert len(requested_at) >= 5
    assert len(sleeps) == len(requested_at) - 1
    assert all(later - earlier >= 1.6 - 1e-9 for earlier, later in zip(requested_at, requested_at[1:]))


@pytest.mark.parametrize("token", [None, "", "  ", "a\nb", "non-ascii-凭证", "x" * 4097])
def test_invalid_credential_is_rejected_locally(token):
    with pytest.raises(memo.MaimemoError) as error:
        memo.MaimemoClient(token, transport=httpx.MockTransport(lambda _: pytest.fail("invalid token reached HTTP")))
    assert_safe_error(error.value)


@pytest.mark.parametrize("interval", [True, -1, float("inf"), float("nan"), "1"])
def test_invalid_throttle_interval_is_rejected_locally(interval):
    with pytest.raises(memo.MaimemoError) as error:
        memo.MaimemoClient(TOKEN, request_interval=interval)
    assert_safe_error(error.value)
