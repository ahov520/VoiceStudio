"""Qwen3-TTS 1.7B sidecar entry point.

Runs under its own venv (``engines/qwen3_tts/.venv`` or the directory named
by ``OMNIVOICE_QWEN3_TTS_DIR``), isolated from the VoiceStudio parent.

Two inference modes, selected by environment:

  * **transformers** (default) - loads ``Qwen/Qwen3-TTS-12Hz-1.7B-Base`` via
    ``transformers`` (``Qwen3TTSForConditionalGeneration``). Set
    ``OMNIVOICE_QWEN3_TTS_MODEL`` to override the HF repo id. The first
    synthesize op downloads the weights (~3 GB) from HuggingFace.

  * **gguf** - shells out to llama.cpp's ``llama-tts`` binary with a
    Q8_0-quantised Qwen3-TTS GGUF (e.g. ``cstr/qwen3-tts-1.7b-base-GGUF``,
    ``ggml-org/Qwen3-TTS-12Hz-1.7B-Base-GGUF``). Set
    ``OMNIVOICE_QWEN3_GGUF_BIN`` to the binary path (else ``llama-tts`` on
    PATH), and optionally ``OMNIVOICE_QWEN3_GGUF_MODEL`` to a local GGUF
    file (else the HF repo is auto-downloaded). Voice cloning works via
    ``--tts-speaker-file`` (reference WAV/MP3).

This script is stdlib-only at import time; the heavy imports happen lazily
on the first synthesize op so the ``ready`` frame lands within the parent's
spawn handshake. Wire protocol is length-prefixed JSON over stdin/stdout,
byte-identical to ``backend/services/subprocess_backend.py``:

    [ 4-byte big-endian uint32 length ][ N bytes UTF-8 JSON ]

    sidecar -> parent: {"op": "ready", "engine": "qwen3-tts", "sample_rate": 24000}
    parent  -> sidecar: {"op": "synthesize", "text": "...", "ref_audio": "/path.wav",
                         "language": "zh", "mode": "auto"}
    sidecar -> parent: {"op": "audio", "audio_pcm_b64": "<base64 int16>",
                        "sample_rate": 24000, "n_samples": N}
    parent  -> sidecar: {"op": "shutdown"} -> exit 0

Restrictions (mirror the IndexTTS sidecar contract):

  * NO imports from ``backend.services`` / ``backend.engines`` - this runs
    under a venv where those modules may not resolve.
  * NO logging of environment variable values.
"""
from __future__ import annotations

import base64
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import traceback

# Mirrors backend/services/subprocess_backend.py::MAX_FRAME_BYTES.
MAX_FRAME_BYTES = 64 * 1024 * 1024

SAMPLE_RATE = 24000

# llama-tts --tts-lang accepts these (the Qwen3-TTS supported set).
_GGUF_LANGS = {
    "zh", "en", "de", "it", "pt", "es", "ja", "ko", "fr", "ru",
    "chinese", "english", "german", "italian", "portuguese",
    "spanish", "japanese", "korean", "french", "russian",
}


def _read_frame() -> dict | None:
    raw = sys.stdin.buffer.read(4)
    if not raw:
        return None
    (length,) = struct.unpack(">I", raw)
    if length > MAX_FRAME_BYTES:
        raise IOError(f"inbound frame too large: {length} bytes")
    payload = sys.stdin.buffer.read(length)
    return json.loads(payload.decode("utf-8"))


def _write_frame(obj: dict) -> None:
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(struct.pack(">I", len(data)) + data)
    sys.stdout.buffer.flush()


def _pcm_from_wav_bytes(wav: bytes) -> bytes:
    """Naive WAV -> int16 PCM (PCM-only; the tools we drive emit PCM16)."""
    import wave
    with wave.open(io.BytesIO(wav), "rb") as wf:
        assert wf.getframerate() == SAMPLE_RATE, wf.getframerate()
        return wf.readframes(wf.getnframes())


def _synth_transformers(text: str, ref_audio: str | None, language: str | None) -> bytes:
    """Qwen3-TTS via transformers. Weights download on first use."""
    import torch  # noqa: F401 - device placement below
    from transformers import AutoProcessor, Qwen3TTSForConditionalGeneration

    model_id = os.environ.get("OMNIVOICE_QWEN3_TTS_MODEL") or "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
    device = "cuda" if torch.cuda.is_available() else (
        "mps" if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available() else "cpu"
    )
    model = Qwen3TTSForConditionalGeneration.from_pretrained(
        model_id, torch_dtype=torch.float16 if device != "cpu" else torch.float32
    ).to(device)
    processor = AutoProcessor.from_pretrained(model_id)

    kwargs = {"text": text}
    if ref_audio:
        # Voice cloning: a reference clip conditions the timbre.
        import soundfile as sf
        ref, sr = sf.read(ref_audio, dtype="float32")
        if sr != SAMPLE_RATE:
            import numpy as np
            from scipy.signal import resample_poly  # type: ignore
            ref = resample_poly(ref, SAMPLE_RATE, sr)
        kwargs["audio"] = ref

    inputs = processor(**kwargs, return_tensors="pt").to(device)
    outputs = model.generate(**inputs)
    audio = outputs.audio_values
    pcm = (audio.squeeze().float().cpu().numpy() * 32767).clip(-32768, 32767).astype("int16")
    return pcm.tobytes()


def _gguf_llama_tts_command(text: str, ref_audio: str | None, language: str | None) -> list[str]:
    """Build the llama-tts argv (GGUF Q8 path)."""
    binary = os.environ.get("OMNIVOICE_QWEN3_GGUF_BIN") or shutil.which("llama-tts")
    if not binary:
        raise RuntimeError(
            "llama-tts binary not found. Set OMNIVOICE_QWEN3_GGUF_BIN to its "
            "path or install llama.cpp's tts tool (see docs/engines/qwen3-tts.md)."
        )
    model = os.environ.get("OMNIVOICE_QWEN3_GGUF_MODEL")
    cmd = [binary]
    if model:
        cmd += ["-m", model]
    else:
        cmd += ["-hf", "ggml-org/Qwen3-TTS-12Hz-1.7B-Base-GGUF"]
    cmd += ["-p", text]
    if language:
        lang = (language or "").lower().replace("_", "-").split("-")[0]
        if lang in _GGUF_LANGS:
            cmd += ["--tts-lang", lang]
    if ref_audio:
        cmd += ["--tts-speaker-file", ref_audio]
    return cmd


def _synth_gguf(text: str, ref_audio: str | None, language: str | None) -> bytes:
    """Qwen3-TTS via llama.cpp's llama-tts binary (Q8 GGUF)."""
    cmd = _gguf_llama_tts_command(text, ref_audio, language)
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "out.wav")
        proc = subprocess.run(
            cmd + ["--output", out],
            capture_output=True,
            timeout=900,
        )
        if proc.returncode != 0:
            msg = (proc.stderr or b"").decode(errors="replace").strip()[:800]
            raise RuntimeError(f"llama-tts failed ({proc.returncode}): {msg}")
        with open(out, "rb") as fh:
            return _pcm_from_wav_bytes(fh.read())


def _mode() -> str:
    env = (os.environ.get("OMNIVOICE_QWEN3_TTS_MODE") or "auto").lower()
    if env in ("gguf", "llama.cpp", "llamacpp"):
        return "gguf"
    if env in ("transformers", "torch", "pt"):
        return "transformers"
    # Auto: a GGUF binary/model present beats the python path (no venv deps).
    if os.environ.get("OMNIVOICE_QWEN3_GGUF_BIN") or os.environ.get("OMNIVOICE_QWEN3_GGUF_MODEL"):
        return "gguf"
    return "transformers"


def _synthesize(frame: dict) -> dict:
    text = str(frame.get("text") or "").strip()
    if not text:
        return {"op": "error", "stage": "synthesize", "message": "empty text"}
    ref_audio = frame.get("ref_audio") or None
    language = frame.get("language") or None
    try:
        mode = frame.get("mode") or _mode()
        if mode == "gguf":
            pcm = _synth_gguf(text, ref_audio, language)
        else:
            pcm = _synth_transformers(text, ref_audio, language)
        return {
            "op": "audio",
            "audio_pcm_b64": base64.b64encode(pcm).decode("ascii"),
            "sample_rate": SAMPLE_RATE,
            "n_samples": len(pcm) // 2,
        }
    except Exception as exc:  # noqa: BLE001 - surface the real cause to the parent
        return {"op": "error", "stage": "synthesize", "message": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    _write_frame({"op": "ready", "engine": "qwen3-tts", "sample_rate": SAMPLE_RATE})
    while True:
        try:
            frame = _read_frame()
        except Exception as exc:  # noqa: BLE001
            _write_frame({"op": "error", "stage": "io", "message": str(exc)})
            return 1
        if frame is None:
            return 0
        op = frame.get("op")
        if op == "ping":
            _write_frame({"op": "pong"})
        elif op == "synthesize":
            _write_frame(_synthesize(frame))
        elif op == "shutdown":
            return 0
        else:
            _write_frame({"op": "error", "stage": "dispatch", "message": f"unknown op: {op}"})


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - last-resort traceback on stderr
        sys.stderr.write(traceback.format_exc())
        sys.exit(1)
