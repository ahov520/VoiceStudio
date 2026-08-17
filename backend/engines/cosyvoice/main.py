"""CosyVoice sidecar entry point.

This file is stdlib-only until the first synthesis.  It adds the VoiceStudio
backend and the parent app's site-packages *after* the compatibility venv's
site-packages, so pinned wheels win while torch/CosyVoice are reused.
"""
from __future__ import annotations

import base64
import json
import os
import struct
import sys
import traceback
from pathlib import Path

MAX_FRAME_BYTES = 64 * 1024 * 1024
SAMPLE_RATE = 24000
_backend = None
_FRAME_OUT = None


def _set_frame_output(stream) -> None:
    global _FRAME_OUT
    _FRAME_OUT = stream


def _write_frame(obj: dict) -> None:
    body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _FRAME_OUT.write(struct.pack(">I", len(body)) + body)
    _FRAME_OUT.flush()


def _read_frame() -> dict | None:
    header = sys.stdin.buffer.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise IOError("short frame header")
    (length,) = struct.unpack(">I", header)
    if length > MAX_FRAME_BYTES:
        raise IOError(f"frame too large: {length}")
    body = sys.stdin.buffer.read(length)
    if len(body) != length:
        raise IOError("short frame body")
    return json.loads(body.decode("utf-8"))


def _add_runtime_paths() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))

    # Installed/source VoiceStudio keeps the parent venv beside backend/.
    # Append (rather than prepend) so the sidecar's pinned packages remain
    # authoritative for transformers/numpy/onnxruntime/wetext.
    candidates = []
    explicit_parent = (os.environ.get("OMNIVOICE_PARENT_SITE_PACKAGES") or "").strip()
    if explicit_parent:
        candidates.append(Path(explicit_parent))
    configured = (os.environ.get("OMNIVOICE_COSYVOICE_DIR") or "").strip()
    if configured:
        clone = Path(configured)
        candidates.extend([clone, clone / "third_party" / "Matcha-TTS"])
    project_venv = backend_root.parent / ".venv"
    candidates.extend([
        project_venv / "Lib" / "site-packages",
        project_venv / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages",
    ])
    for candidate in candidates:
        if candidate.is_dir() and str(candidate) not in sys.path:
            sys.path.append(str(candidate))


def _pcm_from_tensor(audio) -> tuple[str, int]:
    import numpy as np

    arr = audio.detach().to("cpu").float().numpy()
    arr = np.asarray(arr, dtype=np.float32).squeeze()
    while arr.ndim > 1:
        arr = arr.mean(axis=int(np.argmin(arr.shape)))
    arr = np.clip(arr, -1.0, 1.0)
    pcm = (arr * 32767.0).astype(np.int16).tobytes()
    return base64.b64encode(pcm).decode("ascii"), int(arr.shape[0])


def _load_backend() -> None:
    global _backend
    if _backend is not None:
        return
    _write_frame({"op": "progress", "stage": "loading_model", "percent": 0})
    _add_runtime_paths()
    from services.tts_backend import CosyVoiceBackend as InProcessCosyVoiceBackend
    _backend = InProcessCosyVoiceBackend()
    _backend.ensure_ready()
    _write_frame({"op": "progress", "stage": "loading_model", "percent": 100})


def _synthesize(frame: dict) -> dict:
    text = str(frame.get("text") or "").strip()
    if not text:
        return {"op": "error", "stage": "synthesize", "message": "empty text"}
    try:
        _load_backend()
        kwargs = {
            key: frame.get(key)
            for key in ("ref_audio", "ref_text", "instruct", "language")
            if frame.get(key) is not None
        }
        audio = _backend.generate(text, **kwargs)
        pcm_b64, n_samples = _pcm_from_tensor(audio)
        return {
            "op": "audio", "audio_pcm_b64": pcm_b64,
            "sample_rate": int(getattr(_backend, "sample_rate", SAMPLE_RATE)),
            "n_samples": n_samples,
        }
    except Exception as exc:  # noqa: BLE001 - return the real engine failure
        return {"op": "error", "stage": "synthesize", "message": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    # Keep stdout clean: third-party imports are allowed to print freely to
    # fd 1, while the protocol uses the duplicated private descriptor.
    frame_fd = os.dup(1)
    os.dup2(2, 1)
    _set_frame_output(os.fdopen(frame_fd, "wb"))
    _write_frame({"op": "ready", "engine": "cosyvoice", "sample_rate": SAMPLE_RATE})
    while True:
        try:
            frame = _read_frame()
        except Exception as exc:
            _write_frame({"op": "error", "stage": "io", "message": str(exc)})
            return 1
        if frame is None:
            return 0
        op = frame.get("op") if isinstance(frame, dict) else None
        if op == "ping":
            _write_frame({"op": "pong"})
        elif op == "synthesize":
            _write_frame(_synthesize(frame))
        elif op == "shutdown":
            return 0
        else:
            _write_frame({"op": "error", "stage": "dispatch", "message": f"unknown op: {op!r}"})


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.stderr.write(traceback.format_exc())
        sys.exit(1)
