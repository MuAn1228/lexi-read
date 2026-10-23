"""Offline speech tests use fake model samples and isolated temporary caches."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import wave

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

np = pytest.importorskip("numpy")

from backend.api import pronunciation_routes as routes
from backend.auth import get_current_user_id
from backend.config import Settings
from backend.database import get_session
from backend.services import pronunciation as speech


class FakeEngine:
    def __init__(self):
        self.calls = []

    def synthesize(self, text, *, syn_config):
        self.calls.append((text, {"length_scale": syn_config.length_scale}))
        return [SimpleNamespace(audio_float_array=np.array([-.5, 0, .5] * 80, dtype=np.float32),
                                sample_rate=22_050, sample_width=2, sample_channels=1)]


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, _env_file=None)
    model_dir = tmp_path / "tts"
    model_dir.mkdir()
    for path in speech._model_paths(tmp_path):
        path.write_bytes(b"fake optional model")
    monkeypatch.setattr(speech, "get_settings", lambda: settings)
    monkeypatch.setattr(speech, "_dependency_problem", lambda: None)
    engine = FakeEngine()
    monkeypatch.setattr(speech, "_get_engine", lambda *args: speech._PiperAdapter(engine, SimpleNamespace))
    return settings, engine


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user_id] = lambda: 1
    with TestClient(app) as test_client:
        yield test_client


def test_status_only_checks_files_and_dependencies_without_loading_or_writing(runtime, monkeypatch):
    settings, _ = runtime
    monkeypatch.setattr(speech, "_get_engine", lambda *args: pytest.fail("status loaded a model"))
    assert speech.pronunciation_status() == {"available": True, "engine": "piper", "reason": None}
    assert not (settings.storage_dir / "tts-audio").exists()


def test_missing_model_and_dependency_have_explicit_system_fallback(runtime, monkeypatch):
    settings, _ = runtime
    speech._model_paths(settings.storage_dir)[0].unlink()
    status = speech.pronunciation_status()
    assert status["available"] is False and status["engine"] == "system"
    assert "模型" in status["reason"]
    monkeypatch.setattr(speech, "_dependency_problem", lambda: "缺少可选依赖")
    assert speech.pronunciation_status()["reason"] == "缺少可选依赖"
    with pytest.raises(speech.PronunciationUnavailable, match="缺少可选依赖"):
        speech.synthesize("hello", "en-US")


def test_dependency_probe_does_not_import_the_runtime(monkeypatch):
    observed = []
    monkeypatch.setattr(speech.util, "find_spec", lambda name: observed.append(name) or object())
    monkeypatch.setattr(speech, "import_module", lambda *args: pytest.fail("dependency probe imported runtime"))
    assert speech._dependency_problem() is None
    assert observed == ["numpy", "onnxruntime", "piper"]
    monkeypatch.setattr(speech.util, "find_spec", lambda name: None)
    assert speech._dependency_problem()


@pytest.mark.parametrize("text", ["", "   ", "12345!?", "中文", "hello 中文", "привет", "hello\x00", "hello\x1b", "he\u200bllo", "hello\ud800", "a" * 4001])
def test_invalid_text_is_rejected_before_model_work(runtime, text):
    _, engine = runtime
    with pytest.raises(ValueError):
        speech.synthesize(text, "en-US")
    assert engine.calls == []


def test_normalization_preserves_english_and_typography():
    assert speech.normalize_text("  It’s\n a café.\t ") == "It’s a café."


def test_long_text_chunks_stay_bounded_and_in_original_order():
    text = "A brief sentence. " + "This is a long English clause, " * 30 + "A final sentence."
    text = speech.normalize_text(text)
    chunks = speech.split_text(text)
    assert len(chunks) > 2
    assert all(0 < len(chunk) <= speech.MAX_CHUNK_LENGTH for chunk in chunks)
    assert " ".join(chunks) == text
    assert all(len(chunk) <= speech.MAX_CHUNK_LENGTH for chunk in speech.split_text("a" * 901))


@pytest.mark.parametrize("accent,voice", [("en-US", "en_US-ljspeech-medium"), ("en-GB", "en_GB-cori-medium")])
def test_fixed_natural_speed_voice_and_pcm_wav_format(runtime, accent, voice):
    _, engine = runtime
    data, selected_voice = speech.synthesize("Curious", accent)
    assert selected_voice == voice
    assert engine.calls == [("Curious", {"length_scale": 1.0})]
    with wave.open(BytesIO(data), "rb") as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 22_050)
        assert audio.getnframes() == 240
        assert len(audio.readframes(1000)) == 480


def test_piper_adapter_keeps_sentence_order_and_uses_normal_length_scale():
    chunks = [SimpleNamespace(audio_float_array=np.array([value], dtype=np.float32),
                              sample_rate=22_050, sample_width=2, sample_channels=1)
              for value in (.1, .2, .3)]
    calls = []
    def synthesize(text, *, syn_config):
        calls.append((text, syn_config.length_scale))
        return iter(chunks)
    adapter = speech._PiperAdapter(SimpleNamespace(synthesize=synthesize), SimpleNamespace)
    samples, rate = adapter.synthesize("First. Second. Third.", "en-US")
    assert calls == [("First. Second. Third.", 1.0)]
    assert np.allclose(samples, [.1, .2, .3]) and rate == 22_050
    chunks[1].sample_rate = 48_000
    with pytest.raises(speech.PronunciationUnavailable):
        adapter.synthesize("First. Second. Third.", "en-US")


def test_success_is_cached_and_new_service_call_reuses_without_loading(runtime, monkeypatch):
    settings, engine = runtime
    data, voice = speech.synthesize("  curious\n", "en-US")
    files = list((settings.storage_dir / "tts-audio").glob("*.wav"))
    assert len(files) == 1 and files[0].read_bytes() == data
    assert not list(files[0].parent.glob("*.part"))
    monkeypatch.setattr(speech, "_get_engine", lambda *args: pytest.fail("cache hit loaded model"))
    assert speech.synthesize("curious", "en-US") == (data, voice)
    assert len(engine.calls) == 1


def test_accent_text_and_model_identity_separate_cache_entries(runtime):
    settings, engine = runtime
    speech.synthesize("curious", "en-US")
    speech.synthesize("curious", "en-GB")
    speech.synthesize("explore", "en-US")
    model = speech._model_paths(settings.storage_dir)[0]
    model.write_bytes(b"a changed fake model version")
    speech.synthesize("curious", "en-US")
    assert len(engine.calls) == 4
    assert len(list((settings.storage_dir / "tts-audio").glob("*.wav"))) == 4


@pytest.mark.parametrize("bad_cache", [b"not an audio", b"RIFF\x00\x00\x00\x00WAVE", b""])
def test_corrupt_cache_is_regenerated(runtime, bad_cache):
    settings, engine = runtime
    valid, _ = speech.synthesize("curious", "en-US")
    file = next((settings.storage_dir / "tts-audio").glob("*.wav"))
    file.write_bytes(bad_cache)
    assert speech.synthesize("curious", "en-US")[0] == valid
    assert len(engine.calls) == 2 and speech._valid_wav(file.read_bytes())


def test_truncated_pcm_is_not_accepted(runtime):
    valid, _ = speech.synthesize("curious", "en-US")
    assert speech._valid_wav(valid)
    assert not speech._valid_wav(valid[:-2])
    truncated = bytearray(valid[:-2])
    truncated[4:8] = (len(truncated) - 8).to_bytes(4, "little")
    assert not speech._valid_wav(bytes(truncated))


@pytest.mark.parametrize("samples,rate", [(np.array([]), 22_050), (np.array([np.nan]), 22_050), (np.array([[1, 2]]), 22_050), (np.array([.5]), 48_000)])
def test_invalid_model_audio_is_never_cached(runtime, monkeypatch, samples, rate):
    settings, engine = runtime
    adapter = SimpleNamespace(synthesize=lambda *args, **kwargs: (samples, rate))
    monkeypatch.setattr(speech, "_get_engine", lambda *args: adapter)
    with pytest.raises(speech.PronunciationUnavailable):
        speech.synthesize("curious", "en-US")
    assert not (settings.storage_dir / "tts-audio").exists()


def test_runtime_error_returns_no_audio_and_releases_the_slot(runtime, monkeypatch):
    settings, engine = runtime
    def fail(*args, **kwargs):
        raise RuntimeError("do not disclose private model internals")
    monkeypatch.setattr(engine, "synthesize", fail)
    with pytest.raises(speech.PronunciationUnavailable) as error:
        speech.synthesize("curious", "en-US")
    assert "private" not in str(error.value)
    assert not speech._inference_lock.locked()
    assert not (settings.storage_dir / "tts-audio").exists()


def test_only_one_cold_inference_runs_but_cached_audio_bypasses_busy_slot(runtime, monkeypatch):
    settings, engine = runtime
    cached, _ = speech.synthesize("cached", "en-US")
    entered, finish = Event(), Event()
    original = engine.synthesize
    def block(text, **kwargs):
        entered.set()
        assert finish.wait(timeout=10)
        return original(text, **kwargs)
    monkeypatch.setattr(engine, "synthesize", block)
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(speech.synthesize, "first cold", "en-US")
        try:
            assert entered.wait(timeout=10)
            with pytest.raises(speech.PronunciationBusy):
                speech.synthesize("second cold", "en-US")
            assert speech.synthesize("cached", "en-US")[0] == cached
        finally:
            finish.set()
        assert speech._valid_wav(pending.result(timeout=10)[0])
    assert not speech._inference_lock.locked()


def test_disk_cache_cap_removes_old_wavs_and_preserves_unrelated_files(tmp_path, monkeypatch):
    monkeypatch.setattr(speech, "MAX_CACHE_BYTES", 9)
    first, second, third = [tmp_path / f"{index}.wav" for index in range(3)]
    for index, path in enumerate((first, second, third)):
        path.write_bytes(b"1234")
        import os
        os.utime(path, (100 + index, 100 + index))
    unrelated = tmp_path / "keep.txt"
    unrelated.write_text("unchanged")
    speech._trim_cache(tmp_path)
    assert not first.exists() and second.exists() and third.exists()
    assert unrelated.read_text() == "unchanged"


def test_unwritable_cache_does_not_discard_good_audio(runtime, monkeypatch):
    settings, engine = runtime
    (settings.storage_dir / "tts-audio").write_text("not a directory")
    data, _ = speech.synthesize("curious", "en-US")
    assert speech._valid_wav(data)


def test_onnx_session_has_bounded_cpu_threads_and_is_lazy_singleton(runtime, monkeypatch):
    settings, _ = runtime
    # Restore the actual function replaced by the runtime fixture.
    original_get_engine = _ORIGINAL_GET_ENGINE
    sessions, creations = [], []
    class Options:
        pass
    def session(*args, **kwargs):
        sessions.append((args, kwargs))
        return object()
    def create_voice(**kwargs):
        creations.append(kwargs)
        return object()
    for path in speech._model_paths(settings.storage_dir):
        if path.name.endswith(".json"):
            path.write_text('{"audio":{"sample_rate":22050}}')
    modules = {"onnxruntime": SimpleNamespace(SessionOptions=Options, InferenceSession=session,
                 ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL="sequential")),
               "piper": SimpleNamespace(PiperConfig=SimpleNamespace(from_dict=lambda data: SimpleNamespace(sample_rate=data["audio"]["sample_rate"])),
                                         PiperVoice=create_voice, SynthesisConfig=SimpleNamespace)}
    monkeypatch.setattr(speech, "import_module", lambda name: modules[name])
    monkeypatch.setattr(speech, "_engine", None)
    monkeypatch.setattr(speech, "_engine_identity", None)
    identity = speech._file_identity(settings.storage_dir)
    first = original_get_engine(settings.storage_dir, identity, "en-US")
    assert original_get_engine(settings.storage_dir, identity, "en-US") is first
    assert len(sessions) == len(creations) == 1
    options = sessions[0][1]["sess_options"]
    assert options.intra_op_num_threads == 2 and options.inter_op_num_threads == 1
    assert sessions[0][1]["providers"] == ["CPUExecutionProvider"]
    assert str(sessions[0][0][0]).endswith("en_US-ljspeech-medium.onnx")
    assert creations[0]["config"].sample_rate == 22_050
    assert isinstance(first, speech._PiperAdapter)
    assert original_get_engine(settings.storage_dir, identity, "en-GB") is not first
    assert len(sessions) == 2 and str(sessions[1][0][0]).endswith("en_GB-cori-medium.onnx")


_ORIGINAL_GET_ENGINE = speech._get_engine


def test_api_status_and_wav_headers(client, runtime):
    status = client.get("/api/pronunciation/status")
    assert status.status_code == 200 and status.json()["engine"] == "piper"
    assert status.headers["cache-control"] == "private, no-store"
    response = client.post("/api/pronunciation", json={"text": "curious", "accent": "en-GB"})
    assert response.status_code == 200 and response.headers["content-type"] == "audio/wav"
    assert response.headers["x-pronunciation-voice"] == "en_GB-cori-medium"
    assert response.headers["cache-control"] == "private, no-store"
    assert speech._valid_wav(response.content)


@pytest.mark.parametrize("payload", [{}, {"text": ""}, {"text": " "}, {"text": 3}, {"text": "hello", "accent": "zh-CN"},
                                      {"text": "中文"}, {"text": "a" * 4001}, {"text": "hello\x00"}])
def test_api_bad_payloads_do_not_call_engine(client, runtime, payload):
    _, engine = runtime
    response = client.post("/api/pronunciation", json=payload)
    assert response.status_code == 422
    assert engine.calls == []


def test_api_unavailable_and_busy_are_not_silent_audio(client, runtime, monkeypatch):
    monkeypatch.setattr(speech, "_dependency_problem", lambda: "缺少可选组件")
    response = client.post("/api/pronunciation", json={"text": "hello"})
    assert response.status_code == 503 and response.json()["detail"] == "缺少可选组件"
    monkeypatch.setattr(speech, "_dependency_problem", lambda: None)
    assert speech._inference_lock.acquire(blocking=False)
    try:
        response = client.post("/api/pronunciation", json={"text": "hello"})
        assert response.status_code == 429 and response.headers["retry-after"] == "1"
    finally:
        speech._inference_lock.release()


def test_public_auth_runs_before_speech_and_never_opens_models(tmp_path, monkeypatch):
    settings = Settings(app_mode="public", cors_origins="https://reader.example", data_dir=tmp_path, _env_file=None)
    monkeypatch.setattr("backend.auth.get_settings", lambda: settings)
    monkeypatch.setattr(routes, "pronunciation_status", lambda: pytest.fail("unauthenticated status reached runtime"))
    monkeypatch.setattr(routes, "synthesize", lambda *args: pytest.fail("unauthenticated synthesis reached runtime"))
    app = FastAPI()
    app.include_router(routes.router)
    def session():
        yield SimpleNamespace(get=lambda *args: None)
    app.dependency_overrides[get_session] = session
    with TestClient(app) as public_client:
        for headers in ({}, {"Authorization": "Bearer invalid"}, {"Authorization": "Bearer " + "a" * 43}):
            assert public_client.get("/api/pronunciation/status", headers=headers).status_code == 401
            assert public_client.post("/api/pronunciation", headers=headers, json={"text": "hello"}).status_code == 401
    assert list(tmp_path.iterdir()) == []


def test_main_app_registers_the_router():
    from backend.main import app
    paths = set(app.openapi()["paths"])
    assert "/api/pronunciation" in paths and "/api/pronunciation/status" in paths
