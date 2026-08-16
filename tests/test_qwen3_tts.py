"""Tests for the Qwen3-TTS 1.7B engine (transformers / GGUF Q8 modes).

Qwen3-TTS runs in a dedicated subprocess venv (transformers path) or via
llama.cpp's llama-tts binary (GGUF path), so these tests never import the
model itself. They exercise the parent-side wiring that ships in the default
install: registry resolution, subprocess isolation, mode arbitration,
hardware honesty, and the generate() kwarg normalization. No network, no
optional deps, no subprocess spawn.
"""
from __future__ import annotations

import importlib

import pytest


# ── registry wiring ────────────────────────────────────────────────────────


def test_registry_contains_qwen3_tts():
    from services.tts_backend import _REGISTRY, get_backend_class

    assert "qwen3-tts" in _REGISTRY, (
        "_REGISTRY is missing 'qwen3-tts'; check _LAZY_REGISTRY in "
        "services/tts_backend.py"
    )
    cls = _REGISTRY["qwen3-tts"]
    assert cls.__name__ == "Qwen3TTSBackend"
    assert get_backend_class("qwen3-tts") is cls
    assert getattr(cls, "_is_subprocess_isolated", False), (
        "Qwen3TTSBackend should be subprocess-isolated"
    )
    for name in ("is_available", "generate", "sample_rate", "supported_languages"):
        assert hasattr(cls, name), f"Qwen3TTSBackend missing {name!r}"


def test_pep562_lazy_import():
    mod = importlib.import_module("services.tts_backend")
    cls = mod._REGISTRY["qwen3-tts"]
    assert cls.__name__ == "Qwen3TTSBackend"


def test_install_hint_present():
    from services.tts_backend import _INSTALL_HINTS, _SETUP_SNIPPETS

    hint = _INSTALL_HINTS.get("qwen3-tts", "")
    assert "OMNIVOICE_QWEN3_GGUF_BIN" in hint
    assert "Apache-2.0" in hint
    snippet = _SETUP_SNIPPETS.get("qwen3-tts", "")
    assert "OMNIVOICE_QWEN3_TTS_DIR" in snippet


def test_sidecar_script_ships():
    from engines.qwen3_tts.bootstrap import QWEN3_SIDECAR_SCRIPT

    assert QWEN3_SIDECAR_SCRIPT.name == "main.py"
    assert QWEN3_SIDECAR_SCRIPT.is_file()


# ── mode arbitration ───────────────────────────────────────────────────────


def test_mode_defaults_to_transformers(monkeypatch):
    from engines.qwen3_tts import _mode

    monkeypatch.delenv("OMNIVOICE_QWEN3_TTS_MODE", raising=False)
    monkeypatch.delenv("OMNIVOICE_QWEN3_GGUF_BIN", raising=False)
    monkeypatch.delenv("OMNIVOICE_QWEN3_GGUF_MODEL", raising=False)
    assert _mode() == "transformers"


def test_mode_gguf_when_binary_configured(monkeypatch):
    from engines.qwen3_tts import _mode

    monkeypatch.delenv("OMNIVOICE_QWEN3_TTS_MODE", raising=False)
    monkeypatch.setenv("OMNIVOICE_QWEN3_GGUF_BIN", "/usr/local/bin/llama-tts")
    assert _mode() == "gguf"


def test_mode_env_override(monkeypatch):
    from engines.qwen3_tts import _mode

    monkeypatch.setenv("OMNIVOICE_QWEN3_TTS_MODE", "transformers")
    monkeypatch.setenv("OMNIVOICE_QWEN3_GGUF_BIN", "/usr/local/bin/llama-tts")
    assert _mode() == "transformers"  # explicit env wins over auto-detect


# ── availability honesty ───────────────────────────────────────────────────


def test_is_available_gguf_without_binary_is_false(monkeypatch):
    from engines.qwen3_tts import Qwen3TTSBackend

    monkeypatch.setenv("OMNIVOICE_QWEN3_TTS_MODE", "gguf")
    monkeypatch.setenv("OMNIVOICE_QWEN3_GGUF_BIN", "/nonexistent/llama-tts")
    monkeypatch.setattr("engines.qwen3_tts._gguf_binary", lambda: None)
    ok, msg = Qwen3TTSBackend.is_available()
    assert not ok
    assert "OMNIVOICE_QWEN3_GGUF_BIN" in msg


def test_is_available_transformers_without_venv_is_false(monkeypatch):
    from engines.qwen3_tts import Qwen3TTSBackend
    from engines.qwen3_tts import bootstrap

    monkeypatch.delenv("OMNIVOICE_QWEN3_TTS_MODE", raising=False)
    monkeypatch.delenv("OMNIVOICE_QWEN3_GGUF_BIN", raising=False)
    monkeypatch.delenv("OMNIVOICE_QWEN3_GGUF_MODEL", raising=False)
    monkeypatch.delenv("OMNIVOICE_QWEN3_TTS_DIR", raising=False)
    monkeypatch.setattr(bootstrap, "is_installed", lambda: False)
    ok, msg = Qwen3TTSBackend.is_available()
    assert not ok
    assert "OMNIVOICE_QWEN3_TTS_DIR" in msg


# ── hardware + metadata ────────────────────────────────────────────────────


def test_metadata():
    from engines.qwen3_tts import Qwen3TTSBackend

    assert Qwen3TTSBackend.gpu_compat == ("cuda", "mps", "cpu")
    assert Qwen3TTSBackend._DEFAULT_SAMPLE_RATE == 24000
    assert Qwen3TTSBackend.supports_voice_design is False
    langs = Qwen3TTSBackend.supported_languages.fget(None)
    assert "zh" in langs
    assert "en" in langs


def test_generate_normalizes_language_and_pins_mode(monkeypatch):
    from services import subprocess_backend
    from engines.qwen3_tts import Qwen3TTSBackend

    captured = {}

    def _fake_generate(self, text, **kw):
        captured.update(kw)
        return "ok"

    monkeypatch.setattr(subprocess_backend.SubprocessBackend, "generate", _fake_generate)
    inst = Qwen3TTSBackend()
    # language 'zh-CN' -> 'zh' (GGUF --tts-lang table); mode pinned.
    out = inst.generate("你好", language="zh-CN", ref_audio="/tmp/a.wav")
    assert out == "ok"
    assert captured["language"] == "zh"
    assert captured["mode"] == "transformers"
