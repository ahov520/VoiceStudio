"""CosyVoice adapter contracts that do not require loading model weights."""

import pytest

torch = pytest.importorskip("torch")

from services.tts_backend import CosyVoiceBackend  # noqa: E402


class CosyVoice3:
    sample_rate = 24000

    def __init__(self):
        self.calls = []

    def list_available_spks(self):
        return []

    def _result(self):
        return [{"tts_speech": torch.zeros(1, 4)}]

    def inference_zero_shot(self, *args, **kwargs):
        self.calls.append(("zero_shot", args, kwargs))
        return self._result()

    def inference_cross_lingual(self, *args, **kwargs):
        self.calls.append(("cross_lingual", args, kwargs))
        return self._result()

    def inference_instruct2(self, *args, **kwargs):
        self.calls.append(("instruct2", args, kwargs))
        return self._result()


class CosyVoice2(CosyVoice3):
    """Class name intentionally keeps the pre-CosyVoice3 grammar."""


def _backend(model):
    backend = CosyVoiceBackend()
    backend._model = model
    backend._ensure_loaded = lambda: None
    return backend


def test_cosyvoice3_zero_shot_puts_boundary_before_reference_transcript():
    model = CosyVoice3()
    _backend(model).generate(
        "要合成的正文", ref_audio="ref.wav", ref_text="参考音频里的话",
        language="Chinese",
    )

    name, args, kwargs = model.calls[0]
    assert name == "zero_shot"
    assert args[:2] == (
        "要合成的正文",
        "You are a helpful assistant.<|endofprompt|>参考音频里的话",
    )
    assert kwargs == {"stream": False}


def test_cosyvoice3_uses_cross_lingual_for_english_reference_and_chinese_text():
    model = CosyVoice3()
    _backend(model).generate(
        "你好，这是中文测试。",
        ref_audio="ref.wav",
        ref_text="Welcome to this course on agentic AI.",
        language="Chinese",
    )

    name, args, kwargs = model.calls[0]
    assert name == "cross_lingual"
    assert args[:2] == (
        "You are a helpful assistant.<|endofprompt|>你好，这是中文测试。",
        "ref.wav",
    )
    assert kwargs == {"stream": False}


def test_cosyvoice3_cross_lingual_uses_prompt_prefix_once():
    model = CosyVoice3()
    _backend(model).generate("你好，世界", ref_audio="ref.wav", language="Chinese")

    name, args, _kwargs = model.calls[0]
    assert name == "cross_lingual"
    assert args[0] == "You are a helpful assistant.<|endofprompt|>你好，世界"


def test_cosyvoice3_instruct_formats_prompt_without_touching_body():
    model = CosyVoice3()
    _backend(model).generate(
        "要合成的正文", ref_audio="ref.wav", instruct="用温柔的语气说",
    )

    name, args, _kwargs = model.calls[0]
    assert name == "instruct2"
    assert args[:2] == (
        "要合成的正文",
        "You are a helpful assistant. 用温柔的语气说<|endofprompt|>",
    )


def test_pre_cosyvoice3_cross_lingual_keeps_language_tag():
    model = CosyVoice2()
    _backend(model).generate("你好", ref_audio="ref.wav", language="Chinese")

    _name, args, _kwargs = model.calls[0]
    assert args[0] == "<|zh|>你好"


def test_cosyvoice_without_reference_rejects_missing_sft_speakers():
    model = CosyVoice3()
    backend = _backend(model)

    try:
        backend.generate("没有参考音频")
    except RuntimeError as exc:
        assert "Use voice cloning" in str(exc)
    else:
        raise AssertionError("CosyVoice must not invent a missing SFT speaker")


def test_cosyvoice_resolves_installed_hf_snapshot(monkeypatch, tmp_path):
    cache = tmp_path / "hf_cache"
    snapshot = (
        cache
        / "models--FunAudioLLM--Fun-CosyVoice3-0.5B-2512"
        / "snapshots"
        / "29e01c4e8d000f4bcd70751be16fa94bf3d85a18"
    )
    snapshot.mkdir(parents=True)
    (snapshot / "cosyvoice3.yaml").write_text("model", encoding="utf-8")
    monkeypatch.delenv("OMNIVOICE_COSYVOICE_MODEL", raising=False)
    monkeypatch.setenv("HF_HUB_CACHE", str(cache))
    monkeypatch.delenv("HF_HOME", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    assert CosyVoiceBackend._resolved_model_dir() == str(snapshot)
