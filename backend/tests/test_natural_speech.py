"""No real models/network: exercise Rosie audio boundaries with temporary data."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from threading import Event
from types import SimpleNamespace
import wave

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("miniaudio")

from backend.services import natural_speech as natural
from backend.services import pronunciation as speech


def read_pcm(data):
    with wave.open(BytesIO(data), "rb") as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 22_050)
        return np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")


class FakeEngine:
    def __init__(self):
        self.calls = []
        time = np.arange(24_000, dtype=np.float32) / 24_000
        self.samples = (.5 * np.sin(2 * np.pi * 440 * time)).astype(np.float32)

    def synthesize(self, text):
        self.calls.append(text)
        return self.samples


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    directory = natural._model_dir(tmp_path)
    directory.mkdir(parents=True)
    for name in ("config.json", natural.MODEL_FILENAME, "voices.npz"):
        (directory / name).write_bytes(b"isolated fake model")
    monkeypatch.setattr(natural, "_dependency_problem", lambda: None)
    engine = FakeEngine()
    monkeypatch.setattr(natural, "_get_engine", lambda *args: engine)
    return tmp_path, engine


def test_status_does_not_import_load_or_write(runtime, monkeypatch):
    storage, _ = runtime
    monkeypatch.setattr(natural, "import_module", lambda *args: pytest.fail("probe imported runtime"))
    monkeypatch.setattr(natural, "_get_engine", lambda *args: pytest.fail("probe loaded a model"))
    assert natural.natural_status(storage) == (True, None)
    assert not (storage / "natural-tts-audio").exists()


def test_dependency_probe_uses_find_spec_only(monkeypatch):
    seen = []
    monkeypatch.setattr(natural.util, "find_spec", lambda name: seen.append(name) or object())
    monkeypatch.setattr(natural, "import_module", lambda *args: pytest.fail("dependency probe imported runtime"))
    assert natural._dependency_problem() is None
    assert seen == ["numpy", "onnxruntime", "miniaudio", "phonemizer", "espeakng_loader"]
    monkeypatch.setattr(natural.util, "find_spec", lambda name: None)
    assert natural._dependency_problem()


def test_missing_dependency_or_model_reports_unavailable(runtime, monkeypatch):
    storage, engine = runtime
    monkeypatch.setattr(natural, "_dependency_problem", lambda: "缺少可选组件")
    assert natural.natural_status(storage) == (False, "缺少可选组件")
    with pytest.raises(speech.PronunciationUnavailable, match="缺少可选组件"):
        natural.synthesize_natural("eye", storage)
    monkeypatch.setattr(natural, "_dependency_problem", lambda: None)
    (natural._model_dir(storage) / natural.MODEL_FILENAME).unlink()
    assert natural.natural_status(storage)[0] is False
    with pytest.raises(speech.PronunciationUnavailable, match="模型"):
        natural.synthesize_natural("eye", storage)
    assert engine.calls == []


@pytest.mark.parametrize("text", ["", "123", "中文", "hello 中文", "hello\0", "hello\x1b", "he\u200bllo", "hello\ud800", "a" * 4001])
def test_invalid_text_rejected_before_inference(runtime, text):
    storage, engine = runtime
    with pytest.raises(ValueError):
        natural.synthesize_natural(text, storage)
    assert engine.calls == []


def test_real_resampling_preserves_duration_pitch_and_full_tail(runtime):
    storage, engine = runtime
    data, voice = natural.synthesize_natural("  eye\t ", storage)
    assert voice == "kitten-micro-0.8-rosie"
    assert engine.calls == ["eye,"]
    assert speech._valid_wav(data)
    pcm = read_pcm(data)
    leading = round(.35 * 22_050)
    trailing = round(.20 * 22_050)
    assert not pcm[:leading].any() and not pcm[-trailing:].any()
    actual = pcm[leading:-trailing]
    # A WAV-header-only change would keep 24000 frames and change 440 Hz to 404 Hz.
    assert len(actual) == 22_050
    expected = (np.clip(natural._resample(engine.samples), -1, 1) * 32767).astype("<i2")
    assert np.array_equal(actual, expected)
    peak_hz = np.fft.rfftfreq(len(actual), 1 / 22_050)[np.argmax(np.abs(np.fft.rfft(actual)))]
    assert abs(peak_hz - 440) < 2
    # In particular the model's final 5000 samples were not cut off.
    assert np.any(actual[-4000:])


def test_sentence_order_and_explicit_pauses(runtime):
    storage, engine = runtime
    data, _ = natural.synthesize_natural("First. Second! Third?", storage)
    assert engine.calls == ["First,", "Second,", "Third,"]
    pcm = read_pcm(data)
    leading, trailing, pause = round(.35 * 22_050), round(.2 * 22_050), round(.18 * 22_050)
    assert len(pcm) == leading + 3 * 22_050 + 2 * pause + trailing
    assert not pcm[leading + 22_050:leading + 22_050 + pause].any()


@pytest.mark.parametrize("bad", [
    np.array([], dtype=np.float32), np.array([0, 0], dtype=np.float32),
    np.array([np.nan, .5], dtype=np.float32), np.array([np.inf, .5], dtype=np.float32),
    np.array([[.1, .2], [.3, .4]], dtype=np.float32),
])
def test_invalid_model_audio_is_unavailable_and_never_cached(runtime, bad):
    storage, engine = runtime
    engine.samples = bad
    with pytest.raises(speech.PronunciationUnavailable):
        natural.synthesize_natural("eye", storage)
    assert not (storage / "natural-tts-audio").exists()


def test_size_limit_includes_protection_and_pause(runtime, monkeypatch):
    storage, _ = runtime
    monkeypatch.setattr(speech, "MAX_AUDIO_BYTES", 22_050 * 2 + 44)
    with pytest.raises(speech.PronunciationUnavailable, match="过长"):
        natural.synthesize_natural("eye", storage)


def test_cache_reuse_is_separate_from_piper_and_identity_invalidates(runtime, monkeypatch):
    storage, engine = runtime
    first, _ = natural.synthesize_natural("eye", storage)
    assert (storage / "natural-tts-audio").is_dir()
    assert not (storage / "tts-audio").exists()
    second, _ = natural.synthesize_natural(" eye ", storage)
    assert first == second and len(engine.calls) == 1
    monkeypatch.setattr(natural, "CACHE_VERSION", "new-cadence-test")
    natural.synthesize_natural("eye", storage)
    assert len(engine.calls) == 2
    model = natural._model_dir(storage) / natural.MODEL_FILENAME
    model.write_bytes(b"changed model identity")
    natural.synthesize_natural("eye", storage)
    assert len(engine.calls) == 3


def test_corrupt_cache_is_not_returned(runtime):
    storage, engine = runtime
    natural.synthesize_natural("eye", storage)
    next((storage / "natural-tts-audio").glob("*.wav")).write_bytes(b"RIFFbroken")
    data, _ = natural.synthesize_natural("eye", storage)
    assert len(engine.calls) == 2 and speech._valid_wav(data)


def test_read_only_cache_does_not_prevent_playback(runtime, monkeypatch):
    storage, _ = runtime
    def denied(*args, **kwargs):
        raise PermissionError("test read-only cache")
    monkeypatch.setattr(speech, "NamedTemporaryFile", denied)
    data, _ = natural.synthesize_natural("eye", storage)
    assert speech._valid_wav(data)
    assert not list((storage / "natural-tts-audio").glob("*.wav"))


def test_busy_engine_rejects_another_request_and_releases_after_failure(runtime, monkeypatch):
    storage, engine = runtime
    entered, release = Event(), Event()
    def delayed(text):
        entered.set()
        assert release.wait(3)
        return engine.samples
    monkeypatch.setattr(engine, "synthesize", delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(natural.synthesize_natural, "eye", storage)
        assert entered.wait(3)
        try:
            with pytest.raises(speech.PronunciationBusy):
                natural.synthesize_natural("different", storage)
        finally:
            release.set()
        assert speech._valid_wav(future.result()[0])
    def fail(text):
        raise RuntimeError("fake inference failure")
    monkeypatch.setattr(engine, "synthesize", fail)
    with pytest.raises(speech.PronunciationUnavailable):
        natural.synthesize_natural("uncached", storage)
    assert natural._inference_lock.acquire(blocking=False)
    natural._inference_lock.release()


def test_singleton_reuses_engine_until_asset_identity_changes(tmp_path, monkeypatch):
    created = []
    monkeypatch.setattr(natural, "_engine", None)
    monkeypatch.setattr(natural, "_engine_identity", None)
    monkeypatch.setattr(natural, "_KittenAdapter", lambda directory: created.append(directory) or object())
    first = natural._get_engine(tmp_path, ("first",))
    assert natural._get_engine(tmp_path, ("first",)) is first
    assert natural._get_engine(tmp_path, ("second",)) is not first
    assert len(created) == 2


@pytest.fixture
def adapter_runtime(tmp_path, monkeypatch):
    config = {"model_file": natural.MODEL_FILENAME, "voices": "voices.npz",
              "voice_aliases": {"Rosie": "expr-voice-4-f"}, "speed_priors": {"expr-voice-4-f": 1.1}}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    style = np.arange(400 * 256, dtype=np.float32).reshape(400, 256)
    np.savez(tmp_path / "voices.npz", **{"expr-voice-4-f": style})
    calls = {}
    class Wrapper:
        @classmethod
        def set_library(cls, value):
            calls["library"] = value
        @classmethod
        def set_data_path(cls, value):
            calls["data"] = value
    class G2P:
        def __init__(self, **kwargs):
            calls["g2p_config"] = kwargs
        def phonemize(self, values):
            calls["g2p_inputs"] = values
            return ["ˈaɪ, "]
    class Session:
        def __init__(self, model, **kwargs):
            calls["session"] = (model, kwargs)
        def run(self, requested, inputs):
            calls["inputs"] = inputs
            return [np.ones((1, 6000), dtype=np.float32) * .25]
    modules = {
        "numpy": np,
        "onnxruntime": SimpleNamespace(SessionOptions=SimpleNamespace, InferenceSession=Session,
                                        ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL="sequential")),
        "phonemizer.backend": SimpleNamespace(EspeakBackend=G2P),
        "phonemizer.backend.espeak.wrapper": SimpleNamespace(EspeakWrapper=Wrapper),
        "espeakng_loader": SimpleNamespace(get_library_path=lambda: "local-dll", get_data_path=lambda: "local-data"),
    }
    monkeypatch.setattr(natural, "import_module", modules.__getitem__)
    return tmp_path, config, calls, style


def test_rosie_alias_speed_priors_tokens_style_cpu_and_full_tail(adapter_runtime):
    directory, _, calls, style = adapter_runtime
    adapter = natural._KittenAdapter(directory)
    output = adapter.synthesize("eye,")
    assert len(output) == 6000 and output[-1] == .25
    assert calls["g2p_config"] == {"language": "en-us", "preserve_punctuation": True, "with_stress": True}
    assert calls["g2p_inputs"] == ["eye,"]
    assert calls["library"] == "local-dll" and calls["data"] == "local-data"
    inputs = calls["inputs"]
    assert inputs["input_ids"].tolist() == [[0, 156, 43, 102, 16, 3, 10, 0]]
    assert inputs["input_ids"].dtype == np.int64
    assert inputs["speed"].dtype == np.float32 and float(inputs["speed"][0]) == pytest.approx(1.1)
    assert np.array_equal(inputs["style"], style[4:5])
    _, settings = calls["session"]
    assert settings["providers"] == ["CPUExecutionProvider"]
    assert settings["sess_options"].intra_op_num_threads == 2
    assert settings["sess_options"].inter_op_num_threads == 1
    assert settings["sess_options"].execution_mode == "sequential"


@pytest.mark.parametrize("change", [
    {"model_file": "../arbitrary.onnx"}, {"voices": "../arbitrary.npz"},
    {"voice_aliases": {"Rosie": "other-voice"}}, {"speed_priors": {"expr-voice-4-f": float("nan")}},
])
def test_mismatched_asset_config_is_rejected(adapter_runtime, change):
    directory, config, _, _ = adapter_runtime
    config.update(change)
    (directory / "config.json").write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(speech.PronunciationUnavailable):
        natural._KittenAdapter(directory)


def test_cache_size_budget_uses_shared_128mb_trimmer(runtime, monkeypatch):
    storage, _ = runtime
    monkeypatch.setattr(speech, "MAX_CACHE_BYTES", 100)
    result, _ = natural.synthesize_natural("eye", storage)
    assert speech._valid_wav(result)
    assert sum(path.stat().st_size for path in (storage / "natural-tts-audio").glob("*.wav")) <= 100
