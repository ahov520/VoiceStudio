"""Contracts for the dependency-isolated CosyVoice adapter."""

from __future__ import annotations

import base64

import pytest

torch = pytest.importorskip("torch")

from engines.cosyvoice import CosyVoiceBackend  # noqa: E402
from engines.cosyvoice import bootstrap  # noqa: E402
from engines.cosyvoice import main as sidecar_main  # noqa: E402
from services import tts_backend  # noqa: E402


def test_registry_uses_the_isolated_backend():
    backend_cls = tts_backend._REGISTRY["cosyvoice"]
    assert backend_cls is CosyVoiceBackend
    assert backend_cls.runs_out_of_process is True
    assert backend_cls.id == "cosyvoice"


def test_generate_forwards_only_supported_clone_options(monkeypatch):
    captured = {}

    def fake_generate(self, text, **kwargs):
        captured.update(text=text, kwargs=kwargs)
        return torch.zeros(1, 4)

    monkeypatch.setattr("services.subprocess_backend.SubprocessBackend.generate", fake_generate)
    backend = CosyVoiceBackend()
    result = backend.generate(
        "你好", ref_audio="ref.wav", ref_text="hello", instruct="温柔地说",
        language="Chinese", speed=1.8, num_step=4,
    )

    assert tuple(result.shape) == (1, 4)
    assert captured == {
        "text": "你好",
        "kwargs": {
            "ref_audio": "ref.wav", "ref_text": "hello",
            "instruct": "温柔地说", "language": "Chinese",
        },
    }


def test_sidecar_pcm_conversion_is_mono_int16():
    pcm_b64, n_samples = sidecar_main._pcm_from_tensor(torch.tensor([[0.0, 0.5, -0.5]]))
    assert n_samples == 3
    assert len(base64.b64decode(pcm_b64)) == 3 * 2


def test_bootstrap_pins_the_known_cosvoice_compatibility_stack():
    assert bootstrap._BOOTSTRAP_DEPS == [
        "transformers==4.51.3",
        "numpy==1.26.4",
        "onnxruntime==1.18.0",
        "wetext==0.0.4",
        "x-transformers==2.11.24",
    ]


def test_bootstrap_accepts_a_venv_path(monkeypatch, tmp_path):
    venv = tmp_path / "compat-venv"
    python = venv / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setenv("OMNIVOICE_COSYVOICE_DIR", str(venv))
    paths = bootstrap._probe_paths()
    assert paths[0] == python
