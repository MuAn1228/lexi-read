"""Public word fixtures, mocked HTTP and temporary caches; never access live APIs."""
from array import array
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO
import json
from threading import Barrier, Event
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
def clear_lookup_state():
    recordings.close_recording_client()
    recordings._negative.clear()
    recordings._word_choices.clear()
    recordings._inflight.clear()
    recordings._word_inflight.clear()
    yield
    recordings.close_recording_client()
    recordings._negative.clear()
    recordings._word_choices.clear()
    recordings._inflight.clear()
    recordings._word_inflight.clear()


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
            payload = deepcopy(responses["metadata"])
            if responses.get("align_metadata_title", True):
                payload["query"]["pages"][0]["title"] = request.url.params["titles"].split("|")[0]
            return httpx.Response(200, json=payload)
        assert request.url.host == "upload.wikimedia.org"
        return httpx.Response(200, content=responses.get("audio_by_path", {}).get(request.url.path, responses["audio"]))
    monkeypatch.setattr(recordings, "_new_client", lambda: httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=False))
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


@pytest.mark.parametrize("name,url,canonical", [
    ("CC BY 3.0 us", "https://creativecommons.org/licenses/by/3.0/us/deed.en", "https://creativecommons.org/licenses/by/3.0/us/"),
    ("CC BY-SA 3.0 US", "http://creativecommons.org/licenses/by-sa/3.0/us/", "https://creativecommons.org/licenses/by-sa/3.0/us/"),
    ("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/deed.en", "https://creativecommons.org/licenses/by/4.0/"),
])
def test_ported_and_translated_cc_deeds_keep_the_exact_license_and_jurisdiction(runtime, tmp_path, name, url, canonical):
    requests, responses, _, _ = runtime
    responses["metadata"]["query"]["pages"][0]["imageinfo"][0]["extmetadata"].update({
        "LicenseShortName": {"value": name}, "LicenseUrl": {"value": url}})
    result = recordings.record_word("curious", "en-US", tmp_path)
    assert result is not None
    assert result.attribution["license"] == name and result.attribution["license_url"] == canonical
    assert recordings.record_word("curious", "en-US", tmp_path) == result
    assert len(requests) == 3


@pytest.mark.parametrize("name,url", [
    ("CC BY 3.0 us", "https://creativecommons.org/licenses/by/3.0/"),
    ("CC BY 3.0 us", "https://creativecommons.org/licenses/by/3.0/uk/deed.en"),
    ("CC BY 3.0 us", "https://creativecommons.org/licenses/by/4.0/us/deed.en"),
    ("CC BY 3.0 us", "https://creativecommons.org/licenses/by-sa/3.0/us/deed.en"),
    ("CC BY 3.0", "https://creativecommons.org/licenses/by/3.0/us/deed.en"),
    ("CC BY 3.0 us", "https://creativecommons.org/licenses/by/3.0/usdeed.en"),
    ("CC BY 3.0 us", "https://creativecommons.org/licenses/by/3.0/us/deed.en/elsewhere"),
])
def test_ported_license_name_must_agree_with_url_version_type_and_jurisdiction(runtime, tmp_path, name, url):
    requests, responses, _, _ = runtime
    responses["metadata"]["query"]["pages"][0]["imageinfo"][0]["extmetadata"].update({
        "LicenseShortName": {"value": name}, "LicenseUrl": {"value": url}})
    assert recordings.record_word("curious", "en-US", tmp_path) is None
    assert len(requests) == 2 and list(tmp_path.iterdir()) == []


def public_domain_metadata():
    payload = metadata()
    payload["query"]["pages"][0]["imageinfo"][0]["extmetadata"].update({
        "LicenseShortName": {"value": "Public domain"}, "LicenseUrl": {"value": ""},
        "License": {"value": "pd"}, "Copyrighted": {"value": "False"},
        "AttributionRequired": {"value": "false"}, "Restrictions": {"value": ""}})
    return payload


def test_confirmed_public_domain_without_url_uses_mark_without_relabeling_cc0(runtime, tmp_path):
    requests, responses, _, _ = runtime
    responses["metadata"] = public_domain_metadata()
    result = recordings.record_word("curious", "en-US", tmp_path)
    assert result is not None
    assert result.attribution == {"author": "A Reader", "license": "Public domain", "source_url": SOURCE,
                                 "license_url": "https://creativecommons.org/publicdomain/mark/1.0/"}
    assert recordings.record_word("curious", "en-US", tmp_path) == result
    assert len(requests) == 3


@pytest.mark.parametrize("field,value", [
    ("LicenseShortName", "CC0"), ("LicenseShortName", "PD"), ("License", "cc-by"),
    ("Copyrighted", "True"), ("Copyrighted", ""), ("AttributionRequired", "true"),
    ("AttributionRequired", ""), ("Restrictions", "non-commercial only"),
    ("LicenseUrl", "https://evil.example/"), ("Artist", ""),
])
def test_missing_public_domain_license_url_requires_all_corroborating_fields(runtime, tmp_path, field, value):
    requests, responses, _, _ = runtime
    responses["metadata"] = public_domain_metadata()
    responses["metadata"]["query"]["pages"][0]["imageinfo"][0]["extmetadata"][field] = {"value": value}
    assert recordings.record_word("curious", "en-US", tmp_path) is None
    assert len(requests) == 2 and list(tmp_path.iterdir()) == []


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
    # A corrupt audio cache still revalidates license metadata and downloads the
    # audio, while retaining the short-lived, already validated filename lookup.
    assert len(requests) == 5


def test_switching_accent_reuses_only_requested_words_filename_lookup(runtime, tmp_path):
    requests, _, _, _ = runtime
    assert recordings.record_word("curious", "en-US", tmp_path) is not None
    assert recordings.record_word("curious", "en-GB", tmp_path) is not None
    assert len(requests) == 5
    assert sum(request.url.host == "en.wiktionary.org" for request in requests) == 1
    assert [request.url.params["titles"] for request in requests if request.url.host == "commons.wikimedia.org"] == [
        "File:En-us-curious.ogg", "File:LL-Q1860 (eng)-Reader-curious.wav"]
    choices = next(iter(recordings._word_choices.values()))[1]
    assert choices == {"en-US": ("En-us-curious.ogg",), "en-GB": ("LL-Q1860 (eng)-Reader-curious.wav",)}


def candidate_metadata(*filenames):
    pages = []
    for filename in filenames:
        page = metadata()["query"]["pages"][0]
        page["title"] = f"File:{filename}"
        page["imageinfo"][0].update({
            "url": f"https://upload.wikimedia.org/wikipedia/commons/a/ab/{filename}",
            "descriptionurl": f"https://commons.wikimedia.org/wiki/File:{filename}"})
        pages.append(page)
    return {"query": {"pages": pages}}


def configure_candidates(responses, *filenames):
    responses["wikitext"] = "==English==\n" + "\n".join(f"{{{{audio|en|{name}}}}}" for name in filenames)
    responses["metadata"] = candidate_metadata(*filenames)
    responses["align_metadata_title"] = False


def test_later_same_accent_recording_is_used_when_first_has_ineligible_license(runtime, tmp_path):
    requests, responses, _, _ = runtime
    configure_candidates(responses, "En-us-curious.ogg", "En-us-curious-2.ogg")
    responses["metadata"]["query"]["pages"][0]["imageinfo"][0]["extmetadata"]["LicenseShortName"]["value"] = "All rights reserved"
    result = recordings.record_word("curious", "en-US", tmp_path)
    assert result is not None and result.attribution["source_url"].endswith("En-us-curious-2.ogg")
    assert requests[1].url.params["titles"] == "File:En-us-curious.ogg|File:En-us-curious-2.ogg"
    assert len(requests) == 3 and requests[-1].url.path.endswith("En-us-curious-2.ogg")


def test_later_same_accent_recording_is_used_when_first_audio_is_corrupt(runtime, tmp_path):
    requests, responses, _, _ = runtime
    configure_candidates(responses, "En-us-curious.ogg", "En-us-curious-2.ogg")
    responses["audio_by_path"] = {"/wikipedia/commons/a/ab/En-us-curious.ogg": b"invalid audio"}
    result = recordings.record_word("curious", "en-US", tmp_path)
    assert result is not None and result.attribution["source_url"].endswith("En-us-curious-2.ogg")
    assert len(requests) == 4
    assert [request.url.path.rsplit("/", 1)[-1] for request in requests[2:]] == ["En-us-curious.ogg", "En-us-curious-2.ogg"]


def test_batch_metadata_order_does_not_change_the_selected_recording(runtime, tmp_path):
    requests, responses, _, _ = runtime
    configure_candidates(responses, "En-us-curious.ogg", "En-us-curious-2.ogg")
    responses["metadata"]["query"]["pages"].reverse()
    result = recordings.record_word("curious", "en-US", tmp_path)
    assert result is not None and result.attribution["source_url"] == SOURCE
    assert len(requests) == 3 and requests[-1].url.path.endswith("En-us-curious.ogg")


def test_mismatched_file_title_is_not_downloaded_even_with_valid_license(runtime, tmp_path):
    requests, responses, _, _ = runtime
    configure_candidates(responses, "En-us-curious.ogg")
    responses["metadata"]["query"]["pages"][0]["title"] = "File:En-us-unrelated.ogg"
    assert recordings.record_word("curious", "en-US", tmp_path) is None
    assert len(requests) == 2 and list(tmp_path.iterdir()) == []


def test_commons_filename_initial_capitalization_and_spaces_are_normalized(runtime, tmp_path):
    requests, responses, _, _ = runtime
    configure_candidates(responses, "en-us-curious_audio.ogg")
    responses["metadata"]["query"]["pages"][0]["title"] = "File:En-us-curious audio.ogg"
    assert recordings.record_word("curious", "en-US", tmp_path) is not None
    assert len(requests) == 3


def test_candidates_are_deduplicated_accent_specific_and_bounded_to_three(runtime, tmp_path):
    requests, responses, _, _ = runtime
    names = ["En-us-curious.ogg", "En-us-curious-2.ogg", "En-us-curious-3.ogg", "En-us-curious-4.ogg"]
    configure_candidates(responses, *names)
    responses["wikitext"] = "==English==\n{{audio|en|En-uk-curious.ogg}}\n" + "\n".join(
        f"{{{{audio|en|{name}}}}}" for name in [names[0], *names])
    responses["audio_by_path"] = {f"/wikipedia/commons/a/ab/{name}": b"invalid audio" for name in names[:3]}
    assert recordings.record_word("curious", "en-US", tmp_path) is None
    assert requests[1].url.params["titles"] == "|".join(f"File:{name}" for name in names[:3])
    assert len(requests) == 5
    assert [request.url.path.rsplit("/", 1)[-1] for request in requests[2:]] == names[:3]
    assert list(tmp_path.iterdir()) == []


def test_filename_cache_expires_and_is_bounded(runtime, tmp_path, monkeypatch):
    requests, _, _, _ = runtime
    monkeypatch.setattr(recordings, "MAX_LOOKUP_CACHE_ENTRIES", 2)
    assert recordings.record_word("curious", "en-US", tmp_path) is not None
    key = next(iter(recordings._word_choices))
    _, choices = recordings._word_choices[key]
    recordings._word_choices[key] = (recordings.monotonic() - 1, choices)
    assert recordings.record_word("curious", "en-GB", tmp_path) is not None
    assert sum(request.url.host == "en.wiktionary.org" for request in requests) == 2
    assert recordings.record_word("hello", "en-US", tmp_path) is not None
    assert recordings.record_word("vocabulary", "en-US", tmp_path) is not None
    assert len(recordings._word_choices) == 2
    assert key not in recordings._word_choices


def test_concurrent_requests_for_same_word_share_one_recording_lookup(runtime, tmp_path, monkeypatch):
    requests, _, _, _ = runtime
    original_fetch = recordings._fetch
    entered, release, start = Event(), Event(), Barrier(8)
    def fetch(client, url, deadline, **kwargs):
        if url == recordings.WIKTIONARY_API:
            entered.set()
            assert release.wait(3)
        return original_fetch(client, url, deadline, **kwargs)
    monkeypatch.setattr(recordings, "_fetch", fetch)
    def request():
        start.wait(timeout=3)
        return recordings.record_word("curious", "en-US", tmp_path)
    with ThreadPoolExecutor(max_workers=8) as executor:
        pending = [executor.submit(request) for _ in range(8)]
        assert entered.wait(3)
        release.set()
        results = [item.result(timeout=5) for item in pending]
    assert results[0] is not None and all(item == results[0] for item in results)
    assert len(requests) == 3
    assert not recordings._inflight and not recordings._word_inflight


def test_concurrent_accents_share_word_lookup_but_fetch_their_own_recording(runtime, tmp_path, monkeypatch):
    requests, _, _, _ = runtime
    original_fetch = recordings._fetch
    entered, release, start = Event(), Event(), Barrier(2)
    def fetch(client, url, deadline, **kwargs):
        if url == recordings.WIKTIONARY_API:
            entered.set()
            assert release.wait(3)
        return original_fetch(client, url, deadline, **kwargs)
    monkeypatch.setattr(recordings, "_fetch", fetch)
    def request(accent):
        start.wait(timeout=3)
        return recordings.record_word("curious", accent, tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        pending = [executor.submit(request, accent) for accent in ("en-US", "en-GB")]
        assert entered.wait(3)
        release.set()
        assert all(item.result(timeout=5) is not None for item in pending)
    assert len(requests) == 5
    assert sum(request.url.host == "en.wiktionary.org" for request in requests) == 1
    assert len(list((tmp_path / "dictionary-audio").glob("*.json"))) == 2


def test_failure_releases_concurrent_waiters_without_success_cache(runtime, tmp_path, monkeypatch):
    requests, responses, _, _ = runtime
    responses["wikitext"] = "==English==\nNo recording.\n"
    start = Barrier(6)
    def request():
        start.wait(timeout=3)
        return recordings.record_word("curious", "en-US", tmp_path)
    with ThreadPoolExecutor(max_workers=6) as executor:
        assert list(executor.map(lambda _: request(), range(6))) == [None] * 6
    assert len(requests) == 1 and not recordings._inflight and not recordings._word_inflight
    assert list(tmp_path.iterdir()) == []


def test_shared_http_pool_reuses_client_until_explicit_close(runtime, tmp_path, monkeypatch):
    factory, clients = recordings._new_client, []
    def new_client():
        client = factory()
        clients.append(client)
        return client
    requests, _, _, _ = runtime
    monkeypatch.setattr(recordings, "_new_client", new_client)
    assert recordings.record_word("curious", "en-US", tmp_path) is not None
    assert recordings.record_word("hello", "en-US", tmp_path) is not None
    assert len(clients) == 1 and not clients[0].is_closed and len(requests) == 6
    recordings.close_recording_client()
    assert clients[0].is_closed
    assert recordings.record_word("vocabulary", "en-US", tmp_path) is not None
    assert len(clients) == 2 and not clients[1].is_closed and len(requests) == 9


def test_shared_client_does_not_serialize_independent_words(runtime, tmp_path, monkeypatch):
    requests, _, _, _ = runtime
    original_fetch, both_started = recordings._fetch, Barrier(2)
    def fetch(client, url, deadline, **kwargs):
        if url == recordings.WIKTIONARY_API:
            both_started.wait(timeout=3)
        return original_fetch(client, url, deadline, **kwargs)
    monkeypatch.setattr(recordings, "_fetch", fetch)
    with ThreadPoolExecutor(max_workers=2) as executor:
        pending = [executor.submit(recordings.record_word, word, "en-US", tmp_path) for word in ("curious", "hello")]
        assert all(item.result(timeout=5) is not None for item in pending)
    assert len(requests) == 6


def test_shutdown_waits_for_active_lease_before_closing(monkeypatch):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    monkeypatch.setattr(recordings, "_new_client", lambda: client)
    entered, release, closing, closed = Event(), Event(), Event(), Event()
    def use():
        with recordings._client() as leased:
            assert leased is client
            entered.set()
            assert release.wait(3)
            assert not leased.is_closed
    def close():
        closing.set()
        recordings.close_recording_client()
        closed.set()
    with ThreadPoolExecutor(max_workers=2) as executor:
        reader = executor.submit(use)
        assert entered.wait(3)
        closer = executor.submit(close)
        assert closing.wait(3) and not closed.wait(.05)
        release.set()
        reader.result(timeout=3)
        closer.result(timeout=3)
    assert client.is_closed and closed.is_set()


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


def test_expired_external_deadline_does_not_start_network(runtime, tmp_path):
    requests, _, _, _ = runtime
    assert recordings.record_word("curious", "en-US", tmp_path, deadline=recordings.monotonic() - 1) is None
    assert requests == [] and list(tmp_path.iterdir()) == []


def test_expired_external_deadline_still_allows_an_existing_local_recording(runtime, tmp_path, monkeypatch):
    requests, _, _, _ = runtime
    initial = recordings.record_word("curious", "en-US", tmp_path)
    monkeypatch.setattr(recordings, "_client", lambda: pytest.fail("cache hit used network"))
    assert recordings.record_word("curious", "en-US", tmp_path, deadline=recordings.monotonic() - 1) == initial
    assert len(requests) == 3


def test_one_external_deadline_is_used_across_all_candidates(runtime, tmp_path, monkeypatch):
    requests, responses, _, _ = runtime
    configure_candidates(responses, "En-us-curious.ogg", "En-us-curious-2.ogg")
    original_fetch, seen, clock = recordings._fetch, [], [100.0]
    monkeypatch.setattr(recordings, "monotonic", lambda: clock[0])
    def fetch(client, url, deadline, **kwargs):
        seen.append(deadline)
        result = original_fetch(client, url, deadline, **kwargs)
        if url.startswith("https://upload.wikimedia.org/"):
            clock[0] = 100.2
        return result
    monkeypatch.setattr(recordings, "_fetch", fetch)
    assert recordings.record_word("curious", "en-US", tmp_path, deadline=100.1) is None
    assert seen == [100.1] * 3
    assert len(requests) == 3 and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("external_budget,maximum", [(0.25, 0.25), (100, recordings.LOOKUP_SECONDS)])
def test_shared_inflight_wait_respects_external_deadline_and_nine_second_cap(runtime, tmp_path, external_budget, maximum):
    requests, _, _, _ = runtime
    digest = recordings.sha256(json.dumps([recordings.CACHE_VERSION, "curious", "en-US"]).encode("ascii")).hexdigest()
    waits = []
    def result(*, timeout):
        waits.append(timeout)
        raise recordings.FutureTimeoutError()
    recordings._inflight[f"{tmp_path}:{digest}"] = SimpleNamespace(result=result)
    assert recordings.record_word("curious", "en-US", tmp_path, deadline=recordings.monotonic() + external_budget) is None
    assert len(waits) == 1 and 0 <= waits[0] <= maximum
    assert requests == [] and list(tmp_path.iterdir()) == []


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
        fallback = client.post("/api/pronunciation", json={"text": "curious", "accent": "en-US", "allow_word_synthesis": True})
    assert fallback.status_code == 200 and fallback.headers["X-Pronunciation-Source"] == "piper"
    assert fallback.headers["X-Pronunciation-Attribution"] == ""
    assert piper_calls == [("A curious reader.", "en-US"), ("curious", "en-US")]
    assert len(requests) == 1
