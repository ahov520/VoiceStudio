"""CosyVoice 3 subprocess adapter.

CosyVoice's official requirements pin packages which cannot share the
VoiceStudio parent interpreter (notably transformers 4.51, numpy 1.26,
onnxruntime 1.18, wetext 0.0.4, and x-transformers 2.11).  The public engine
keeps the normal TTSBackend contract while the actual model runs in a child
process with a dedicated venv.  The sidecar reuses the already-installed
CosyVoice package and torch from the app environment, so the RTX/CUDA build is
not replaced or duplicated.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from services.subprocess_backend import SubprocessBackend

if TYPE_CHECKING:
    import torch  # noqa: F401

logger = logging.getLogger("omnivoice.cosyvoice")


def _cosyvoice_files_present() -> bool:
    """Locate package files without importing the incompatible stack."""
    configured = (os.environ.get("OMNIVOICE_COSYVOICE_DIR") or "").strip()
    candidates = []
    if configured:
        candidates.append(Path(configured))
    project_venv = Path(__file__).resolve().parents[2].parent / ".venv"
    candidates.extend([
        project_venv / "Lib" / "site-packages",
        project_venv / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages",
    ])
    return any((root / "cosyvoice" / "cli" / "cosyvoice.py").is_file() for root in candidates)


class CosyVoiceBackend(SubprocessBackend):
    """CosyVoice 3 with dependency isolation and killable inference."""

    id = "cosyvoice"
    display_name = "CosyVoice 3 (9 langs, zero-shot, isolated venv, Apache-2.0)"
    supports_cloning = True
    gpu_compat = ("cuda", "cpu")
    _DEFAULT_SAMPLE_RATE = 24000

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        # The package may come from the app venv or from a user-provided
        # CosyVoice clone.  Do not import the heavy model here; this probe is
        # called frequently while rendering the engine picker.
        try:
            from cosyvoice.cli.cosyvoice import AutoModel  # noqa: F401
        except Exception as exc:  # noqa: BLE001 - probe must never crash picker
            if not _cosyvoice_files_present():
                return False, (
                    "cosyvoice package is unavailable in the app environment "
                    f"({type(exc).__name__}: {exc}). Install the official "
                    "CosyVoice clone, or set OMNIVOICE_COSYVOICE_DIR to its "
                    "root. The model directory is configured with "
                    "OMNIVOICE_COSYVOICE_MODEL."
                )

        from engines.cosyvoice.bootstrap import COSYVOICE_SIDECAR_SCRIPT
        if not COSYVOICE_SIDECAR_SCRIPT.exists():
            return False, f"CosyVoice sidecar script missing at {COSYVOICE_SIDECAR_SCRIPT}"

        if not cls._venv_is_present():
            return True, "ready — a private compatible environment will be prepared on first use"
        return True, "ready"

    @classmethod
    def _venv_is_present(cls) -> bool:
        from engines.cosyvoice.bootstrap import is_installed
        return is_installed()

    @classmethod
    def venv_python(cls) -> Path:
        from engines.cosyvoice.bootstrap import resolve_cosyvoice_venv
        return resolve_cosyvoice_venv()

    @classmethod
    def sidecar_script(cls) -> Path:
        from engines.cosyvoice.bootstrap import COSYVOICE_SIDECAR_SCRIPT
        return COSYVOICE_SIDECAR_SCRIPT

    @property
    def sample_rate(self) -> int:
        return self._DEFAULT_SAMPLE_RATE

    @property
    def supported_languages(self) -> list[str]:
        return ["zh", "en", "ja", "ko", "yue", "de", "es", "fr", "it", "ru"]

    @property
    def recv_timeout_s(self) -> float:
        # The first request can load a 0.5B checkpoint.  Keep the child alive
        # while that legitimate cold load completes; the outer generation
        # budget still bounds the request and a later request can kill a hung
        # process through the same watchdog.
        try:
            return max(120.0, float(os.environ.get("OMNIVOICE_COSYVOICE_RECV_TIMEOUT_S", "900")))
        except (TypeError, ValueError):
            return 900.0

    def generate(self, text: str, **kw) -> "torch.Tensor":
        forwarded = {"ref_audio": kw.get("ref_audio"), "ref_text": kw.get("ref_text")}
        for key in ("instruct", "language"):
            if kw.get(key) is not None:
                forwarded[key] = kw[key]
        return super().generate(text, **forwarded)


__all__ = ["CosyVoiceBackend"]
