"""CosyVoice compatibility-venv resolver.

The upstream repository has no pyproject suitable for VoiceStudio's generic
source installer.  We therefore provision only the conflicting Python wheels
into a small managed venv and reuse the app's torch/CosyVoice package.  A user
clone's ``.venv`` is preferred when it already contains the pinned stack.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

from core.config import DATA_DIR
from engines._venv_probe import ProbeResult, log_safe, venv_can_import

logger = logging.getLogger("omnivoice.cosyvoice.bootstrap")

COSYVOICE_SIDECAR_SCRIPT: Path = Path(__file__).parent / "main.py"
_MANAGED_VENV_DIR = Path(DATA_DIR) / "engines" / "cosyvoice" / ".venv"
_UV_VENV_TIMEOUT_S = 120
_UV_PIP_INSTALL_TIMEOUT_S = 1800

# These are the versions required by CosyVoice 3's official requirements.
# torch is intentionally absent: the app's CUDA build is reused by the
# sidecar, which avoids a second multi-GB wheel and preserves GPU support.
_BOOTSTRAP_DEPS = [
    "transformers==4.51.3",
    "numpy==1.26.4",
    "onnxruntime==1.18.0",
    "wetext==0.0.4",
    "x-transformers==2.11.24",
]

_PROBE_SNIPPET = (
    "import numpy, onnxruntime, transformers, wetext, x_transformers\n"
    "assert numpy.__version__.startswith('1.26.')\n"
    "assert transformers.__version__.startswith('4.51.')\n"
)

_resolved_python: Optional[Path] = None


def invalidate() -> None:
    global _resolved_python
    _resolved_python = None


def _venv_python(venv_dir: Path) -> Path:
    from services.sidecar_install import _venv_python as canonical
    return canonical(venv_dir)


def _probe_paths() -> list[Path]:
    out: list[Path] = []
    configured = (os.environ.get("OMNIVOICE_COSYVOICE_DIR") or "").strip()
    if configured:
        root = Path(configured)
        # Accept either a clone root (the documented shape) or a venv path;
        # the latter is useful for existing installations that keep their
        # environment outside the checkout.
        out.append(_venv_python(root if _venv_python(root).is_file() else root / ".venv"))
    out.append(_venv_python(_MANAGED_VENV_DIR))
    return out


def is_installed() -> bool:
    return any(candidate.is_file() for candidate in _probe_paths())


def _venv_can_import(python_path: Path) -> ProbeResult:
    return venv_can_import(
        python_path, _PROBE_SNIPPET, engine="cosyvoice", logger=logger,
    )


def _locate_uv() -> Optional[str]:
    from services.sidecar_install import _locate_uv
    return _locate_uv()


def _uv_env() -> "dict[str, str] | None":
    from services.sidecar_install import uv_subprocess_env
    return uv_subprocess_env(_MANAGED_VENV_DIR.parent.parent)


def _bootstrap_managed_venv() -> Path:
    uv = _locate_uv()
    if not uv:
        raise RuntimeError(
            "uv is required to prepare the CosyVoice compatibility environment. "
            "Install uv or set OMNIVOICE_COSYVOICE_DIR to a clone with a working .venv."
        )
    _MANAGED_VENV_DIR.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Preparing CosyVoice compatibility venv at %s", log_safe(_MANAGED_VENV_DIR))
    try:
        subprocess.run(
            [uv, "venv", str(_MANAGED_VENV_DIR)], check=True,
            timeout=_UV_VENV_TIMEOUT_S, capture_output=True, env=_uv_env(),
        )
        python_path = _venv_python(_MANAGED_VENV_DIR)
        subprocess.run(
            [uv, "pip", "install", "--python", str(python_path), *_BOOTSTRAP_DEPS],
            check=True, timeout=_UV_PIP_INSTALL_TIMEOUT_S,
            capture_output=True, env=_uv_env(),
        )
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else str(exc)
        raise RuntimeError(
            "CosyVoice compatibility environment installation failed: "
            f"{detail[-1200:]}"
        ) from exc
    return _venv_python(_MANAGED_VENV_DIR)


def resolve_cosyvoice_venv() -> Path:
    global _resolved_python
    if _resolved_python is not None:
        return _resolved_python
    unproven: Optional[Path] = None
    for candidate in _probe_paths():
        if not candidate.is_file():
            continue
        verdict = _venv_can_import(candidate)
        if verdict == "yes":
            _resolved_python = candidate
            return candidate
        if verdict == "unproven" and unproven is None:
            unproven = candidate
    if unproven is not None:
        logger.warning("CosyVoice venv probe timed out for %s; using it as fallback", log_safe(unproven))
        _resolved_python = unproven
        return unproven
    candidate = _bootstrap_managed_venv()
    verdict = _venv_can_import(candidate)
    if verdict == "no":
        raise RuntimeError(
            "CosyVoice compatibility environment was created but its pinned "
            "dependencies cannot be imported. Reinstall the CosyVoice engine."
        )
    _resolved_python = candidate
    return candidate


__all__ = [
    "COSYVOICE_SIDECAR_SCRIPT", "invalidate", "is_installed",
    "resolve_cosyvoice_venv",
]
