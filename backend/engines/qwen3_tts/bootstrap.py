"""Qwen3-TTS venv probe + lazy bootstrap.

The parent needs the sidecar interpreter to spawn. Unlike the source-clone
engines (IndexTTS, MOSS-v1.5), Qwen3-TTS needs no checkout — the
transformers path is pure PyPI deps (torch + transformers + soundfile +
scipy). Probe order:

    1. ``${OMNIVOICE_QWEN3_TTS_DIR}/.venv/`` (or ``Scripts\\python.exe`` on
       Windows). Highest priority: power users who built their own env get
       zero migration cost.
    2. ``backend/engines/qwen3_tts/.venv/`` — this package's owned venv,
       created by step 3 when needed.
    3. Bootstrap: ``uv venv`` + ``uv pip install`` the transformers-mode
       deps into step-2's venv. Requires ``uv`` on PATH.

GGUF mode needs no Python venv at all: the sidecar shells out to llama.cpp's
``llama-tts`` binary (``OMNIVOICE_QWEN3_GGUF_BIN`` or PATH). The backend's
``is_available`` routes on that.

The import probe is tri-state (``yes``/``no``/``unproven``) via
``engines._venv_probe`` — a cold torch import can exceed 10 s; a timeout
proves nothing and must not discard a working venv (#1414).
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

from engines._venv_probe import ProbeResult, log_safe, venv_can_import

logger = logging.getLogger("omnivoice.qwen3_tts.bootstrap")

#: Absolute path to the sidecar entrypoint.
QWEN3_SIDECAR_SCRIPT: Path = Path(__file__).parent / "main.py"

#: This package's owned venv (Probe 2 / bootstrap target).
_ENGINES_VENV_DIR: Path = Path(__file__).parent / ".venv"

_UV_VENV_TIMEOUT_S = 120
_UV_PIP_INSTALL_TIMEOUT_S = 1800  # torch is a big wheel on a cold cache

#: Transformers-mode deps installed into the managed venv. GGUF mode skips
#: the venv entirely (llama-tts is a binary).
_BOOTSTRAP_DEPS = [
    "torch",
    "transformers",
    "soundfile",
    "scipy",
    "numpy",
]

#: Import probe: transformers + torch are the only hard requirements.
_PROBE_SNIPPET = (
    "import torch\n"
    "import transformers\n"
    "from transformers import Qwen3TTSForConditionalGeneration, AutoProcessor\n"
)


def invalidate() -> None:
    """Clear the resolved-python cache. Tests call this between scenarios."""
    global _resolved_python
    _resolved_python = None


_resolved_python: Optional[Path] = None


def _venv_python(venv_dir: Path) -> Path:
    """Python executable path inside a venv (cross-platform layout)."""
    from services.sidecar_install import _venv_python as _canonical
    return _canonical(venv_dir)


def _probe_paths() -> list[Path]:
    out: list[Path] = []
    omv_dir = os.environ.get("OMNIVOICE_QWEN3_TTS_DIR")
    if omv_dir:
        out.append(_venv_python(Path(omv_dir) / ".venv"))
    out.append(_venv_python(_ENGINES_VENV_DIR))
    return out


def is_installed() -> bool:
    """Cheap existence check (every Settings render calls this)."""
    return any(cand.is_file() for cand in _probe_paths())


def _venv_can_import(python_path: Path) -> ProbeResult:
    return venv_can_import(python_path, _PROBE_SNIPPET)


def _locate_uv() -> Optional[str]:
    import shutil
    uv = os.environ.get("OMNIVOICE_UV_BIN") or shutil.which("uv")
    if not uv and Path(sys.executable).parent.joinpath("uv.exe").is_file():
        # App-private uv install (source builds) sits beside the backend python.
        uv = str(Path(sys.executable).parent.joinpath("uv.exe"))
    return uv


def _uv_env() -> "dict[str, str] | None":
    """uv cache co-location for installs on a non-system volume."""
    from services.sidecar_install import uv_subprocess_env
    return uv_subprocess_env(_ENGINES_VENV_DIR.parent.parent)


def _bootstrap_managed_venv() -> Path:
    """Create engines/qwen3_tts/.venv and install the transformers-mode deps."""
    uv = _locate_uv()
    if not uv:
        raise RuntimeError(
            "uv is required to bootstrap the Qwen3-TTS venv but was not found "
            "on PATH. Install uv, or set OMNIVOICE_QWEN3_TTS_DIR to an "
            "existing venv you created yourself (see docs/engines/qwen3-tts.md)."
        )
    logger.info(
        "Bootstrapping Qwen3-TTS venv at %s (torch download can take minutes)",
        _ENGINES_VENV_DIR,
    )
    try:
        subprocess.run(
            [uv, "venv", str(_ENGINES_VENV_DIR)],
            check=True, timeout=_UV_VENV_TIMEOUT_S, capture_output=True,
            env=_uv_env(),
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"uv venv failed for Qwen3-TTS at {_ENGINES_VENV_DIR}: "
            f"{exc.stderr.decode('utf-8', errors='replace') if exc.stderr else exc}"
        ) from exc
    python_path = _venv_python(_ENGINES_VENV_DIR)
    try:
        subprocess.run(
            [uv, "pip", "install", "--python", str(python_path), * _BOOTSTRAP_DEPS],
            check=True, timeout=_UV_PIP_INSTALL_TIMEOUT_S, capture_output=True,
            env=_uv_env(),
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "Qwen3-TTS bootstrap installed deps but `import transformers` "
            "failed. See docs/engines/qwen3-tts.md. stderr: "
            f"{exc.stderr.decode('utf-8', errors='replace')[:800] if exc.stderr else exc}"
        ) from exc
    return python_path


def resolve_venv() -> Path:
    """Resolve the sidecar interpreter, bootstrapping the managed venv if needed."""
    global _resolved_python
    if _resolved_python is not None:
        return _resolved_python
    unproven: Optional[Path] = None
    for cand in _probe_paths():
        if cand.is_file():
            verdict = _venv_can_import(cand)
            if verdict == "yes":
                logger.info("Qwen3-TTS venv resolved: %s", cand)
                _resolved_python = cand
                return cand
            if verdict == "unproven" and unproven is None:
                unproven = cand
    if unproven is not None:
        logger.warning(
            "Qwen3-TTS import probe timed out for %s; using it as fallback",
            unproven,
        )
        _resolved_python = unproven
        return unproven
    cand = _bootstrap_managed_venv()
    if _venv_can_import(cand) == "no":
        raise RuntimeError(
            "Qwen3-TTS bootstrap completed but `import transformers` failed "
            f"from {cand}. See docs/engines/qwen3-tts.md."
        )
    _resolved_python = cand
    return cand
