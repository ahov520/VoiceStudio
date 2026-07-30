"""pockettts sidecar entry point (#1306).

Runs Kyutai PocketTTS in a child process under the parent's own interpreter
(same pins), so a wedged generate can be hard-killed by the parent to reclaim
memory. Mirrors engines/omnivoice_subprocess/main.py.

Wire protocol: length-prefixed JSON over stdin/stdout, byte-identical to
services/subprocess_backend.py::

    [ 4-byte big-endian uint32 length ][ N bytes UTF-8 JSON ]

Op flow:
    1. sidecar -> parent: {"op":"ready","engine":"pockettts","sample_rate":24000}
    2. parent -> sidecar: {"op":"ping"} -> {"op":"pong","vram_mb":0}
    3. parent -> sidecar: {"op":"synthesize","text":"...",
                            "ref_audio":"/path/to/ref.wav"}
       -> {"op":"progress",...} (cold load) then
       -> {"op":"audio","audio_pcm_b64":"...","sample_rate":24000,
           "n_samples":N}
    4. parent -> sidecar: {"op":"shutdown"} -> exit 0

Stdlib-only at import time; torch + pocket_tts are imported lazily on the first
synthesize so the ready frame fits the parent's 30s spawn handshake even on a
cold filesystem.

Note: ``TTSModel.load_model()`` pulls the gated kyutai/pocket-tts weights from
HuggingFace, so it needs HF auth + the access agreement accepted. A failure here
currently surfaces as a raw error frame; the typed "weights are gated" preflight
(condition 6) is built on top of this shape, not in it.
"""
from __future__ import annotations

import base64
import json
import struct
import sys
import traceback

# Mirrors services/subprocess_backend.py::MAX_FRAME_BYTES.
MAX_FRAME_BYTES = 64 * 1024 * 1024

#: PocketTTS emits 24 kHz mono. Re-read from the loaded model on each generate.
POCKETTTS_SAMPLE_RATE = 24_000

#: Default preset voice when no reference clip is supplied (a public preset, so
#: the run has no local-file dependency). Voice source does not affect synth speed.
_DEFAULT_VOICE = "alba"

_model = None
# ref_audio path (or "" for the default) -> voice_state. get_state_for_audio_prompt
# is relatively slow, so cache per ref to avoid re-encoding on every call.
_voice_cache: dict[str, object] = {}


# -- wire protocol -----------------------------------------------------------


def _send(stream, obj: dict) -> None:
    body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    stream.write(struct.pack("!I", len(body)))
    stream.write(body)
    stream.flush()


def _recv(stream):
    header = stream.read(4)
    if len(header) < 4:
        return None  # EOF
    (n,) = struct.unpack("!I", header)
    if n > MAX_FRAME_BYTES:
        raise IOError(f"frame too large: {n}")
    body = bytearray()
    while len(body) < n:
        chunk = stream.read(n - len(body))
        if not chunk:
            raise IOError("short read")
        body.extend(chunk)
    return json.loads(bytes(body).decode("utf-8"))


def _measure_vram_mb() -> float:
    """CPU-only engine: always 0. Kept for protocol parity with the parent."""
    return 0.0


# -- model loading (lazy, on first synthesize) -------------------------------


def _load_model(stdout):
    """Cold-construct the PocketTTS model. Emits progress frames for the parent
    watchdog. Raises on failure (e.g. gated-weights access without HF auth); the
    caller emits an error frame and stays alive for a retry."""
    global _model
    if _model is not None:
        return _model

    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 0})

    from pocket_tts import TTSModel  # type: ignore[import-not-found]  # noqa: PLC0415

    _model = TTSModel.load_model()
    _send(stdout, {"op": "progress", "stage": "loading_model", "percent": 100})
    return _model


def _voice_state(model, ref_audio):
    """Return a (cached) voice state for ``ref_audio`` (path/URL) or the default
    preset when none is given."""
    key = ref_audio or ""
    state = _voice_cache.get(key)
    if state is not None:
        return state
    state = model.get_state_for_audio_prompt(ref_audio or _DEFAULT_VOICE)
    _voice_cache[key] = state
    return state


def _tensor_to_pcm_b64(audio, sample_rate: int) -> tuple[str, int, int]:
    """Convert a float waveform in [-1, 1] to base64 int16 PCM."""
    import numpy as np

    arr = np.asarray(audio, dtype=np.float32).squeeze()
    if arr.ndim > 1:
        arr = arr.mean(axis=0)  # defensive downmix to mono
    arr = np.clip(arr, -1.0, 1.0)
    pcm = (arr * 32767.0).astype(np.int16).tobytes()
    return base64.b64encode(pcm).decode("ascii"), int(sample_rate), int(arr.shape[-1])


def _handle_synthesize(msg: dict, stdout) -> None:
    """Dispatch one synthesize request. Emits the audio frame or raises."""
    text = msg.get("text")
    if not text or not isinstance(text, str):
        raise ValueError("synthesize: missing or non-string 'text'")

    model = _load_model(stdout)
    ref_audio = msg.get("ref_audio") or None
    voice_state = _voice_state(model, ref_audio)

    audio = model.generate_audio(voice_state, text)
    sample_rate = int(getattr(model, "sample_rate", POCKETTTS_SAMPLE_RATE))

    pcm_b64, sr, n_samples = _tensor_to_pcm_b64(audio, sample_rate)
    _send(stdout, {
        "op": "audio",
        "audio_pcm_b64": pcm_b64,
        "sample_rate": sr,
        "n_samples": n_samples,
    })


# -- main loop ---------------------------------------------------------------


def main() -> int:
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer

    # Ready handshake fires BEFORE any heavy import.
    _send(stdout, {
        "op": "ready",
        "engine": "pockettts",
        "sample_rate": POCKETTTS_SAMPLE_RATE,
    })

    while True:
        try:
            msg = _recv(stdin)
        except Exception as exc:
            _send(stdout, {
                "op": "error",
                "stage": "recv",
                "message": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            })
            return 1
        if msg is None:
            return 0

        op = msg.get("op") if isinstance(msg, dict) else None
        try:
            if op == "ping":
                _send(stdout, {"op": "pong", "vram_mb": _measure_vram_mb()})
            elif op == "synthesize":
                _handle_synthesize(msg, stdout)
            elif op == "shutdown":
                return 0
            else:
                _send(stdout, {
                    "op": "error",
                    "stage": "dispatch",
                    "message": f"unknown op: {op!r}",
                })
        except Exception as exc:
            _send(stdout, {
                "op": "error",
                "stage": op or "unknown",
                "message": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            })


if __name__ == "__main__":
    sys.exit(main())
