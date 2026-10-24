"""On-demand English recordings from openly licensed Wiktionary/Commons files.

Only a requested word is looked up. Each recording keeps its author, license and
source, and is never presented as a recording if retrieval or validation fails.
"""
from __future__ import annotations

from array import array
import atexit
import base64
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
from importlib import import_module
from io import BytesIO
import json
import os
from pathlib import Path
import re
import sys
from tempfile import NamedTemporaryFile
from threading import Condition, Lock
from time import monotonic, time
import unicodedata
from urllib.parse import unquote, urlsplit, urlunsplit
import wave

import httpx

SAMPLE_RATE = 22_050
LEADING_FRAMES = round(SAMPLE_RATE * .35)
TRAILING_FRAMES = round(SAMPLE_RATE * .20)
MAX_RECORDING_SECONDS = 12
MAX_BODY_BYTES = 1024 * 1024
MAX_CACHE_ENTRY_BYTES = 2 * 1024 * 1024
MAX_CACHE_BYTES = 128 * 1024 * 1024
CACHE_TTL_SECONDS = 30 * 24 * 60 * 60
MAX_RECORDING_CANDIDATES = 3
# Keep filename lookup, batched metadata and at most three audio downloads within
# one budget; warmed requests are faster, and successful recordings stay local.
LOOKUP_SECONDS = 9.0
CACHE_VERSION = "commons-english-pcm22050-v1"
WIKTIONARY_API = "https://en.wiktionary.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
LOOKUP_CACHE_SECONDS = 5 * 60
MAX_LOOKUP_CACHE_ENTRIES = 128
_negative: dict[str, float] = {}
_lookup_lock = Lock()
_inflight: dict[str, Future[Recording | None]] = {}
_word_choices: dict[str, tuple[float, dict[str, tuple[str, ...]]]] = {}
_word_inflight: dict[str, Future[dict[str, tuple[str, ...]] | None]] = {}
_client_condition = Condition()
_shared_client: httpx.Client | None = None
_client_users = 0
_client_closing = False


@dataclass(frozen=True)
class Recording:
    audio: bytes
    attribution: dict[str, str]


def _word(text: str) -> str | None:
    text = text.strip().rstrip(".!?").replace("’", "'").replace("‘", "'")
    if len(text) > 80 or not re.fullmatch(r"[A-Za-z]+(?:[-'][A-Za-z]+)*'?", text):
        return None
    return text


def _new_client() -> httpx.Client:
    return httpx.Client(follow_redirects=False,
                        limits=httpx.Limits(max_connections=8, max_keepalive_connections=6, keepalive_expiry=60.0),
                        headers={"User-Agent": "LexiRead/0.1 (personal English study; https://github.com/MuAn1228/lexi-read)"})


@contextmanager
def _client():
    """Lease the thread-safe pool without serializing independent requests."""
    global _shared_client, _client_users
    with _client_condition:
        while _client_closing:
            _client_condition.wait()
        if _shared_client is None:
            _shared_client = _new_client()
        client = _shared_client
        _client_users += 1
    try:
        yield client
    finally:
        with _client_condition:
            _client_users -= 1
            _client_condition.notify_all()


def close_recording_client() -> None:
    """Close idle connections at application shutdown, after active leases end."""
    global _shared_client, _client_closing
    with _client_condition:
        while _client_closing:
            _client_condition.wait()
        _client_closing = True
        while _client_users:
            _client_condition.wait()
        client, _shared_client = _shared_client, None
    try:
        if client is not None:
            client.close()
    finally:
        with _client_condition:
            _client_closing = False
            _client_condition.notify_all()


atexit.register(close_recording_client)


def _fetch(client: httpx.Client, url: str, deadline: float, *, params=None) -> bytes:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError("Recording lookup deadline exceeded")
    with client.stream("GET", url, params=params, timeout=min(4.0, remaining)) as response:
        response.raise_for_status()
        length = response.headers.get("Content-Length")
        if length and int(length) > MAX_BODY_BYTES:
            raise ValueError("Recording response is too large")
        parts, size = [], 0
        for part in response.iter_bytes():
            size += len(part)
            if size > MAX_BODY_BYTES or monotonic() > deadline:
                raise ValueError("Recording response exceeds limits")
            parts.append(part)
        return b"".join(parts)


def _recording_filenames(wikitext: str, accent: str) -> tuple[str, ...]:
    english = re.search(r"(?ms)^==\s*English\s*==[ \t]*\r?\n(.*?)(?=^==[^=]|\Z)", wikitext)
    if english is None:
        return ()
    choices: list[str] = []
    for match in re.finditer(r"\{\{\s*audio\s*\|\s*en\s*\|\s*([^{}\n]+?)\s*\}\}", english.group(1), re.I):
        fields = [part.strip() for part in match.group(1).split("|")]
        filename = fields[0]
        if (not filename or len(filename) > 200 or any(c in filename for c in "/\\\r\n?#")
                or not re.search(r"\.(?:wav|ogg|oga|mp3|flac)$", filename, re.I)):
            continue
        inferred: set[str] = set()
        if re.match(r"en[-_]us[-_]", filename, re.I):
            inferred.add("en-US")
        if re.match(r"en[-_](?:uk|gb)[-_]", filename, re.I):
            inferred.add("en-GB")
        labels = [part.split("=", 1)[1].strip().casefold() for part in fields[1:]
                  if re.match(r"a\s*=", part, re.I)]
        for label in labels:
            for variant in re.split(r"\s*[,;]\s*", label):
                if variant in {"ga", "us", "usa", "general american", "american", "united states"}:
                    inferred.add("en-US")
                if variant in {"uk", "gb", "rp", "received pronunciation", "southern england", "british", "england"}:
                    inferred.add("en-GB")
        if inferred == {accent}:
            if filename not in choices:
                choices.append(filename)
            if len(choices) >= MAX_RECORDING_CANDIDATES:
                break
    return tuple(choices)


def _recording_choices(client: httpx.Client, word: str, key: str, deadline: float) -> dict[str, tuple[str, ...]]:
    """Share one small, short-lived lookup between the two requested accents."""
    with _lookup_lock:
        now = monotonic()
        cached = _word_choices.get(key)
        if cached is not None and cached[0] > now:
            return cached[1]
        pending = _word_inflight.get(key)
        leader = pending is None
        if leader:
            pending = Future()
            _word_inflight[key] = pending
    if not leader:
        try:
            choices = pending.result(timeout=max(0, deadline - monotonic()))
        except FutureTimeoutError:
            raise TimeoutError("Recording lookup deadline exceeded") from None
        if choices is None:
            raise ValueError("Recording lookup failed")
        return choices
    try:
        entry = json.loads(_fetch(client, WIKTIONARY_API, deadline, params={
            "action": "parse", "page": word, "prop": "wikitext", "format": "json", "formatversion": "2", "redirects": "1"}))
        text = entry.get("parse", {}).get("wikitext", "")
        choices = {accent: _recording_filenames(text, accent) for accent in ("en-US", "en-GB")}
        with _lookup_lock:
            # Only validated filenames are retained, rather than entire articles.
            for expired in [name for name, value in _word_choices.items() if value[0] <= monotonic()]:
                _word_choices.pop(expired, None)
            _word_choices.pop(key, None)
            if len(_word_choices) >= MAX_LOOKUP_CACHE_ENTRIES:
                _word_choices.pop(next(iter(_word_choices)))
            _word_choices[key] = (monotonic() + LOOKUP_CACHE_SECONDS, choices)
        pending.set_result(choices)
        return choices
    finally:
        if not pending.done():
            pending.set_result(None)
        with _lookup_lock:
            _word_inflight.pop(key, None)


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _plain(value: str) -> str:
    parser = _PlainText()
    parser.feed(value)
    clean = "".join(c for c in " ".join(parser.parts) if unicodedata.category(c) not in {"Cc", "Cf", "Cs"})
    return " ".join(clean.split()).strip()


def _safe_url(value: str, host: str, prefix: str, *, strip_query=False) -> str | None:
    if not isinstance(value, str) or any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or parsed.hostname != host or parsed.username
                or parsed.password or parsed.port is not None or not parsed.path.startswith(prefix)):
            return None
    except ValueError:
        return None
    decoded = unquote(parsed.path)
    if ("\\" in decoded or any(part in {".", ".."} for part in decoded.split("/"))
            or any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in decoded)):
        return None
    if not strip_query and (parsed.query or parsed.fragment):
        return None
    return urlunsplit(("https", host, parsed.path, "", ""))


def _licensed_attribution(author: str, license_name: str, source_url: str, license_url: str) -> dict[str, str] | None:
    author, license_name = _plain(author), _plain(license_name)
    if not author or len(author) > 240 or len(license_name) > 60:
        return None
    source_url = _safe_url(source_url, "commons.wikimedia.org", "/wiki/File:")
    license_url = _safe_url(license_url, "creativecommons.org", "/")
    if not source_url or not license_url:
        return None
    path = urlsplit(license_url).path
    cc_license = re.fullmatch(r"CC BY(?P<sa>-SA)? (?P<version>1\.0|2\.0|2\.5|3\.0|4\.0)(?: (?P<jurisdiction>[a-z]{2}))?", license_name, re.I)
    if cc_license:
        kind = "by-sa" if cc_license["sa"] else "by"
        jurisdiction = cc_license["jurisdiction"]
        directory = f"/licenses/{kind}/{cc_license['version']}/"
        if jurisdiction:
            directory += jurisdiction.casefold() + "/"
        # Commons also returns links to translated deeds. Keep the exact license
        # version and jurisdiction, but use its stable canonical directory URL.
        if (path not in {directory, directory.rstrip("/")}
                and not re.fullmatch(rf"{re.escape(directory)}deed(?:\.[a-z-]+)?", path)):
            return None
        license_url = "https://creativecommons.org" + directory
    elif license_name.casefold() in {"cc0", "cc0 1.0", "public domain", "pd"}:
        if path not in {"/publicdomain/zero/1.0/", "/publicdomain/mark/1.0/"}:
            return None
    else:
        return None
    return {"author": author, "license": license_name, "source_url": source_url, "license_url": license_url}


def _file_title(value: str) -> str:
    """Only apply Commons' space and initial-letter file-title normalization."""
    if not isinstance(value, str) or not value.startswith("File:"):
        return ""
    name = " ".join(value[5:].replace("_", " ").split())
    return "File:" + name[:1].upper() + name[1:]


def _file_metadata(data: dict, filename: str | None = None) -> tuple[str, dict[str, str]] | None:
    pages = data.get("query", {}).get("pages", [])
    if not isinstance(pages, list) or not pages:
        return None
    page = next((page for page in pages if isinstance(page, dict)
                 and _file_title(page.get("title", "")) == _file_title(f"File:{filename}")), None) if filename is not None else pages[0]
    if not isinstance(page, dict):
        return None
    infos = page.get("imageinfo", [])
    if not infos:
        return None
    info = infos[0]
    audio_url = _safe_url(info.get("url", ""), "upload.wikimedia.org", "/wikipedia/commons/", strip_query=True)
    metadata = info.get("extmetadata", {})
    value = lambda key: metadata.get(key, {}).get("value", "")
    license_url = value("LicenseUrl")
    if (not license_url and value("LicenseShortName").strip().casefold() == "public domain"
            and value("License").strip().casefold() == "pd"
            and value("Copyrighted").strip().casefold() == "false"
            and value("AttributionRequired").strip().casefold() == "false"
            and not value("Restrictions").strip()):
        # The Public Domain Mark identifies public-domain status; it is not a
        # substitute CC0 license. Accept it only with the corroborating fields.
        license_url = "https://creativecommons.org/publicdomain/mark/1.0/"
    attribution = _licensed_attribution(value("Artist"), value("LicenseShortName"),
                                        info.get("descriptionurl", ""), license_url)
    if not audio_url or not attribution:
        return None
    return audio_url, attribution


def _decode_audio(data: bytes, *, deadline: float | None = None) -> bytes:
    decoder = import_module("miniaudio")
    stream = None
    parts, total_frames = [], 0
    try:
        # Decode no more than the allowed duration plus a single detection frame.
        # A small compressed file can otherwise allocate minutes of PCM at once.
        stream = decoder.stream_memory(data, output_format=decoder.SampleFormat.SIGNED16,
                                       nchannels=1, sample_rate=SAMPLE_RATE, frames_to_read=4096)
        while True:
            if deadline is not None and monotonic() > deadline:
                raise ValueError("Recording lookup deadline exceeded")
            requested = min(4096, SAMPLE_RATE * MAX_RECORDING_SECONDS - total_frames + 1)
            try:
                samples = stream.send(requested)
            except StopIteration:
                break
            if (not isinstance(samples, array) or samples.typecode != "h" or samples.itemsize != 2
                    or not 0 < len(samples) <= requested):
                raise ValueError("Recording audio format is invalid")
            total_frames += len(samples)
            if total_frames > SAMPLE_RATE * MAX_RECORDING_SECONDS:
                raise ValueError("Recording audio exceeds limits")
            if sys.byteorder != "little":
                samples.byteswap()
            parts.append(samples.tobytes())
    except Exception as exc:
        raise ValueError("Recording audio could not be decoded") from exc
    finally:
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass
    if total_frames <= 0:
        raise ValueError("Recording audio format is invalid")
    pcm = b"".join(parts)
    output = BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(SAMPLE_RATE)
        audio.writeframes(bytes(LEADING_FRAMES * 2) + pcm + bytes(TRAILING_FRAMES * 2))
    return output.getvalue()


def _valid_audio(data: bytes) -> bool:
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE" or int.from_bytes(data[4:8], "little") != len(data) - 8:
        return False
    try:
        with wave.open(BytesIO(data), "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, SAMPLE_RATE, "NONE"):
                return False
            frames = audio.getnframes()
            if not LEADING_FRAMES + TRAILING_FRAMES < frames <= SAMPLE_RATE * MAX_RECORDING_SECONDS + LEADING_FRAMES + TRAILING_FRAMES:
                return False
            pcm = audio.readframes(frames)
            return (len(pcm) == frames * 2 and pcm[:LEADING_FRAMES * 2] == bytes(LEADING_FRAMES * 2)
                    and pcm[-TRAILING_FRAMES * 2:] == bytes(TRAILING_FRAMES * 2)
                    and len(data) == frames * 2 + 44)
    except (wave.Error, EOFError, OSError, ValueError):
        return False


def _cache_read(path: Path, word: str, accent: str) -> Recording | None:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_CACHE_ENTRY_BYTES:
            return None
        payload = json.loads(path.read_text(encoding="utf8"))
        age = time() - payload["created_at"]
        if (payload["version"] != CACHE_VERSION or payload["word"] != word or payload["accent"] != accent
                or not 0 <= age < CACHE_TTL_SECONDS):
            return None
        attribution = payload["attribution"]
        checked = _licensed_attribution(attribution["author"], attribution["license"], attribution["source_url"], attribution["license_url"])
        data = base64.b64decode(payload["audio"], validate=True)
        return Recording(data, checked) if checked and _valid_audio(data) else None
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
        return None


def _cache_save(path: Path, word: str, accent: str, recording: Recording) -> None:
    temporary = None
    try:
        payload = {"version": CACHE_VERSION, "created_at": time(), "word": word, "accent": accent,
                   "attribution": recording.attribution, "audio": base64.b64encode(recording.audio).decode("ascii")}
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        if len(encoded) > MAX_CACHE_ENTRY_BYTES:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, prefix="recording-", suffix=".part", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
        os.replace(temporary, path)
        entries = []
        for entry in path.parent.glob("*.json"):
            if not entry.is_symlink() and entry.is_file():
                stat = entry.stat()
                entries.append((stat.st_mtime_ns, stat.st_size, entry))
        total = sum(item[1] for item in entries)
        for _, size, entry in sorted(entries):
            if total <= MAX_CACHE_BYTES:
                break
            entry.unlink(missing_ok=True)
            total -= size
    except OSError:
        pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def record_word(text: str, accent: str, storage_dir: Path, *, deadline: float | None = None) -> Recording | None:
    word = _word(text)
    if word is None or accent not in {"en-US", "en-GB"}:
        return None
    digest = sha256(json.dumps([CACHE_VERSION, word, accent]).encode("ascii")).hexdigest()
    path = storage_dir / "dictionary-audio" / f"{digest}.json"
    cached = _cache_read(path, word, accent)
    if cached is not None:
        return cached
    deadline = min(monotonic() + LOOKUP_SECONDS, deadline) if deadline is not None else monotonic() + LOOKUP_SECONDS
    if monotonic() >= deadline:
        return None
    key = f"{storage_dir}:{digest}"
    with _lookup_lock:
        if _negative.get(key, 0) > monotonic():
            return None
        pending = _inflight.get(key)
        leader = pending is None
        if leader:
            pending = Future()
            _inflight[key] = pending
    if not leader:
        try:
            return pending.result(timeout=max(0, deadline - monotonic()))
        except FutureTimeoutError:
            return None
    try:
        # A preceding writer may have completed between our first read and the
        # in-flight check. Recheck locally before starting another remote lookup.
        recording = _cache_read(path, word, accent)
        if recording is None:
            try:
                with _client() as client:
                    choices = _recording_choices(client, word, f"{storage_dir}:{word}", deadline)
                    filenames = choices[accent]
                    if not filenames:
                        raise ValueError("No recording for the requested accent")
                    metadata = json.loads(_fetch(client, COMMONS_API, deadline, params={
                        "action": "query", "titles": "|".join(f"File:{name}" for name in filenames), "prop": "imageinfo", "iiprop": "url|extmetadata",
                        "format": "json", "formatversion": "2"}))
                    for filename in filenames:
                        if monotonic() >= deadline:
                            break
                        try:
                            selected = _file_metadata(metadata, filename)
                            if selected is None:
                                continue
                            audio_url, attribution = selected
                            audio = _decode_audio(_fetch(client, audio_url, deadline), deadline=deadline)
                            if monotonic() > deadline or not _valid_audio(audio):
                                continue
                            recording = Recording(audio, attribution)
                            break
                        except (httpx.HTTPError, httpx.InvalidURL, OSError, ValueError, KeyError, TypeError, AttributeError, ImportError, TimeoutError, RecursionError):
                            # A damaged or unavailable file must not hide a later
                            # eligible recording for this exact requested accent.
                            continue
                if recording is None:
                    raise ValueError("No eligible openly licensed recording")
                _cache_save(path, word, accent, recording)
            except (httpx.HTTPError, httpx.InvalidURL, OSError, ValueError, KeyError, TypeError, AttributeError, ImportError, TimeoutError, RecursionError):
                # Short-lived misses prevent a bad connection from delaying every replay.
                with _lookup_lock:
                    if len(_negative) >= 256:
                        _negative.clear()
                    _negative[key] = monotonic() + 60
        pending.set_result(recording)
        return recording
    finally:
        if not pending.done():
            pending.set_result(None)
        with _lookup_lock:
            _inflight.pop(key, None)
