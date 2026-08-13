"""Cloud TTS engines (services.tts_cloud) — OpenAI-compatible /v1/audio/speech
servers, ElevenLabs, and DashScope CosyVoice behind the standard TTSBackend
surface.

settings_store backed by in-memory dicts; every provider faked at its SDK/HTTP
boundary (openai.OpenAI, httpx.Client, dashscope SpeechSynthesizer) — no
network, no audio leaves the process. House convention per
test_asr_openai_compat_877.py: direct handler calls, no TestClient.
"""
from __future__ import annotations

import io
import os
import struct
import sys
import types
import wave

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

_HAS_TORCH = __import__("importlib").util.find_spec("torch") is not None
pytestmark = pytest.mark.skipif(not _HAS_TORCH, reason="torch not installed")


def _wav_bytes(sr=24000, n=2400, value=1000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(struct.pack("<" + "h" * n, *([value] * n)))
    return buf.getvalue()


@pytest.fixture
def ss(monkeypatch):
    from services import settings_store as _ss

    text: dict[str, str] = {}
    secrets: dict[str, str] = {}
    monkeypatch.setattr(_ss, "get_text", lambda k, default=None: text.get(k, default))
    monkeypatch.setattr(_ss, "set_text", lambda k, v: text.__setitem__(k, v))
    monkeypatch.setattr(_ss, "get_secret", lambda n: secrets.get(n))
    monkeypatch.setattr(
        _ss, "set_secret", lambda n, v: secrets.__setitem__(n, v) if v else secrets.pop(n, None)
    )
    monkeypatch.setattr(_ss, "list_secret_names", lambda: list(secrets))
    return _ss


@pytest.fixture
def tc(ss, monkeypatch):
    for var in (
        "TTS_OPENAI_COMPAT_BASE_URL", "TTS_OPENAI_COMPAT_MODEL",
        "TTS_OPENAI_COMPAT_VOICE", "TTS_OPENAI_COMPAT_API_KEY",
        "TTS_ELEVENLABS_VOICE_ID", "TTS_ELEVENLABS_MODEL_ID",
        "TTS_DASHSCOPE_MODEL", "TTS_DASHSCOPE_VOICE",
        "ELEVENLABS_API_KEY", "DASHSCOPE_API_KEY", "ELEVENLABS_BASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    import importlib
    return importlib.import_module("services.tts_cloud")


@pytest.fixture
def settings_mod(tc):
    import importlib
    return importlib.import_module("api.routers.settings")


# ── registry integration ────────────────────────────────────────────────────


def test_registered_in_tts_registry_with_install_hints(tc):
    from services import tts_backend

    for bid in ("openai-compat-tts", "elevenlabs-tts", "dashscope-tts"):
        assert bid in tts_backend._REGISTRY
        assert bid in tts_backend._INSTALL_HINTS
    assert tts_backend._REGISTRY["openai-compat-tts"] is tc.OpenAICompatTTSBackend
    assert tts_backend._REGISTRY["elevenlabs-tts"] is tc.ElevenLabsTTSBackend
    assert tts_backend._REGISTRY["dashscope-tts"] is tc.DashScopeTTSBackend


def test_cloud_engines_claim_no_gpu_and_no_cloning(tc):
    for cls in (tc.OpenAICompatTTSBackend, tc.ElevenLabsTTSBackend, tc.DashScopeTTSBackend):
        assert cls.gpu_compat == ("cpu",)
        assert cls.min_vram_gb == 0.0
        assert cls.supports_cloning is False


# ── availability gating ─────────────────────────────────────────────────────


def test_openai_compat_unavailable_without_base_url(tc):
    ok, msg = tc.OpenAICompatTTSBackend.is_available()
    assert ok is False and "Engines" in msg


def test_elevenlabs_and_dashscope_gate_on_provider_key(tc, ss):
    ok, msg = tc.ElevenLabsTTSBackend.is_available()
    assert ok is False and "ELEVENLABS_API_KEY" in msg
    ok, msg = tc.DashScopeTTSBackend.is_available()
    assert ok is False and "DASHSCOPE_API_KEY" in msg

    ss.set_secret("cloud_key.elevenlabs", "sk-11")
    assert tc.ElevenLabsTTSBackend.is_available() == (True, "ready")
    ss.set_secret("cloud_key.dashscope", "sk-ds")
    # dashscope package is installed in the test env, so key ⇒ ready.
    assert tc.DashScopeTTSBackend.is_available() == (True, "ready")


# ── OpenAI-compatible generate ──────────────────────────────────────────────


def _fake_openai_speech(monkeypatch, *, response_bytes=None, raise_exc=None):
    captured_kwargs = []
    calls = []

    class _FakeClient:
        def __init__(self, **kwargs):
            captured_kwargs.append(kwargs)
            self.audio = types.SimpleNamespace(
                speech=types.SimpleNamespace(create=self._create)
            )

        def _create(self, **kw):
            calls.append(kw)
            if raise_exc is not None:
                raise raise_exc
            return types.SimpleNamespace(content=response_bytes)

    import openai
    monkeypatch.setattr(openai, "OpenAI", _FakeClient)
    return captured_kwargs, calls


def test_openai_compat_generate_decodes_wav_to_mono_tensor(tc, ss, monkeypatch):
    ss.set_text(tc._TTS_OPENAI_COMPAT_BASE_URL_KEY, "http://localhost:8000/v1")
    captured_kwargs, calls = _fake_openai_speech(monkeypatch, response_bytes=_wav_bytes(n=2400))
    wav = tc.OpenAICompatTTSBackend().generate("hello world")
    assert wav.ndim == 2 and wav.shape[0] == 1 and wav.shape[1] == 2400
    assert captured_kwargs[0]["max_retries"] == 0
    assert calls[0]["model"] == "tts-1"
    assert calls[0]["voice"] == "alloy"
    assert calls[0]["response_format"] == "wav"
    assert "speed" not in calls[0]  # 1.0 is the server default — don't send it


def test_openai_compat_generate_passes_voice_override_and_speed(tc, ss, monkeypatch):
    ss.set_text(tc._TTS_OPENAI_COMPAT_BASE_URL_KEY, "http://localhost:8000/v1")
    _, calls = _fake_openai_speech(monkeypatch, response_bytes=_wav_bytes())
    tc.OpenAICompatTTSBackend().generate("hi", voice="anna", speed=1.5)
    assert calls[0]["voice"] == "anna"
    assert calls[0]["speed"] == 1.5


def test_openai_compat_generate_resamples_to_declared_rate(tc, ss, monkeypatch):
    ss.set_text(tc._TTS_OPENAI_COMPAT_BASE_URL_KEY, "http://localhost:8000/v1")
    _fake_openai_speech(monkeypatch, response_bytes=_wav_bytes(sr=48000, n=4800))
    wav = tc.OpenAICompatTTSBackend().generate("hi")
    # 4800 samples at 48 kHz = 0.1 s → 2400 samples at the declared 24 kHz.
    assert wav.shape[1] == 2400


def test_openai_compat_generate_wraps_errors_without_raw_leak(tc, ss, monkeypatch):
    ss.set_text(tc._TTS_OPENAI_COMPAT_BASE_URL_KEY, "http://localhost:8000/v1")
    _fake_openai_speech(monkeypatch, raise_exc=ConnectionError("connection refused"))
    with pytest.raises(RuntimeError) as ei:
        tc.OpenAICompatTTSBackend().generate("hi")
    assert "localhost:8000" in str(ei.value)
    assert "ConnectionError" in str(ei.value)


# ── ElevenLabs generate ─────────────────────────────────────────────────────


def _fake_httpx_post(monkeypatch, *, status_code=200, content=b"", text="", raise_exc=None):
    import httpx

    captured: dict = {}

    class _Resp:
        def __init__(self):
            self.status_code = status_code
            self.content = content
            self.text = text

    class _Client:
        def __init__(self, **kw):
            captured["client_kwargs"] = kw

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, params=None, headers=None, json=None, files=None, data=None):
            captured.update(url=url, params=params or {}, headers=headers or {},
                            json=json, files=files, data=data)
            if raise_exc is not None:
                raise raise_exc
            return _Resp()

    monkeypatch.setattr(httpx, "Client", _Client)
    return captured


def test_elevenlabs_generate_requests_pcm_and_decodes_it(tc, ss, monkeypatch):
    ss.set_secret("cloud_key.elevenlabs", "sk-11")
    pcm = struct.pack("<4h", 0, 16384, -16384, 0)
    captured = _fake_httpx_post(monkeypatch, content=pcm)
    wav = tc.ElevenLabsTTSBackend().generate("hello")
    assert wav.shape == (1, 4)
    assert abs(float(wav[0, 1]) - 0.5) < 1e-3
    assert captured["url"].endswith(f"/v1/text-to-speech/{tc._ELEVENLABS_DEFAULT_VOICE}")
    assert captured["params"] == {"output_format": "pcm_24000"}
    assert captured["headers"] == {"xi-api-key": "sk-11"}
    assert captured["json"]["model_id"] == "eleven_multilingual_v2"
    assert "voice_settings" not in captured["json"]  # speed 1.0 → not sent


def test_elevenlabs_generate_voice_override_and_clamped_speed(tc, ss, monkeypatch):
    ss.set_secret("cloud_key.elevenlabs", "sk-11")
    captured = _fake_httpx_post(monkeypatch, content=struct.pack("<2h", 0, 0))
    tc.ElevenLabsTTSBackend().generate("hi", voice="voice-abc", speed=2.0)
    assert "/v1/text-to-speech/voice-abc" in captured["url"]
    assert captured["json"]["voice_settings"] == {"speed": 1.2}  # clamped to API max


def test_elevenlabs_generate_auth_failure_names_the_fix(tc, ss, monkeypatch):
    ss.set_secret("cloud_key.elevenlabs", "sk-bad")
    _fake_httpx_post(monkeypatch, status_code=401)
    with pytest.raises(RuntimeError) as ei:
        tc.ElevenLabsTTSBackend().generate("hi")
    assert "Cloud providers" in str(ei.value)
    assert "sk-bad" not in str(ei.value)


def test_elevenlabs_voice_list_helper(tc, ss, monkeypatch):
    import httpx

    ss.set_secret("cloud_key.elevenlabs", "sk-11")

    class _Resp:
        status_code = 200

        def json(self):
            return {"voices": [
                {"voice_id": "v1", "name": "Rachel", "category": "premade"},
                {"name": "no-id-dropped"},
            ]}

    class _Client:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None):
            return _Resp()

    monkeypatch.setattr(httpx, "Client", _Client)
    out = tc.list_elevenlabs_voices()
    assert out["ok"] is True
    assert out["voices"] == [{"voice_id": "v1", "name": "Rachel", "category": "premade"}]


def test_elevenlabs_voice_list_not_configured(tc):
    assert tc.list_elevenlabs_voices() == {"ok": False, "status": "not_configured", "voices": []}


# ── DashScope generate ──────────────────────────────────────────────────────


def _fake_dashscope_synth(monkeypatch, *, response_bytes=None, raise_exc=None):
    import dashscope
    import dashscope.audio.tts_v2 as tts_v2

    captured: dict = {}

    class _FakeSynth:
        def __init__(self, **kw):
            captured["init"] = kw
            self.last_response = {"request_id": "req-1"}

        def call(self, text):
            captured["text"] = text
            if raise_exc is not None:
                raise raise_exc
            return response_bytes

    monkeypatch.setattr(tts_v2, "SpeechSynthesizer", _FakeSynth)
    return captured, dashscope


def test_dashscope_generate_blocking_call_returns_tensor(tc, ss, monkeypatch):
    ss.set_secret("cloud_key.dashscope", "sk-ds")
    captured, dashscope = _fake_dashscope_synth(monkeypatch, response_bytes=_wav_bytes(n=240))
    wav = tc.DashScopeTTSBackend().generate("你好", speed=1.4)
    assert wav.shape == (1, 240)
    assert dashscope.api_key == "sk-ds"
    assert captured["init"]["model"] == "cosyvoice-v2"
    assert captured["init"]["voice"] == "longxiaochun_v2"
    assert captured["init"]["speech_rate"] == 1.4
    assert captured["text"] == "你好"


def test_dashscope_generate_reads_configured_model_and_voice(tc, ss, monkeypatch):
    ss.set_secret("cloud_key.dashscope", "sk-ds")
    ss.set_text(tc._TTS_DASHSCOPE_MODEL_KEY, "cosyvoice-v3-flash")
    ss.set_text(tc._TTS_DASHSCOPE_VOICE_KEY, "longanhuan")
    captured, _ = _fake_dashscope_synth(monkeypatch, response_bytes=_wav_bytes(n=24))
    tc.DashScopeTTSBackend().generate("hi")
    assert captured["init"]["model"] == "cosyvoice-v3-flash"
    assert captured["init"]["voice"] == "longanhuan"


def test_dashscope_generate_empty_response_raises_actionable_error(tc, ss, monkeypatch):
    ss.set_secret("cloud_key.dashscope", "sk-ds")
    _fake_dashscope_synth(monkeypatch, response_bytes=None)
    with pytest.raises(RuntimeError) as ei:
        tc.DashScopeTTSBackend().generate("hi")
    assert "no audio" in str(ei.value)
    assert "sk-ds" not in str(ei.value)


# ── settings routes ─────────────────────────────────────────────────────────


def test_tts_openai_compat_route_roundtrip_never_echoes_key(settings_mod):
    st = settings_mod.set_tts_openai_compat(
        settings_mod._TTSOpenAICompatBody(
            base_url="http://localhost:8000/v1/", model="cosy", voice="anna",
            api_key="sk-test-9",
        )
    )
    assert st == {"base_url": "http://localhost:8000/v1", "model": "cosy",
                  "voice": "anna", "has_key": True}
    assert "sk-test-9" not in str(st)
    # None leaves values unchanged; '' clears the key
    st2 = settings_mod.set_tts_openai_compat(settings_mod._TTSOpenAICompatBody(api_key=""))
    assert st2["model"] == "cosy" and st2["has_key"] is False


def test_tts_openai_compat_route_rejects_schemeless_url(settings_mod):
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        settings_mod.set_tts_openai_compat(
            settings_mod._TTSOpenAICompatBody(base_url="localhost:8000/v1")
        )


def test_tts_elevenlabs_route_roundtrip(settings_mod, ss):
    st = settings_mod.get_tts_elevenlabs()
    assert st["voice_id"] and st["model_id"] and st["has_key"] is False
    st = settings_mod.set_tts_elevenlabs(
        settings_mod._TTSElevenLabsBody(voice_id="v-9", model_id="eleven_v3")
    )
    assert st["voice_id"] == "v-9" and st["model_id"] == "eleven_v3"


def test_tts_dashscope_route_roundtrip(settings_mod):
    st = settings_mod.set_tts_dashscope(
        settings_mod._TTSDashScopeBody(model="cosyvoice-v3-plus", voice="longanhuan")
    )
    assert st["model"] == "cosyvoice-v3-plus" and st["voice"] == "longanhuan"


def test_tts_openai_compat_probe_route_reads_persisted_config(settings_mod, tc, monkeypatch):
    import httpx

    settings_mod.set_tts_openai_compat(
        settings_mod._TTSOpenAICompatBody(base_url="http://localhost:9001/v1", api_key="sk-p")
    )
    captured: dict = {}

    class _Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"data": [{"id": "tts-1"}]}

    class _Client:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None):
            captured["url"] = url
            captured["headers"] = headers or {}
            return _Resp()

    monkeypatch.setattr(httpx, "Client", _Client)
    out = settings_mod.test_tts_openai_compat()
    assert out["ok"] is True and out["model_found"] is True
    assert captured["url"] == "http://localhost:9001/v1/models"
    assert captured["headers"]["Authorization"] == "Bearer sk-p"
