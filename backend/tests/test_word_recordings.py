"""Public word fixtures, mocked HTTP and temporary caches; never access live APIs."""
from array import array
from io import BytesIO
import json
from types import SimpleNamespace
import wave

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest

from backend.api import pronunciation_routes as routes
from backend.auth import get_current_user_id
from backend.services import pronunciation as speech
from backend.services import word_recordings as recordings


WIKITEXT = """==English==
===Pronunciation===
* {{audio|en|LL-Q1860 (eng)-Reader-curious.wav|a=Southern England|IPA=/example/}}
* {{audio|en|En-us-curious.ogg|a=GA|IPA=/example/}}
==French==
* {{audio|en|En-us-not-English.ogg}}
"""
AUTHOR = '<a href="https://commons.wikimedia.org/wiki/User:Reader">A Reader</a>'
SOURCE = "https://commons.wikimedia.org/wiki/File:En-us-curious.ogg"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/3.0/"


def metadata(**overrides):
    info = {"url": "https://upload.wikimedia.org/wikipedia/commons/8/8f/En-us-curious.ogg?utm_source=wiktionary",
            "descriptionurl": SOURCE,
            "extmetadata": {"Artist": {"value": AUTHOR}, "LicenseShortName": {"value": "CC BY-SA 3.0"},
                            "LicenseUrl": {"value": "http://creativecommons.org/licenses/by-sa/3.0/"}}}
    info.update(overrides)
    return {"query": {"pages": [{"title": "File:En-us-curious.ogg", "imageinfo": [info]}]}}


@pytest.fixture(autouse=True)
def clear_negative():
    recordings._negative.clear()
    yield
    recordings._negative.clear()


@pytest.fixture
def runtime(monkeypatch):
    requests = []
    responses = {"wikitext": WIKITEXT, "metadata": metadata(), "audio": b"fake ogg recording"}
    decoded_pcm = array("h", [14000, -9000, 0, 3000, -12000])
    decoder = SimpleNamespace(SampleFormat=SimpleNamespace(SIGNED16="signed16"))
    def stream_memory(data, **options):
        assert data == responses["audio"]
        assert options == {"output_format": "signed16", "nchannels": 1, "sample_rate": 22_050, "frames_to_read": 4096}
        def generate():
            requested = yield array("h")
            cursor = 0
            while cursor < len(decoded_pcm):
                segment = decoded_pcm[cursor:cursor + requested]
                cursor += len(segment)
                requested = yield segment
        stream = generate()
        next(stream)
        return stream
    decoder.stream_memory = stream_memory
    monkeypatch.setattr(recordings, "import_module", lambda name: decoder if name == "miniaudio" else pytest.fail(name))
    def handle(request):
        requests.append(request)
        if str(request.url).startswith(recordings.WIKTIONARY_API):
            return httpx.Response(200, json={"parse": {"wikitext": responses["wikitext"]}})
        if str(request.url).startswith(recordings.COMMONS_API):
            return httpx.Response(200, json=responses["metadata"])
        assert request.url.host == "upload.wikimedia.org"
        return httpx.Response(200, content=responses["audio"])
    monkeypatch.setattr(recordings, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=False))
    return requests, responses, decoder, decoded_pcm


def pcm(data):
    with wave.open(BytesIO(data), "rb") as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 22_050)
        return audio.readframes(audio.getnframes())


def test_requested_word_uses_three_fixed_requests_and_attributed_recording(runtime, tmp_path):
    requests, _, _, original = runtime
    result = recordings.record_word("curious", "en-US", tmp_path)
    assert result is not None
    assert result.attribution == {"author": "A Reader", "license": "CC BY-SA 3.0", "source_url": SOURCE, "license_url": LICENSE_URL}
    assert [request.url.host for request in requests] == ["en.wiktionary.org", "commons.wikimedia.org", "upload.wikimedia.org"]
    assert requests[0].url.params["page"] == "curious"
    assert requests[1].url.params["titles"] == "File:En-us-curious.ogg"
    assert requests[2].url.query == b""
    assert all(request.url.scheme == "https" for request in requests)
    assert pcm(result.audio) == bytes(recordings.LEADING_FRAMES * 2) + original.tobytes() + bytes(recordings.TRAILING_FRAMES * 2)
    assert recordings._valid_audio(result.audio)


def test_southern_england_is_selected_only_for_british_accent(runtime, tmp_path):
    requests, _, _, _ = runtime
    result = recordings.record_word("curious", "en-GB", tmp_path)
    assert result is not None
    assert requests[1].url.params["titles"] == "File:LL-Q1860 (eng)-Reader-curious.wav"


@pytest.mark.parametrize("text", ["a curious reader", "hello there.", "中文", "x/../curious", "https://example.com", "a" * 81, "hello?x=1", "word... word", "hello\x00"])
def test_nonwords_and_untrusted_inputs_never_access_network(runtime, tmp_path, text):
    requests, _, _, _ = runtime
    assert recordings.record_word(text, "en-US", tmp_path) is None
    assert requests == [] and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("text,expected", [("Curious.", "Curious"), ("well-being", "well-being"), ("don't", "don't"), ("don’t", "don't"), ("readers’", "readers'")])
def test_word_punctuation_and_typographic_apostrophe_are_normalized(runtime, tmp_path, text, expected):
    requests, _, _, _ = runtime
    assert recordings.record_word(text, "en-US", tmp_path) is not None
    assert requests[0].url.params["page"] == expected


@pytest.mark.parametrize("text", [
    "==French==\n{{audio|en|En-us-curious.ogg}}\n",
    "==English==\n{{audio|fr|En-us-curious.ogg}}\n",
    "==English==\n{{audio|en|Curious.ogg}}\n",
    "==English==\n{{audio|en|En-uk-curious.ogg|a=UK}}\n",
    "==English==\n{{audio|en|En-us-curious.ogg|a=RP}}\n",
    "==English==\n{{audio|en|../En-us-curious.ogg|a=GA}}\n",
    "==English==\n{{audio|en|En-us-curious.exe|a=GA}}\n",
])
def test_other_languages_unknown_or_wrong_accents_and_bad_filenames_do_not_download(runtime, tmp_path, text):
    requests, responses, _, _ = runtime
    responses["wikitext"] = text
    assert recordings.record_word("curious", "en-US", tmp_path) is None
    assert len(requests) == 1
    assert not (tmp_path / "dictionary-audio").exists()


@pytest.mark.parametrize("name,url", [
    ("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/"),
    ("CC BY-SA 3.0", "http://creativecommons.org/licenses/by-sa/3.0/"),
    ("CC0", "https://creativecommons.org/publicdomain/zero/1.0/"),
    ("Public domain", "https://creativecommons.org/publicdomain/mark/1.0/"),
])
def test_only_explicit_compatible_open_licenses_are_accepted(runtime, tmp_path, name, url):
    _, responses, _, _ = runtime
    responses["metadata"]["query"]["pages"][0]["imageinfo"][0]["extmetadata"].update({
        "LicenseShortName": {"value": name}, "LicenseUrl": {"value": url}})
    result = recordings.record_word("curious", "en-US", tmp_path)
    assert result is not None and result.attribution["license"] == name
    assert result.attribution["license_url"].startswith("https://creativecommons.org/")


@pytest.mark.parametrize("name,url", [
    ("All rights reserved", LICENSE_URL), ("CC BY-NC 4.0", "https://creativecommons.org/licenses/by-nc/4.0/"),
    ("CC BY-SA 3.0", "https://evil.example/licenses/by-sa/3.0/"),
    ("CC BY-SA 3.0", "https://creativecommons.org/licenses/by/3.0/"),
    ("CC BY-SA 3.0", "javascript:alert(1)"), ("CC BY-SA 3.0", ""),
    ("Public domain", "https://creativecommons.org/licenses/by/4.0/"),
])
def test_missing_restricted_or_mismatched_licenses_never_download_audio(runtime, tmp_path, name, url):
    requests, responses, _, _ = runtime
    responses["metadata"]["query"]["pages"][0]["imageinfo"][0]["extmetadata"].update({
        "LicenseShortName": {"value": name}, "LicenseUrl": {"value": url}})
    assert recordings.record_word("curious", "en-US", tmp_path) is None
    assert len(requests) == 2


@pytest.mark.parametrize("field,value", [
    ("url", "https://127.0.0.1/audio.ogg"), ("url", "https://upload.wikimedia.org.evil.example/wikipedia/commons/x.ogg"),
    ("url", "https://user:pass@upload.wikimedia.org/wikipedia/commons/x.ogg"),
    ("url", "https://upload.wikimedia.org:444/wikipedia/commons/x.ogg"),
    ("url", "https://upload.wikimedia.org/wikipedia/commons/%2e%2e/audio.ogg"),
    ("url", "https://upload.wikimedia.org/wikipedia/commons/x\x00.ogg"),
    ("url", "https://upload.wikimedia.org/wikipedia/commons/x\x7f.ogg"),
    ("url", "https://upload.wikimedia.org/wikipedia/commons/x%00.ogg"),
    ("descriptionurl", "https://commons.wikimedia.org/wiki/User:Someone"),
    ("descriptionurl", "https://commons.wikimedia.org/wiki/File:test?next=https://evil.example"),
])
def test_remote_metadata_cannot_redirect_audio_or_attribution_to_arbitrary_targets(runtime, tmp_path, field, value):
    requests, responses, _, _ = runtime
    responses["metadata"] = metadata(**{field: value})
    assert recordings.record_word("curious", "en-US", tmp_path) is None
    assert len(requests) == 2


def test_author_html_is_plain_text_and_missing_author_is_rejected(runtime, tmp_path):
    requests, responses, _, _ = runtime
    responses["metadata"]["query"]["pages"][0]["imageinfo"][0]["extmetadata"]["Artist"]["value"] = '<script>bad()</script><a href="javascript:x">A Reader</a>\r\n'
    result = recordings.record_word("curious", "en-US", tmp_path)
    assert result is not None and result.attribution["author"] == "A Reader"
    responses["metadata"]["query"]["pages"][0]["imageinfo"][0]["extmetadata"]["Artist"]["value"] = ""
    assert recordings.record_word("hello", "en-US", tmp_path) is None
    assert len(requests) == 5


def test_successful_cache_reuses_audio_and_license_without_any_network(runtime, tmp_path, monkeypatch):
    requests, _, _, _ = runtime
    initial = recordings.record_word("curious", "en-US", tmp_path)
    assert initial is not None
    assert len(list((tmp_path / "dictionary-audio").glob("*.json"))) == 1
    assert not list((tmp_path / "dictionary-audio").glob("*.part"))
    monkeypatch.setattr(recordings, "_client", lambda: pytest.fail("cache hit used network"))
    assert recordings.record_word("curious", "en-US", tmp_path) == initial
    assert len(requests) == 3


@pytest.mark.parametrize("damage", ["expired", "future", "version", "word", "accent", "audio", "license", "json", "oversized", "deep"])
def test_invalid_cache_is_never_used_and_success_is_regenerated(runtime, tmp_path, damage):
    requests, _, _, _ = runtime
    initial = recordings.record_word("curious", "en-US", tmp_path)
    path = next((tmp_path / "dictionary-audio").glob("*.json"))
    payload = json.loads(path.read_text("utf8"))
    if damage == "expired": payload["created_at"] -= recordings.CACHE_TTL_SECONDS + 1
    elif damage == "future": payload["created_at"] += 1000
    elif damage in {"version", "word", "accent"}: payload[damage] = "wrong"
    elif damage == "audio": payload["audio"] = "not a base64 WAV"
    elif damage == "license": payload["attribution"]["license_url"] = "https://evil.example/"
    if damage == "json": path.write_text("{broken", "utf8")
    elif damage == "oversized": path.write_bytes(b" " * (recordings.MAX_CACHE_ENTRY_BYTES + 1))
    elif damage == "deep": path.write_text("[" * 2000 + "0" + "]" * 2000, "utf8")
    else: path.write_text(json.dumps(payload), "utf8")
    assert recordings.record_word("curious", "en-US", tmp_path) == initial
    assert len(requests) == 6


def test_unavailable_dictionary_has_short_negative_cache_and_no_success_cache(runtime, tmp_path):
    requests, responses, _, _ = runtime
    responses["wikitext"] = "==English==\nNo recording.\n"
    assert recordings.record_word("curious", "en-US", tmp_path) is None
    assert recordings.record_word("curious", "en-US", tmp_path) is None
    assert len(requests) == 1 and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("status", [301, 302, 403, 429, 500])
def test_redirects_and_http_errors_fall_back_without_following(runtime, tmp_path, monkeypatch, status):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(status, headers={"Location": "https://evil.example/private"})
    monkeypatch.setattr(recordings, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False))
    assert recordings.record_word("curious", "en-US", tmp_path) is None
    assert len(requests) == 1 and list(tmp_path.iterdir()) == []


def test_response_size_limit_is_enforced_before_json_or_audio_decode(runtime, tmp_path, monkeypatch):
    monkeypatch.setattr(recordings, "MAX_BODY_BYTES", 100)
    assert recordings.record_word("curious", "en-US", tmp_path) is None
    assert list(tmp_path.iterdir()) == []


def test_elapsed_total_budget_prevents_more_requests(runtime, tmp_path, monkeypatch):
    requests, _, _, _ = runtime
    expired = recordings.LOOKUP_SECONDS + 1
    clock = iter([0, 0, 0, expired])
    monkeypatch.setattr(recordings, "monotonic", lambda: next(clock, expired))
    assert recordings.record_word("curious", "en-US", tmp_path) is None
    assert len(requests) <= 1 and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("failure", ["error", "too_long", "empty", "wrong_type", "oversized_chunk"])
def test_invalid_decoded_audio_never_creates_success_cache(runtime, tmp_path, failure):
    _, _, decoder, original = runtime
    def stream_memory(*args, **kwargs):
        if failure == "error": raise RuntimeError("decoder error")
        def generate():
            requested = yield array("h")
            if failure == "empty":
                return
            while True:
                if failure == "wrong_type": segment = array("f", [1])
                elif failure == "oversized_chunk": segment = array("h", [1]) * (requested + 1)
                else: segment = array("h", [1]) * requested
                requested = yield segment
        stream = generate()
        next(stream)
        return stream
    decoder.stream_memory = stream_memory
    assert recordings.record_word("curious", "en-US", tmp_path) is None
    assert list(tmp_path.iterdir()) == []


def test_streaming_decoder_reads_at_most_twelve_seconds_plus_one_detection_frame(runtime):
    _, responses, decoder, _ = runtime
    requested_frames, closed = [], []
    def stream_memory(*args, **kwargs):
        def generate():
            try:
                requested = yield array("h")
                while True:
                    requested_frames.append(requested)
                    requested = yield array("h", [1]) * requested
            finally:
                closed.append(True)
        stream = generate()
        next(stream)
        return stream
    decoder.stream_memory = stream_memory
    with pytest.raises(ValueError):
        recordings._decode_audio(responses["audio"])
    assert sum(requested_frames) == 12 * 22_050 + 1
    assert max(requested_frames) <= 4096 and closed == [True]


def test_real_miniaudio_decode_error_is_a_safe_dictionary_miss(runtime, tmp_path, monkeypatch):
    decoder = pytest.importorskip("miniaudio")
    monkeypatch.setattr(recordings, "import_module", lambda name: decoder)
    assert recordings.record_word("curious", "en-US", tmp_path) is None
    assert list(tmp_path.iterdir()) == []


def test_invalid_url_and_deep_remote_json_fail_without_success_cache(runtime, tmp_path, monkeypatch):
    def invalid_url(request):
        raise httpx.InvalidURL("malformed remote URL")
    monkeypatch.setattr(recordings, "_client", lambda: httpx.Client(transport=httpx.MockTransport(invalid_url)))
    assert recordings.record_word("curious", "en-US", tmp_path) is None
    def deep_json(request):
        return httpx.Response(200, content=("[" * 2000 + "0" + "]" * 2000).encode())
    monkeypatch.setattr(recordings, "_client", lambda: httpx.Client(transport=httpx.MockTransport(deep_json)))
    assert recordings.record_word("hello", "en-US", tmp_path) is None
    assert list(tmp_path.iterdir()) == []


def test_cache_cap_discards_old_entries_but_preserves_unrelated_files(runtime, tmp_path, monkeypatch):
    monkeypatch.setattr(recordings, "MAX_CACHE_BYTES", 50_000)
    recordings.record_word("curious", "en-US", tmp_path)
    directory = tmp_path / "dictionary-audio"
    first = next(directory.glob("*.json"))
    import os
    os.utime(first, (100, 100))
    keep = directory / "keep.txt"
    keep.write_text("keep")
    recordings.record_word("hello", "en-US", tmp_path)
    assert not first.exists()
    assert len(list(directory.glob("*.json"))) == 1 and keep.read_text() == "keep"


def test_route_identifies_recording_and_preserves_required_attribution(runtime, tmp_path, monkeypatch):
    monkeypatch.setattr(speech, "get_settings", lambda: SimpleNamespace(storage_dir=tmp_path))
    monkeypatch.setattr(speech, "synthesize", lambda *args: pytest.fail("recording success used Piper"))
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user_id] = lambda: 1
    with TestClient(app) as client:
        response = client.post("/api/pronunciation", json={"text": "curious", "accent": "en-US"})
    assert response.status_code == 200 and response.headers["content-type"] == "audio/wav"
    assert response.headers["X-Pronunciation-Source"] == "dictionary"
    assert response.headers["X-Pronunciation-Voice"] == "wiktionary-en-US"
    assert json.loads(response.headers["X-Pronunciation-Attribution"])["source_url"] == SOURCE
    assert response.headers["Cache-Control"] == "private, no-store"
    assert recordings._valid_audio(response.content)


def test_sentence_route_is_piper_only_and_dictionary_miss_falls_back_explicitly(runtime, tmp_path, monkeypatch):
    requests, responses, _, _ = runtime
    monkeypatch.setattr(speech, "get_settings", lambda: SimpleNamespace(storage_dir=tmp_path))
    piper_calls = []
    monkeypatch.setattr(speech, "synthesize", lambda text, accent: (piper_calls.append((text, accent)) or b"piper wave", "en_US-ljspeech-medium"))
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user_id] = lambda: 1
    with TestClient(app) as client:
        response = client.post("/api/pronunciation", json={"text": "A curious reader.", "accent": "en-US"})
        assert response.status_code == 200 and response.headers["X-Pronunciation-Source"] == "piper"
        assert response.headers["X-Pronunciation-Attribution"] == "" and requests == []
        responses["wikitext"] = "==English==\nNo recording.\n"
        fallback = client.post("/api/pronunciation", json={"text": "curious", "accent": "en-US"})
    assert fallback.status_code == 200 and fallback.headers["X-Pronunciation-Source"] == "piper"
    assert fallback.headers["X-Pronunciation-Attribution"] == ""
    assert piper_calls == [("A curious reader.", "en-US"), ("curious", "en-US")]
    assert len(requests) == 1
