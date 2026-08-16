"""Qwen3-TTS 1.7B adapter (Apache-2.0) — subprocess-isolated sidecar.

Qwen3-TTS-12Hz-1.7B-Base is Qwen's open multilingual TTS with zero-shot
voice cloning from a reference clip. It ships two inference paths:

  * **transformers** (default) — official HF checkpoint via
    ``Qwen3TTSForConditionalGeneration`` in a dedicated venv (torch +
    transformers + soundfile + scipy), isolated from the parent's pins.
  * **gguf** — llama.cpp's ``llama-tts`` binary with a Q8_0 quantised GGUF
    (e.g. ``cstr/qwen3-tts-1.7b-base-GGUF`` / ``ggml-org/...-GGUF``), for
    low-VRAM hosts. Set ``OMNIVOICE_QWEN3_GGUF_BIN`` (binary path) and
    optionally ``OMNIVOICE_QWEN3_GGUF_MODEL`` (local .gguf); voice cloning
    rides ``--tts-speaker-file``.

The backend class lives HERE (not in ``services.tts_backend``) because it
imports ``services.subprocess_backend``, which imports
``services.tts_backend`` for ``TTSBackend`` — defining the class in the
package breaks the import cycle (same reason as IndexTTS2).
"""
from __future__ import annotations

import logging
import os
import shutil
from typing import TYPE_CHECKING

from services.subprocess_backend import SubprocessBackend

if TYPE_CHECKING:
    import torch  # noqa: F401

logger = logging.getLogger("omnivoice.qwen3_tts")

#: Languages the GGUF path can pass to --tts-lang (the Qwen3-TTS set).
_GGUF_LANGS = {
    "zh", "en", "de", "it", "pt", "es", "ja", "ko", "fr", "ru",
    "chinese", "english", "german", "italian", "portuguese",
    "spanish", "japanese", "korean", "french", "russian",
}


def _mode() -> str:
    """gguf when a binary/model is configured, else transformers."""
    env = (os.environ.get("OMNIVOICE_QWEN3_TTS_MODE") or "auto").lower()
    if env in ("gguf", "llama.cpp", "llamacpp"):
        return "gguf"
    if env in ("transformers", "torch", "pt"):
        return "transformers"
    if os.environ.get("OMNIVOICE_QWEN3_GGUF_BIN") or os.environ.get("OMNIVOICE_QWEN3_GGUF_MODEL"):
        return "gguf"
    return "transformers"


def _gguf_binary() -> str | None:
    return os.environ.get("OMNIVOICE_QWEN3_GGUF_BIN") or shutil.which("llama-tts")


class Qwen3TTSBackend(SubprocessBackend):
    """Qwen3-TTS 1.7B — subprocess sidecar, transformers or GGUF Q8 backend."""

    id = "qwen3-tts"
    display_name = "Qwen3-TTS 1.7B (Apache-2.0, multilingual, zero-shot clone)"
    supports_voice_design = False  # needs a reference clip for timbre
    supports_emotion = False
    _DEFAULT_SAMPLE_RATE = 24000
    gpu_compat = ("cuda", "mps", "cpu")

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        # NEVER import the model here: the transformers path lives in the
        # sidecar venv, not this interpreter. Cheap disk/binary checks only.
        mode = _mode()
        if mode == "gguf":
            if not _gguf_binary():
                return False, (
                    "llama-tts binary not found. Set OMNIVOICE_QWEN3_GGUF_BIN "
                    "to its path (build llama.cpp's tts tool), or switch to "
                    "the transformers path (OMNIVOICE_QWEN3_TTS_MODE=transformers). "
                    "See docs/engines/qwen3-tts.md."
                )
            return True, "ok"
        from engines.qwen3_tts.bootstrap import QWEN3_SIDECAR_SCRIPT, is_installed
        if not is_installed():
            return False, (
                "Qwen3-TTS venv not found. Set OMNIVOICE_QWEN3_TTS_DIR to an "
                "existing venv, or install uv and let VoiceStudio bootstrap "
                "one automatically on first use. See docs/engines/qwen3-tts.md."
            )
        if not QWEN3_SIDECAR_SCRIPT.exists():
            return False, (
                f"Qwen3-TTS sidecar script missing at {QWEN3_SIDECAR_SCRIPT} — "
                "reinstall VoiceStudio."
            )
        return True, "ok"

    @classmethod
    def venv_python(cls):
        from engines.qwen3_tts.bootstrap import resolve_venv
        return resolve_venv()

    @classmethod
    def sidecar_script(cls):
        from engines.qwen3_tts.bootstrap import QWEN3_SIDECAR_SCRIPT
        return QWEN3_SIDECAR_SCRIPT

    @property
    def sample_rate(self) -> int:
        return self._DEFAULT_SAMPLE_RATE

    @property
    def supported_languages(self) -> list[str]:
        # The GGUF path's --tts-lang table; transformers mode covers the same
        # set (auto-detected from text). Stable regardless of mode.
        return ["zh", "en", "de", "it", "pt", "es", "ja", "ko", "fr", "ru"]

    def generate(self, text: str, **kw) -> "torch.Tensor":
        # Normalize the public kwargs into the sidecar payload. The base
        # class forwards every JSON-safe kwarg verbatim; we only pin the
        # mode so a stale env var can't flip mid-run.
        kw["mode"] = _mode()
        if kw.get("language"):
            lang = str(kw["language"]).lower().replace("_", "-").split("-")[0]
            kw["language"] = lang if lang in _GGUF_LANGS else None
        return super().generate(text, **kw)
