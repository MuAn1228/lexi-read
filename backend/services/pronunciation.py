"""Local speech and attributed English recordings; no automatic model downloads."""
from __future__ import annotations

from hashlib import sha256
from importlib import import_module, util
from io import BytesIO
import json
import logging
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from threading import Lock
from time import monotonic
import unicodedata
import wave

from backend.config import get_settings

LOGGER = logging.getLogger(__name__)
SAMPLE_RATE = 22_050
MAX_TEXT_LENGTH = 4_000
MAX_CHUNK_LENGTH = 300
MAX_CACHE_BYTES = 128 * 1024 * 1024
MAX_AUDIO_BYTES = 48 * 1024 * 1024
LEADING_SILENCE_SECONDS = .35
TRAILING_SILENCE_SECONDS = .20
SENTENCE_PAUSE_SECONDS = .18
WORD_LENGTH_SCALE = 1.35
SENTENCE_LENGTH_SCALE = 1.18
CACHE_VERSION = "piper-pcm22050-clear-v2"
VOICES = {"en-US": "en_US-ljspeech-medium", "en-GB": "en_GB-cori-medium"}
_inference_lock = Lock()
_engine = None
_engine_identity = None


class PronunciationUnavailable(Exception):
    """Optional local speech runtime is absent or failed to synthesize."""


class PronunciationBusy(Exception):
    """One CPU inference is already in progress."""


class RecordingNotFound(Exception):
    """No usable English recording; synthesis has not been requested."""


def is_pronunciation_word(text: str) -> bool:
    word = text.strip().rstrip(".!?").replace("’", "'").replace("‘", "'")
    return re.fullmatch(r"[A-Za-z]+(?:[-'][A-Za-z]+)*'?", word) is not None


def _dependency_problem() -> str | None:
    for name in ("numpy", "onnxruntime", "piper"):
        try:
            if util.find_spec(name) is None:
                return "本机自然发音组件尚未安装，将使用系统朗读。"
        except (ImportError, ValueError, AttributeError):
            return "本机自然发音组件不可用，将使用系统朗读。"
    return None


def _model_paths(storage_dir: Path) -> tuple[Path, ...]:
    directory = storage_dir / "tts"
    return tuple(directory / f"{voice}{suffix}" for voice in VOICES.values()
                 for suffix in (".onnx", ".onnx.json"))


def _file_identity(storage_dir: Path) -> tuple:
    identity = []
    for path in _model_paths(storage_dir):
        try:
            stat = path.stat()
            if not path.is_file() or stat.st_size <= 0:
                raise OSError("missing speech model")
            identity.append((path.name, stat.st_size, stat.st_mtime_ns))
        except OSError as exc:
            raise PronunciationUnavailable("本机自然发音模型尚未安装，将使用系统朗读。") from exc
    # Version the reading cadence and protective silence as well as the model files.
    # Old unpadded clips must not survive a change to the pronunciation pipeline.
    return (CACHE_VERSION, *identity)


def pronunciation_status() -> dict:
    reason = _dependency_problem()
    if reason is None:
        try:
            _file_identity(get_settings().storage_dir)
        except PronunciationUnavailable as exc:
            reason = str(exc)
    return {"available": reason is None, "engine": "piper" if reason is None else "system", "reason": reason}


def normalize_text(text: str) -> str:
    if not isinstance(text, str) or len(text) > MAX_TEXT_LENGTH:
        raise ValueError("朗读文本不能超过 4000 个字符。")
    for char in text:
        category = unicodedata.category(char)
        if category in {"Cs", "Cf"} or (category == "Cc" and char not in "\n\r\t"):
            raise ValueError("朗读文本包含不支持的控制字符。")
        if char.isalpha() and not (char.isascii() or unicodedata.name(char, "").startswith("LATIN ")):
            raise ValueError("自然发音目前支持英文单词和英文句子，请选择英文文本。")
    normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()
    if not re.search(r"[A-Za-z]", normalized):
        raise ValueError("请选择包含英文字母的单词或句子。")
    return normalized


def split_text(text: str) -> list[str]:
    """Bound inference length at sentence/word boundaries, preserving reading order."""
    chunks: list[str] = []
    pending = ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        while len(sentence) > MAX_CHUNK_LENGTH:
            boundary = max(sentence.rfind(mark, 0, MAX_CHUNK_LENGTH + 1) for mark in (" ", ", ", "; ", ": "))
            if boundary < MAX_CHUNK_LENGTH // 2:
                boundary = MAX_CHUNK_LENGTH
            part, sentence = sentence[:boundary].strip(), sentence[boundary:].strip()
            if pending:
                chunks.append(pending)
                pending = ""
            if part:
                chunks.append(part)
        if pending and len(pending) + len(sentence) + 1 > MAX_CHUNK_LENGTH:
            chunks.append(pending)
            pending = ""
        pending = f"{pending} {sentence}".strip()
    if pending:
        chunks.append(pending)
    return chunks


class _PiperAdapter:
    """Keep model-specific synthesis arguments out of caching and WAV handling."""

    def __init__(self, engine, synthesis_config):
        self.engine = engine
        self.synthesis_config = synthesis_config

    def synthesize(self, text: str, accent: str):
        np = import_module("numpy")
        # A vocabulary word needs a clearer cadence than continuous prose. Keep
        # its original spelling and phonemes; length_scale changes model timing.
        single_word = re.fullmatch(r"[A-Za-z]+(?:[-'‘’][A-Za-z]+)*['‘’]?[.!?]?", text) is not None
        length_scale = WORD_LENGTH_SCALE if single_word else SENTENCE_LENGTH_SCALE
        chunks = list(self.engine.synthesize(text, syn_config=self.synthesis_config(length_scale=length_scale)))
        if not chunks or any((chunk.sample_rate, chunk.sample_width, chunk.sample_channels) !=
                             (SAMPLE_RATE, 2, 1) for chunk in chunks):
            raise PronunciationUnavailable("自然发音生成失败，请使用系统朗读或稍后重试。")
        parts = []
        for index, chunk in enumerate(chunks):
            if index:
                # Piper yields a separate chunk at each sentence boundary.
                parts.append(np.zeros(round(SAMPLE_RATE * SENTENCE_PAUSE_SECONDS), dtype=np.float32))
            parts.append(chunk.audio_float_array)
        return np.concatenate(parts), SAMPLE_RATE


def _get_engine(storage_dir: Path, identity: tuple, accent: str):
    # Called only while holding _inference_lock: model initialization and inference
    # share one bounded CPU slot rather than spawning additional worker engines.
    global _engine, _engine_identity
    engine_identity = (str(storage_dir), identity, accent)
    if _engine is not None and _engine_identity == engine_identity:
        return _engine
    ort = import_module("onnxruntime")
    piper = import_module("piper")
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    model_path = storage_dir / "tts" / f"{VOICES[accent]}.onnx"
    config_path = model_path.with_suffix(".onnx.json")
    with config_path.open(encoding="utf-8") as handle:
        config = piper.PiperConfig.from_dict(json.load(handle))
    if config.sample_rate != SAMPLE_RATE:
        raise PronunciationUnavailable("本机自然发音模型配置不匹配，请重新安装发音模型。")
    session = ort.InferenceSession(str(model_path), sess_options=options, providers=["CPUExecutionProvider"])
    engine = _PiperAdapter(piper.PiperVoice(session=session, config=config), piper.SynthesisConfig)
    _engine, _engine_identity = engine, engine_identity
    return engine


def _valid_wav(data: bytes) -> bool:
    if len(data) < 46 or len(data) > MAX_AUDIO_BYTES or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return False
    if int.from_bytes(data[4:8], "little") != len(data) - 8:
        return False
    try:
        with wave.open(BytesIO(data), "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, SAMPLE_RATE, "NONE"):
                return False
            frames = audio.getnframes()
            return frames > 0 and len(audio.readframes(frames)) == frames * 2
    except (wave.Error, EOFError, OSError, ValueError):
        return False


def _read_cache(path: Path) -> bytes | None:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_AUDIO_BYTES:
            return None
        data = path.read_bytes()
        if _valid_wav(data):
            return data
        path.unlink(missing_ok=True)
    except OSError:
        pass
    return None


def _trim_cache(directory: Path) -> None:
    entries = []
    for path in directory.glob("*.wav"):
        try:
            if not path.is_symlink() and path.is_file():
                stat = path.stat()
                entries.append((stat.st_mtime_ns, stat.st_size, path))
        except OSError:
            continue
    total = sum(size for _, size, _ in entries)
    for _, size, path in sorted(entries):
        if total <= MAX_CACHE_BYTES:
            break
        try:
            path.unlink(missing_ok=True)
            total -= size
        except OSError:
            continue


def _save_cache(path: Path, data: bytes) -> None:
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, prefix="speech-", suffix=".part", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
        os.replace(temporary, path)
        _trim_cache(path.parent)
    except OSError:
        LOGGER.warning("Could not persist local pronunciation audio")
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _render(engine, text: str, accent: str) -> bytes:
    np = import_module("numpy")
    chunks = split_text(text)
    # Start the output device before the first consonant and let its final sound
    # finish before playback ends. Never trim or fade the model's original audio.
    leading = np.zeros(round(SAMPLE_RATE * LEADING_SILENCE_SECONDS), dtype=np.float32)
    trailing = np.zeros(round(SAMPLE_RATE * TRAILING_SILENCE_SECONDS), dtype=np.float32)
    parts = [leading]
    total_frames = leading.size + trailing.size
    for index, chunk in enumerate(chunks):
        samples, sample_rate = engine.synthesize(chunk, accent)
        samples = np.asarray(samples, dtype=np.float32)
        if sample_rate != SAMPLE_RATE or samples.ndim != 1 or samples.size == 0 or not np.isfinite(samples).all():
            raise PronunciationUnavailable("自然发音生成失败，请使用系统朗读或稍后重试。")
        if index:
            pause = np.zeros(round(SAMPLE_RATE * SENTENCE_PAUSE_SECONDS), dtype=np.float32)
            parts.append(pause)
            total_frames += pause.size
        parts.append(samples)
        total_frames += samples.size
        if total_frames * 2 + 44 > MAX_AUDIO_BYTES:
            raise PronunciationUnavailable("朗读音频过长，请缩短选择的句子。")
    parts.append(trailing)
    pcm = (np.clip(np.concatenate(parts), -1, 1) * 32767).astype("<i2").tobytes()
    output = BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(SAMPLE_RATE)
        audio.writeframes(pcm)
    data = output.getvalue()
    if not _valid_wav(data):
        raise PronunciationUnavailable("自然发音生成失败，请使用系统朗读或稍后重试。")
    return data


def synthesize(text: str, accent: str) -> tuple[bytes, str]:
    text = normalize_text(text)
    if accent not in VOICES:
        raise ValueError("请选择美式英语或英式英语发音。")
    reason = _dependency_problem()
    if reason is not None:
        raise PronunciationUnavailable(reason)
    storage_dir = get_settings().storage_dir
    identity = _file_identity(storage_dir)
    digest = sha256(json.dumps([identity, text, accent], ensure_ascii=True).encode("ascii")).hexdigest()
    cache_path = storage_dir / "tts-audio" / f"{digest}.wav"
    cached = _read_cache(cache_path)
    if cached is not None:
        return cached, VOICES[accent]
    if not _inference_lock.acquire(blocking=False):
        raise PronunciationBusy("正在准备其他朗读，请稍后重试。")
    try:
        cached = _read_cache(cache_path)
        if cached is not None:
            return cached, VOICES[accent]
        try:
            data = _render(_get_engine(storage_dir, identity, accent), text, accent)
        except PronunciationUnavailable:
            raise
        except Exception as exc:
            LOGGER.warning("Local pronunciation failed (%s)", type(exc).__name__)
            raise PronunciationUnavailable("自然发音暂时不可用，请使用系统朗读或稍后重试。") from exc
        _save_cache(cache_path, data)
        return data, VOICES[accent]
    finally:
        _inference_lock.release()


def synthesize_with_source(text: str, accent: str, *, allow_word_synthesis: bool = False) -> tuple[bytes, str, str, dict | None]:
    """Prefer English recordings; synthesize a word only by explicit choice."""
    from backend.services.word_recordings import LOOKUP_SECONDS, record_word

    normalized = normalize_text(text)
    if accent not in VOICES:
        raise ValueError("请选择美式英语或英式英语发音。")
    if is_pronunciation_word(normalized):
        storage_dir = get_settings().storage_dir
        deadline = monotonic() + LOOKUP_SECONDS
        other_accent = "en-GB" if accent == "en-US" else "en-US"
        for actual_accent in (accent, other_accent):
            recording = record_word(normalized, actual_accent, storage_dir, deadline=deadline)
            if recording is not None:
                return recording.audio, f"wiktionary-{actual_accent}", "dictionary", recording.attribution
        if not allow_word_synthesis:
            raise RecordingNotFound("暂无可用的英美词典录音，请稍后重试；如需合成朗读，可在发音设置中开启。")
    data, voice = synthesize(normalized, accent)
    return data, voice, "piper", None
