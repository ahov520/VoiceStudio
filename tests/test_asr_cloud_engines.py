"""Cloud ASR engines (services.asr_cloud) — ElevenLabs Scribe and DashScope
sync recognition, adapted into the app's Whisper-style
``{chunks, segments, language}`` shape.

settings_store in-memory, providers faked at the HTTP/SDK boundary (no
network). House convention per test_asr_openai_compat_877.py.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")


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
def ac(ss, monkeypatch):
    for var in ("ELEVENLABS_API_KEY", "DASHSCOPE_API_KEY", "ELEVENLABS_BASE_URL",
                "ASR_ELEVENLABS_MODEL_ID", "ASR_DASHSCOPE_MODEL"):
        monkeypatch.delenv(var, raising=False)
    import importlib
    return importlib.import_module("services.asr_cloud")


# ── registry integration ────────────────────────────────────────────────────


def test_registered_in_asr_registry_with_install_hints(ac):
    from services import asr_backend

    for bid in ("elevenlabs-asr", "dashscope-asr"):
        assert bid in asr_backend._REGISTRY
        assert bid in asr_backend._INSTALL_HINTS
    assert asr_backend._REGISTRY["elevenlabs-asr"] is ac.ElevenLabsASRBackend
    assert asr_backend._REGISTRY["dashscope-asr"] is ac.DashScopeASRBackend


def test_cloud_asr_never_wins_auto_detect(ac, ss, monkeypatch):
    """Cloud transcription is explicit opt-in: even fully configured, the
    auto-detect ladder must keep resolving to a local engine family."""
    from services import asr_backend

    ss.set_secret("cloud_key.elevenlabs", "sk-11")
    ss.set_secret("cloud_key.dashscope", "sk-ds")
    picked = asr_backend._auto_detect()
    assert picked not in ("elevenlabs-asr", "dashscope-asr")


# ── ElevenLabs Scribe ───────────────────────────────────────────────────────


def test_scribe_words_group_into_sentence_segments(ac):
    words = [
        {"text": "Hello", "type": "word", "start": 0.0, "end": 0.4},
        {"text": " ", "type": "spacing"},
        {"text": "world.", "type": "word", "start": 0.5, "end": 1.0},
        {"text": "(laughter)", "type": "audio_event", "start": 1.0, "end": 1.5},
        {"text": "Next", "type": "word", "start": 1.6, "end": 2.0},
        {"text": " ", "type": "spacing"},
        {"text": "part", "type": "word", "start": 2.1, "end": 2.4},
    ]
    segs = ac._scribe_words_to_segments(words)
    assert [s["text"] for s in segs] == ["Hello world.", "Next part"]
    assert segs[0]["start"] == 0.0 and segs[0]["end"] == 1.0
    assert segs[0]["words"][0] == {"word": "Hello", "start": 0.0, "end": 0.4}
    assert segs[1]["start"] == 1.6 and segs[1]["end"] == 2.4


def test_scribe_words_split_on_long_silence(ac):
    words = [
        {"text": "one", "type": "word", "start": 0.0, "end": 0.5},
        {"text": "two", "type": "word", "start": 2.0, "end": 2.5},  # 1.5 s gap
    ]
    segs = ac._scribe_words_to_segments(words)
    assert [s["text"] for s in segs] == ["one", "two"]


def test_language_code_normalization(ac):
    assert ac._normalize_language("eng") == "en"
    assert ac._normalize_language("cmn") == "zh"
    assert ac._normalize_language("zh") == "zh"
    assert ac._normalize_language(None) == "en"


def test_elevenlabs_asr_gates_on_key(ac):
    ok, msg = ac.ElevenLabsASRBackend.is_available()
    assert ok is False and "ELEVENLABS_API_KEY" in msg


def _fake_httpx_post(monkeypatch, *, status_code=200, json_body=None, text="", raise_exc=None):
    import httpx

    captured: dict = {}

    class _Resp:
        def __init__(self):
            self.status_code = status_code
            self.text = text

        def json(self):
            if json_body is None:
                raise ValueError("no json")
            return json_body

    class _Client:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, files=None, data=None):
            captured.update(url=url, headers=headers or {}, files=files, data=data or {})
            if raise_exc is not None:
                raise raise_exc
            return _Resp()

    monkeypatch.setattr(httpx, "Client", _Client)
    return captured


def test_elevenlabs_transcribe_adapts_words_and_language(ac, ss, monkeypatch, tmp_path):
    ss.set_secret("cloud_key.elevenlabs", "sk-11")
    body = {
        "language_code": "eng",
        "text": "Hello world.",
        "words": [
            {"text": "Hello", "type": "word", "start": 0.0, "end": 0.4},
            {"text": " ", "type": "spacing"},
            {"text": "world.", "type": "word", "start": 0.5, "end": 1.0},
        ],
    }
    captured = _fake_httpx_post(monkeypatch, json_body=body)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF....WAVEfmt ")
    out = ac.ElevenLabsASRBackend().transcribe(str(audio))
    assert out["language"] == "en"
    assert out["segments"] == [{
        "text": "Hello world.", "start": 0.0, "end": 1.0,
        "words": [
            {"word": "Hello", "start": 0.0, "end": 0.4},
            {"word": "world.", "start": 0.5, "end": 1.0},
        ],
    }]
    assert out["chunks"] == [{"text": "Hello world.", "timestamp": (0.0, 1.0)}]
    assert captured["url"].endswith("/v1/speech-to-text")
    assert captured["headers"] == {"xi-api-key": "sk-11"}
    assert captured["data"]["model_id"] == "scribe_v2"
    assert captured["data"]["timestamps_granularity"] == "word"


def test_elevenlabs_transcribe_text_only_fallback(ac, ss, monkeypatch, tmp_path):
    ss.set_secret("cloud_key.elevenlabs", "sk-11")
    _fake_httpx_post(monkeypatch, json_body={"language_code": "spa", "text": "hola", "words": []})
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF....WAVEfmt ")
    out = ac.ElevenLabsASRBackend().transcribe(str(audio))
    assert out["segments"] == [{"text": "hola", "start": 0.0, "end": None, "words": []}]
    assert out["language"] == "es"


def test_elevenlabs_transcribe_auth_failure_is_actionable(ac, ss, monkeypatch, tmp_path):
    ss.set_secret("cloud_key.elevenlabs", "sk-bad")
    _fake_httpx_post(monkeypatch, status_code=401)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF....WAVEfmt ")
    with pytest.raises(RuntimeError) as ei:
        ac.ElevenLabsASRBackend().transcribe(str(audio))
    assert "Cloud providers" in str(ei.value)
    assert "sk-bad" not in str(ei.value)


# ── DashScope adaptation ────────────────────────────────────────────────────


def test_dashscope_asr_gates_on_key(ac):
    ok, msg = ac.DashScopeASRBackend.is_available()
    assert ok is False and "DASHSCOPE_API_KEY" in msg


def test_dashscope_adapt_sentences_with_ms_timestamps_and_offset(ac):
    output = {
        "output": {
            "sentences": [
                {
                    "text": "你好。", "begin_time": 100, "end_time": 900,
                    "words": [
                        {"text": "你好", "begin_time": 100, "end_time": 600},
                    ],
                },
                {"text": "第二句。", "begin_time": 1000, "end_time": 2000},
            ]
        }
    }
    segs, lang = ac.DashScopeASRBackend._adapt_output(output, offset_s=240.0, chunk_dur_s=240.0)
    assert lang is None
    assert segs[0]["text"] == "你好。"
    assert segs[0]["start"] == pytest.approx(240.1)
    assert segs[0]["end"] == pytest.approx(240.9)
    assert segs[0]["words"] == [
        {"word": "你好", "start": pytest.approx(240.1), "end": pytest.approx(240.6)},
    ]
    assert segs[1]["start"] == pytest.approx(241.0)


def test_dashscope_adapt_single_sentence_shape(ac):
    output = {"output": {"sentence": {"text": "hello", "begin_time": 0, "end_time": 500}}}
    segs, _ = ac.DashScopeASRBackend._adapt_output(output, 0.0, 10.0)
    assert segs == [{"text": "hello", "start": 0.0, "end": 0.5, "words": []}]


def test_dashscope_adapt_choices_shape_carries_language_annotation(ac):
    output = {
        "choices": [{
            "message": {
                "content": [{"text": "plain text"}],
                "annotations": [{"type": "audio_info", "language": "zh"}],
            }
        }]
    }
    segs, lang = ac.DashScopeASRBackend._adapt_output(output, 240.0, 120.0)
    assert lang == "zh"
    # Text-only results anchor to the chunk bounds so downstream segmentation
    # still sees a monotonic timeline.
    assert segs == [{"text": "plain text", "start": 240.0, "end": 360.0, "words": []}]


def test_dashscope_adapt_bare_text_fallback(ac):
    segs, _ = ac.DashScopeASRBackend._adapt_output({"text": "raw"}, 0.0, 5.0)
    assert segs == [{"text": "raw", "start": 0.0, "end": 5.0, "words": []}]


def test_dashscope_transcribe_merges_chunks_with_offsets(ac, ss, monkeypatch, tmp_path):
    ss.set_secret("cloud_key.dashscope", "sk-ds")
    c1 = tmp_path / "chunk0000.wav"
    c2 = tmp_path / "chunk0001.wav"
    # pcm_s16le mono 16 kHz: 44-byte header + 2 bytes/sample.
    c1.write_bytes(b"\x00" * (44 + 2 * 16000 * 240))  # 240 s
    c2.write_bytes(b"\x00" * (44 + 2 * 16000 * 10))   # 10 s
    monkeypatch.setattr(
        ac.DashScopeASRBackend, "_split_to_chunks",
        staticmethod(lambda audio_path, out_dir: [str(c1), str(c2)]),
    )
    outputs = {
        str(c1): {"output": {"sentence": {"text": "part one", "begin_time": 0, "end_time": 1000}}},
        str(c2): {"output": {"sentence": {"text": "part two", "begin_time": 0, "end_time": 2000}}},
    }
    monkeypatch.setattr(
        ac.DashScopeASRBackend, "_call_chunk", lambda self, chunk: outputs[chunk]
    )
    out = ac.DashScopeASRBackend().transcribe(str(tmp_path / "input.wav"))
    assert [s["text"] for s in out["segments"]] == ["part one", "part two"]
    assert out["segments"][0]["start"] == pytest.approx(0.0)
    assert out["segments"][1]["start"] == pytest.approx(240.0)  # second chunk offset
    assert out["segments"][1]["end"] == pytest.approx(242.0)
    assert out["chunks"][1]["timestamp"] == (
        pytest.approx(240.0), pytest.approx(242.0),
    )


def test_dashscope_call_chunk_error_is_scrubbed(ac, ss, monkeypatch, tmp_path):
    ss.set_secret("cloud_key.dashscope", "sk-ds")
    import dashscope

    class _Resp:
        status_code = 401
        code = "InvalidApiKey"
        message = "key sk-ds rejected"
        output = None

    monkeypatch.setattr(
        dashscope, "MultiModalConversation",
        type("MMC", (), {"call": staticmethod(lambda **kw: _Resp())}),
    )
    with pytest.raises(RuntimeError) as ei:
        ac.DashScopeASRBackend()._call_chunk(str(tmp_path / "c.wav"))
    assert "401" in str(ei.value)
    assert "InvalidApiKey" in str(ei.value)


def test_dashscope_call_chunk_sends_local_audio_message(ac, ss, monkeypatch, tmp_path):
    ss.set_secret("cloud_key.dashscope", "sk-ds")
    import dashscope

    captured: dict = {}

    class _Resp:
        status_code = 200
        output = {"text": "ok"}

    def _call(**kw):
        captured.update(kw)
        return _Resp()

    monkeypatch.setattr(
        dashscope, "MultiModalConversation",
        type("MMC", (), {"call": staticmethod(_call)}),
    )
    chunk = str(tmp_path / "c.wav")
    out = ac.DashScopeASRBackend()._call_chunk(chunk)
    assert out == {"text": "ok"}
    assert captured["model"] == "qwen-audio-3.0-asr-flash"
    assert captured["messages"] == [{"role": "user", "content": [{"audio": chunk}]}]
    assert dashscope.api_key == "sk-ds"
