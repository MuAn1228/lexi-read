"""Offline speech tests use fake model samples and isolated temporary caches."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from io import BytesIO
import json
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
    monkeypatch.setattr("backend.services.word_recordings.record_word", lambda *args, **kwargs: None)
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
    assert speech.pronunciation_status() == {"available": True, "engine": "piper", "reason": None, "natural_available": False}
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
def test_clear_word_cadence_voice_and_pcm_wav_format(runtime, accent, voice):
    _, engine = runtime
    data, selected_voice = speech.synthesize("Curious", accent)
    assert selected_voice == voice
    assert engine.calls == [("Curious", {"length_scale": 1.35})]
    with wave.open(BytesIO(data), "rb") as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 22_050)
        expected_frames = round(.35 * 22_050) + 240 + round(.20 * 22_050)
        assert audio.getnframes() == expected_frames
        assert len(audio.readframes(expected_frames + 1)) == expected_frames * 2


@pytest.mark.parametrize("text,scale", [
    ("curious", 1.35), ("Curious.", 1.35), ("hello!", 1.35), ("why?", 1.35),
    ("well-being", 1.35), ("don't", 1.35), ("don’t", 1.35), ("don‘t", 1.35),
    ("readers'", 1.35), ("readers’", 1.35), ("It's.", 1.35),
    ("a curious reader", 1.18), ("Read this slowly.", 1.18), ("Hello. Again.", 1.18),
])
def test_word_and_sentence_profiles_change_timing_without_changing_text(runtime, text, scale):
    _, engine = runtime
    speech.synthesize(text, "en-US")
    assert engine.calls == [(text, {"length_scale": scale})]


def test_piper_adapter_keeps_sentence_order_and_adds_only_sentence_boundary_pauses():
    chunks = [SimpleNamespace(audio_float_array=np.array([value], dtype=np.float32),
                              sample_rate=22_050, sample_width=2, sample_channels=1)
              for value in (.1, .2, .3)]
    calls = []
    def synthesize(text, *, syn_config):
        calls.append((text, syn_config.length_scale))
        return iter(chunks)
    adapter = speech._PiperAdapter(SimpleNamespace(synthesize=synthesize), SimpleNamespace)
    samples, rate = adapter.synthesize("First. Second. Third.", "en-US")
    assert calls == [("First. Second. Third.", 1.18)]
    pause_frames = round(.18 * 22_050)
    expected = np.concatenate([np.array([.1], dtype=np.float32), np.zeros(pause_frames, dtype=np.float32),
                               np.array([.2], dtype=np.float32), np.zeros(pause_frames, dtype=np.float32),
                               np.array([.3], dtype=np.float32)])
    assert np.array_equal(samples, expected) and rate == 22_050
    chunks[1].sample_rate = 48_000
    with pytest.raises(speech.PronunciationUnavailable):
        adapter.synthesize("First. Second. Third.", "en-US")


def test_start_and_end_protection_preserves_all_original_pcm_samples(runtime, monkeypatch):
    _, engine = runtime
    # Deliberate first/last nonzero samples expose clipping, trimming or fades.
    original = np.array([.75, -.25, 0, .125, -.5], dtype=np.float32)
    monkeypatch.setattr(engine, "synthesize", lambda *args, **kwargs: [SimpleNamespace(
        audio_float_array=original, sample_rate=22_050, sample_width=2, sample_channels=1)])
    data, _ = speech.synthesize("complete", "en-US")
    with wave.open(BytesIO(data), "rb") as audio:
        actual = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
    leading_frames, trailing_frames = round(.35 * 22_050), round(.20 * 22_050)
    assert len(actual) == leading_frames + len(original) + trailing_frames
    assert np.count_nonzero(actual[:leading_frames]) == 0
    assert np.count_nonzero(actual[-trailing_frames:]) == 0
    expected_pcm = (original * 32767).astype("<i2")
    assert np.array_equal(actual[leading_frames:-trailing_frames], expected_pcm)
    assert actual[leading_frames] == expected_pcm[0]
    assert actual[-trailing_frames - 1] == expected_pcm[-1]


def test_long_clause_keeps_chunk_order_with_exact_boundary_pauses(runtime):
    _, engine = runtime
    text = "This is one continuous English clause with several words " * 14
    text = speech.normalize_text(text)
    chunks = speech.split_text(text)
    assert len(chunks) >= 3
    data, _ = speech.synthesize(text, "en-US")
    assert engine.calls == [(chunk, {"length_scale": 1.18}) for chunk in chunks]
    original_pcm = (np.array([-.5, 0, .5] * 80, dtype=np.float32) * 32767).astype("<i2")
    expected_parts = [np.zeros(round(.35 * 22_050), dtype="<i2")]
    for index in range(len(chunks)):
        if index:
            expected_parts.append(np.zeros(round(.18 * 22_050), dtype="<i2"))
        expected_parts.append(original_pcm)
    expected_parts.append(np.zeros(round(.20 * 22_050), dtype="<i2"))
    with wave.open(BytesIO(data), "rb") as audio:
        actual = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
    assert np.array_equal(actual, np.concatenate(expected_parts))


def test_protection_silence_is_counted_in_the_audio_size_limit(runtime, monkeypatch):
    settings, engine = runtime
    padding_bytes = (round(.35 * 22_050) + round(.20 * 22_050)) * 2
    monkeypatch.setattr(speech, "MAX_AUDIO_BYTES", padding_bytes + 240 * 2 + 43)
    with pytest.raises(speech.PronunciationUnavailable, match="音频过长"):
        speech.synthesize("curious", "en-US")
    assert len(engine.calls) == 1
    assert not (settings.storage_dir / "tts-audio").exists()


def test_success_is_cached_and_new_service_call_reuses_without_loading(runtime, monkeypatch):
    settings, engine = runtime
    data, voice = speech.synthesize("  curious\n", "en-US")
    files = list((settings.storage_dir / "tts-audio").glob("*.wav"))
    assert len(files) == 1 and files[0].read_bytes() == data
    assert not list(files[0].parent.glob("*.part"))
    monkeypatch.setattr(speech, "_get_engine", lambda *args: pytest.fail("cache hit loaded model"))
    assert speech.synthesize("curious", "en-US") == (data, voice)
    assert len(engine.calls) == 1


def test_old_unprotected_cache_is_not_reused(runtime):
    settings, engine = runtime
    identity = speech._file_identity(settings.storage_dir)
    assert identity[0] == "piper-pcm22050-clear-v2"
    previous_identity = ("piper-pcm22050-v1", *identity[1:])
    old_key = sha256(json.dumps([previous_identity, "curious", "en-US"], ensure_ascii=True).encode("ascii")).hexdigest()
    old_path = settings.storage_dir / "tts-audio" / f"{old_key}.wav"
    old_path.parent.mkdir()
    output = BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(22_050)
        audio.writeframes(b"\x00\x40" * 240)
    old_data = output.getvalue()
    assert speech._valid_wav(old_data)
    old_path.write_bytes(old_data)

    new_data, _ = speech.synthesize("curious", "en-US")
    assert len(engine.calls) == 1 and new_data != old_data
    assert old_path.read_bytes() == old_data
    assert len(list(old_path.parent.glob("*.wav"))) == 2
    assert speech.synthesize("curious", "en-US")[0] == new_data
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
    response = client.post("/api/pronunciation", json={"text": "curious", "accent": "en-GB", "allow_word_synthesis": True})
    assert response.status_code == 200 and response.headers["content-type"] == "audio/wav"
    assert response.headers["x-pronunciation-voice"] == "en_GB-cori-medium"
    assert response.headers["cache-control"] == "private, no-store"
    assert speech._valid_wav(response.content)


@pytest.mark.parametrize("payload", [{}, {"text": ""}, {"text": " "}, {"text": 3}, {"text": "hello", "accent": "zh-CN"},
                                      {"text": "中文"}, {"text": "a" * 4001}, {"text": "hello\x00"},
                                      {"text": "hello", "allow_word_synthesis": "false"},
                                      {"text": "hello", "allow_word_synthesis": 1}])
def test_api_bad_payloads_do_not_call_engine(client, runtime, payload):
    _, engine = runtime
    response = client.post("/api/pronunciation", json=payload)
    assert response.status_code == 422
    assert engine.calls == []


def test_api_unavailable_and_busy_are_not_silent_audio(client, runtime, monkeypatch):
    monkeypatch.setattr(speech, "_dependency_problem", lambda: "缺少可选组件")
    response = client.post("/api/pronunciation", json={"text": "hello", "allow_word_synthesis": True})
    assert response.status_code == 503 and response.json()["detail"] == "缺少可选组件"
    monkeypatch.setattr(speech, "_dependency_problem", lambda: None)
    assert speech._inference_lock.acquire(blocking=False)
    try:
        response = client.post("/api/pronunciation", json={"text": "hello", "allow_word_synthesis": True})
        assert response.status_code == 429 and response.headers["retry-after"] == "1"
    finally:
        speech._inference_lock.release()


def test_public_auth_runs_before_speech_and_never_opens_models(tmp_path, monkeypatch):
    settings = Settings(app_mode="public", cors_origins="https://reader.example", data_dir=tmp_path, _env_file=None)
    monkeypatch.setattr("backend.auth.get_settings", lambda: settings)
    monkeypatch.setattr(routes, "pronunciation_status", lambda: pytest.fail("unauthenticated status reached runtime"))
    monkeypatch.setattr(routes, "synthesize_with_source", lambda *args: pytest.fail("unauthenticated synthesis reached runtime"))
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


@pytest.mark.parametrize("word", ["hello", "readers’", "curious...", "a" * 81])
def test_words_without_recordings_never_implicitly_synthesize(client, runtime, monkeypatch, word):
    _, engine = runtime
    calls = []
    def unavailable(text, accent, storage_dir, **kwargs):
        calls.append((text, accent, kwargs["deadline"]))
        return None
    monkeypatch.setattr("backend.services.word_recordings.record_word", unavailable)
    response = client.post("/api/pronunciation", json={"text": word, "accent": "en-US"})
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "recording_not_found"
    assert response.headers["cache-control"] == "private, no-store"
    assert [call[1] for call in calls] == ["en-US", "en-GB"]
    assert calls[0][2] == calls[1][2]  # Both accents share one remote deadline.
    assert not engine.calls


def test_other_accent_recording_is_labeled_and_not_synthesized(client, runtime, monkeypatch):
    _, engine = runtime
    credits = {"author": "A Reader", "license": "CC BY-SA 4.0",
               "source_url": "https://commons.wikimedia.org/wiki/File:Example.wav",
               "license_url": "https://creativecommons.org/licenses/by-sa/4.0/"}
    def lookup(text, accent, storage_dir, **kwargs):
        return None if accent == "en-US" else SimpleNamespace(audio=b"recorded-word", attribution=credits)
    monkeypatch.setattr("backend.services.word_recordings.record_word", lookup)
    response = client.post("/api/pronunciation", json={"text": "headmaster", "accent": "en-US"})
    assert response.status_code == 200 and response.content == b"recorded-word"
    assert response.headers["x-pronunciation-source"] == "dictionary"
    assert response.headers["x-pronunciation-accent"] == "en-GB"
    assert json.loads(response.headers["x-pronunciation-attribution"]) == credits
    assert not engine.calls


def test_sentences_keep_local_speech_without_dictionary_network(client, runtime, monkeypatch):
    monkeypatch.setattr("backend.services.word_recordings.record_word", lambda *args, **kwargs: pytest.fail("sentence looked up as a word"))
    response = client.post("/api/pronunciation", json={"text": "A quiet garden."})
    assert response.status_code == 200
    assert response.headers["x-pronunciation-source"] == "piper"


def test_enabled_new_voice_is_explicitly_american_and_not_a_recording(client, runtime, monkeypatch):
    monkeypatch.setattr("backend.services.natural_speech.natural_status", lambda storage: (True, None))
    monkeypatch.setattr("backend.services.natural_speech.synthesize_natural",
                        lambda text, storage: (b"rosie-audio", "kitten-micro-0.8-rosie"))
    response = client.post("/api/pronunciation", json={"text": "metacognition", "accent": "en-GB", "allow_word_synthesis": True})
    assert response.status_code == 200 and response.content == b"rosie-audio"
    assert response.headers["x-pronunciation-source"] == "kitten"
    assert response.headers["x-pronunciation-voice"] == "kitten-micro-0.8-rosie"
    assert response.headers["x-pronunciation-accent"] == "en-US"
    assert not response.headers["x-pronunciation-attribution"]
    assert not runtime[1].calls


def test_new_voice_does_not_bypass_the_word_synthesis_preference(client, runtime, monkeypatch):
    monkeypatch.setattr("backend.services.natural_speech.natural_status", lambda storage: (True, None))
    monkeypatch.setattr("backend.services.natural_speech.synthesize_natural", lambda *args: pytest.fail("strict word synthesized"))
    response = client.post("/api/pronunciation", json={"text": "metacognition"})
    assert response.status_code == 404 and not runtime[1].calls


def test_failed_new_voice_never_silently_uses_the_old_voice(client, runtime, monkeypatch):
    monkeypatch.setattr("backend.services.natural_speech.natural_status", lambda storage: (True, None))
    def fail(*args):
        raise speech.PronunciationUnavailable("新声线暂不可用，请重试。")
    monkeypatch.setattr("backend.services.natural_speech.synthesize_natural", fail)
    response = client.post("/api/pronunciation", json={"text": "metacognition", "allow_word_synthesis": True})
    assert response.status_code == 503 and not runtime[1].calls


def test_new_voice_status_does_not_require_piper(client, runtime, monkeypatch):
    monkeypatch.setattr("backend.services.natural_speech.natural_status", lambda storage: (True, None))
    monkeypatch.setattr(speech, "_dependency_problem", lambda: "Piper未安装")
    response = client.get("/api/pronunciation/status")
    assert response.json() == {"available": True, "engine": "kitten", "reason": None, "natural_available": True}
