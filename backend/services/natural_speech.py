"""Optional, entirely local Kitten Micro speech with the user's selected Rosie voice.

Input tokenization follows KittenTTS 0.8.1 (Apache-2.0). Models are installed by
the setup command; request handling never imports kittentts or downloads assets.
Unlike the upstream example, all model audio, including its tail, is retained.
"""
from __future__ import annotations

from hashlib import sha256
from importlib import import_module, util
from io import BytesIO
import json
import logging
import math
from pathlib import Path
import re
from threading import Lock
import wave

LOGGER = logging.getLogger(__name__)
VOICE_ID = "kitten-micro-0.8-rosie"
ACTUAL_ACCENT = "en-US"
MODEL_FILENAME = "kitten_tts_micro_v0_8.onnx"
MODEL_REVISION = "1ccf72b2c2048fd17efac7de2fab32d10e225084"
SOURCE_RATE = 24_000
OUTPUT_RATE = 22_050
CACHE_VERSION = "kitten-micro-rosie-pcm22050-full-tail-v1"
_inference_lock = Lock()
_engine = None
_engine_identity = None


def _speech():
    # Pronunciation selects the engine, so import its shared validation lazily.
    from backend.services import pronunciation
    return pronunciation


def _unavailable(message: str = "Rosie 合成朗读暂时不可用，请稍后重试。"):
    return _speech().PronunciationUnavailable(message)


def _dependency_problem() -> str | None:
    for name in ("numpy", "onnxruntime", "miniaudio", "phonemizer", "espeakng_loader"):
        try:
            if util.find_spec(name) is None:
                return "Rosie 合成朗读组件尚未安装。"
        except (ImportError, ValueError, AttributeError):
            return "Rosie 合成朗读组件不可用。"
    return None


def _model_dir(storage_dir: Path) -> Path:
    return Path(storage_dir) / "tts" / "kitten-micro-0.8"


def _file_identity(storage_dir: Path) -> tuple:
    directory = _model_dir(storage_dir)
    identity = []
    try:
        for filename in ("config.json", MODEL_FILENAME, "voices.npz"):
            path = directory / filename
            info = path.stat()
            if not path.is_file() or info.st_size <= 0:
                raise OSError("missing asset")
            if filename == "config.json" and info.st_size > 16_384:
                raise OSError("invalid configuration size")
            identity.append((filename, info.st_size, info.st_mtime_ns))
    except OSError as exc:
        raise _unavailable("Rosie 合成朗读模型尚未安装，请运行发音模型安装命令。") from exc
    speech = _speech()
    cadence = (SOURCE_RATE, OUTPUT_RATE, speech.LEADING_SILENCE_SECONDS,
               speech.TRAILING_SILENCE_SECONDS, speech.SENTENCE_PAUSE_SECONDS)
    return (CACHE_VERSION, MODEL_REVISION, VOICE_ID, cadence, *identity)


def natural_status(storage_dir: Path) -> tuple[bool, str | None]:
    """Probe dependencies/files only: no imports, model loading or cache writes."""
    reason = _dependency_problem()
    if reason is None:
        try:
            _file_identity(storage_dir)
        except _speech().PronunciationUnavailable as exc:
            reason = str(exc)
    return reason is None, reason


def _word_index() -> dict[str, int]:
    # Exact symbol order from the pinned KittenTTS 0.8.1 TextCleaner.
    punctuation = ';:,.!?¡¿—…"«»"" '
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    ipa = "ɑɐɒæɓʙβɔɕçɗɖðʤəɘɚɛɜɝɞɟʄɡɠɢʛɦɧħɥʜɨɪʝɭɬɫɮʟɱɯɰŋɳɲɴøɵɸθœɶʘɹɺɾɻʀʁɽʂʃʈʧʉʊʋⱱʌɣɤʍχʎʏʑʐʒʔʡʕʢǀǁǂǃˈˌːˑʼʴʰʱʲʷˠˤ˞↓↑→↗↘'̩'ᵻ"
    return {symbol: index for index, symbol in enumerate(["$"] + list(punctuation) + list(letters) + list(ipa))}


def _chunks(text: str) -> list[str]:
    # Match upstream generate's sentence splitting and comma termination. Keep
    # the shared maximum length, with explicit pauses at each sentence/chunk.
    result = []
    for block in _speech().split_text(text):
        for part in re.split(r"[.!?]+", block):
            part = part.strip()
            if part:
                result.append(part if part[-1] in ".!?,;:" else part + ",")
    return result


class _KittenAdapter:
    def __init__(self, directory: Path):
        np = import_module("numpy")
        ort = import_module("onnxruntime")
        phonemizer = import_module("phonemizer.backend")
        wrapper = import_module("phonemizer.backend.espeak.wrapper")
        loader = import_module("espeakng_loader")
        config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
        if (config.get("model_file") != MODEL_FILENAME or config.get("voices") != "voices.npz"
                or config.get("voice_aliases", {}).get("Rosie") != "expr-voice-4-f"):
            raise _unavailable("Rosie 合成朗读模型配置不匹配，请重新安装。")
        self.voice = config["voice_aliases"]["Rosie"]
        self.speed = config.get("speed_priors", {}).get(self.voice, 1.0)
        if isinstance(self.speed, bool) or not isinstance(self.speed, (int, float)) or not math.isfinite(self.speed) or not .25 <= self.speed <= 4:
            raise _unavailable("Rosie 合成朗读语速配置无效，请重新安装。")
        with np.load(directory / "voices.npz", allow_pickle=False) as voices:
            self.style = np.asarray(voices[self.voice], dtype=np.float32).copy()
        if self.style.ndim != 2 or self.style.shape[0] == 0 or self.style.shape[1] != 256 or not np.isfinite(self.style).all():
            raise _unavailable("Rosie 合成朗读声线配置无效，请重新安装。")
        self.word_index = _word_index()
        wrapper.EspeakWrapper.set_library(loader.get_library_path())
        wrapper.EspeakWrapper.set_data_path(loader.get_data_path())
        self.phonemizer = phonemizer.EspeakBackend(language="en-us", preserve_punctuation=True, with_stress=True)
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.log_severity_level = 3
        self.session = ort.InferenceSession(str(directory / MODEL_FILENAME), sess_options=options, providers=["CPUExecutionProvider"])

    def synthesize(self, chunk: str):
        np = import_module("numpy")
        phonemes = self.phonemizer.phonemize([chunk])
        if not phonemes or not isinstance(phonemes[0], str) or not phonemes[0].strip():
            raise _unavailable()
        normalized = " ".join(re.findall(r"\w+|[^\w\s]", phonemes[0]))
        tokens = [self.word_index[char] for char in normalized if char in self.word_index]
        if not tokens:
            raise _unavailable()
        reference = min(len(chunk), self.style.shape[0] - 1)
        inputs = {
            "input_ids": np.array([[0, *tokens, 10, 0]], dtype=np.int64),
            "style": self.style[reference:reference + 1],
            "speed": np.array([self.speed], dtype=np.float32),
        }
        outputs = self.session.run(None, inputs)
        if not outputs:
            raise _unavailable()
        audio = np.asarray(outputs[0], dtype=np.float32)
        if audio.ndim == 2 and audio.shape[0] == 1:
            audio = audio[0]
        # Preserve the full model output; upstream's [:-5000] would discard it.
        return audio


def _get_engine(storage_dir: Path, identity: tuple):
    global _engine, _engine_identity
    engine_identity = (str(Path(storage_dir).resolve()), identity)
    if _engine is not None and _engine_identity == engine_identity:
        return _engine
    engine = _KittenAdapter(_model_dir(storage_dir))
    _engine, _engine_identity = engine, engine_identity
    return engine


def _resample(samples):
    np = import_module("numpy")
    miniaudio = import_module("miniaudio")
    data = miniaudio.convert_frames(
        miniaudio.SampleFormat.FLOAT32, 1, SOURCE_RATE, samples.astype("<f4", copy=False).tobytes(),
        miniaudio.SampleFormat.FLOAT32, 1, OUTPUT_RATE,
    )
    return np.frombuffer(data, dtype="<f4")


def _render(engine, text: str) -> bytes:
    speech = _speech()
    np = import_module("numpy")
    leading = np.zeros(round(OUTPUT_RATE * speech.LEADING_SILENCE_SECONDS), dtype=np.float32)
    trailing = np.zeros(round(OUTPUT_RATE * speech.TRAILING_SILENCE_SECONDS), dtype=np.float32)
    parts = [leading]
    total_frames = leading.size + trailing.size
    for index, chunk in enumerate(_chunks(text)):
        raw = np.asarray(engine.synthesize(chunk), dtype=np.float32)
        if (raw.ndim != 1 or raw.size == 0 or not np.isfinite(raw).all()
                or not np.any(raw) or raw.size * OUTPUT_RATE / SOURCE_RATE * 2 + 44 > speech.MAX_AUDIO_BYTES):
            raise _unavailable("Rosie 合成音频无效或过长，请缩短文本后重试。")
        samples = _resample(raw)
        if samples.size == 0 or not np.isfinite(samples).all():
            raise _unavailable()
        if index:
            pause = np.zeros(round(OUTPUT_RATE * speech.SENTENCE_PAUSE_SECONDS), dtype=np.float32)
            parts.append(pause)
            total_frames += pause.size
        total_frames += samples.size
        if total_frames * 2 + 44 > speech.MAX_AUDIO_BYTES:
            raise _unavailable("朗读音频过长，请缩短选择的句子。")
        parts.append(samples)
    if len(parts) == 1:
        raise _unavailable()
    parts.append(trailing)
    pcm = (np.clip(np.concatenate(parts), -1, 1) * 32767).astype("<i2").tobytes()
    output = BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(OUTPUT_RATE)
        audio.writeframes(pcm)
    result = output.getvalue()
    if not speech._valid_wav(result):
        raise _unavailable()
    return result


def synthesize_natural(text: str, storage_dir: Path) -> tuple[bytes, str]:
    speech = _speech()
    normalized = speech.normalize_text(text)
    reason = _dependency_problem()
    if reason is not None:
        raise _unavailable(reason)
    storage_dir = Path(storage_dir)
    identity = _file_identity(storage_dir)
    digest = sha256(json.dumps([identity, normalized], ensure_ascii=True).encode("ascii")).hexdigest()
    cache_path = storage_dir / "natural-tts-audio" / f"{digest}.wav"
    cached = speech._read_cache(cache_path)
    if cached is not None:
        return cached, VOICE_ID
    if not _inference_lock.acquire(blocking=False):
        raise speech.PronunciationBusy("正在准备其他朗读，请稍后重试。")
    try:
        cached = speech._read_cache(cache_path)
        if cached is not None:
            return cached, VOICE_ID
        try:
            result = _render(_get_engine(storage_dir, identity), normalized)
        except speech.PronunciationUnavailable:
            raise
        except Exception as exc:
            LOGGER.warning("Rosie synthesis failed (%s)", type(exc).__name__)
            raise _unavailable() from exc
        speech._save_cache(cache_path, result)
        return result, VOICE_ID
    finally:
        _inference_lock.release()
