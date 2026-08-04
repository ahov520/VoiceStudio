"""pockettts: Kyutai PocketTTS as a crash-isolated, CPU-only TTS sidecar (#1306).

PocketTTS (kyutai-labs/pocket-tts, 100M params) is hired for the "fastest CPU
render / lowest latency" job, the row the engine-acceptance framework leaves
unheld: every CPU engine OmniVoice ships is either English-only or a quality
engine falling back to CPU. PocketTTS is the complementary opposite end of the
spectrum from the quality engines (omnivoice, IndexTTS, Supertonic-3): small,
fast, CPU-only, zero-shot cloning from a reference clip. Six languages
(en/fr/de/pt/it/es), one model per language, selected via the ``language``
kwarg. Measured ~8-9x real-time on an Apple M3 Pro (see
scripts/bench_engines_latency.py, PR #1322).

This engine runs PocketTTS in a child process via :class:`SubprocessBackend`,
mirroring engines/omnivoice_subprocess and engines/supertonic3. Crash isolation:
a wedged generate is hard-killed by the parent's watchdog, reclaiming the
child's memory, the thing an in-process engine structurally cannot do. CPU-only
by design (``gpu_compat = ("cpu",)``): Kyutai observes no GPU speedup for this
100M, batch-1 model.

Opt-in (Settings -> Engines, or ``OMNIVOICE_TTS_BACKEND=pockettts``); the
default ``omnivoice`` engine is unchanged, so existing users see no behaviour
change.

Licence: MIT (code) + CC-BY-4.0 (weights), both commercial-OK (cleared from
primary sources in #1306). The weights are gated on HuggingFace (an access
agreement plus an acceptable-use clause); the engine must surface that honestly
at first-run rather than failing inside a download (condition 6 of the #1306
acceptance). That preflight is built on top of this shape, not in it.

Streaming note: PocketTTS streams audio (``generate_audio_stream``), but this
batch sidecar returns one audio frame per synth, matching the SubprocessBackend
contract every other subprocess engine uses. A streaming-aware variant
(incremental audio frames) is a documented opportunity to recover PocketTTS's
~33 ms time-to-first-audio end-to-end; out of scope for this shape, raised on
the PR.
"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from services.subprocess_backend import SubprocessBackend

if TYPE_CHECKING:
    import torch  # noqa: F401


class PocketTTSBackend(SubprocessBackend):
    """Kyutai PocketTTS in a killable, CPU-only sidecar process."""

    id = "pockettts"
    display_name = "PocketTTS (Kyutai, 6 langs, CPU-only, MIT/CC-BY-4.0)"
    _DEFAULT_SAMPLE_RATE = 24_000
    # CPU-only by design (honest hardware reporting, like supertonic3): Kyutai
    # ships no CUDA/MPS path and reports no GPU speedup for this model.
    gpu_compat: tuple[str, ...] = ("cpu",)
    supports_cloning = True  # zero-shot clone from a reference clip

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        # Optional-dep gate: the pocket-tts wheel is installed only when the user
        # opted in. The interpreter is the parent's own (sys.executable), so
        # there is no separate venv to validate.
        try:
            import pocket_tts  # type: ignore[import-not-found]  # noqa: F401
        except Exception as e:
            return False, (
                f"pocket_tts package not installed or failed to import ({e}). "
                f"Enable in Settings -> Engines (pip install pocket-tts)."
            )
        return True, "ready (CPU-only)"

    @classmethod
    def venv_python(cls) -> Path:
        # Parent interpreter: pocket-tts deps (torch>=2.5, scipy, beartype) sit
        # happily at the parent's pins, so this isolates for crash recovery, not
        # dependency pins (same rationale as omnivoice-subprocess).
        return Path(sys.executable)

    @classmethod
    def sidecar_script(cls) -> Path:
        return Path(__file__).resolve().parent / "main.py"

    @property
    def recv_timeout_s(self) -> float:
        # A cold load pulls gated weights (a 24-layer model can be hundreds of MB),
        # so allow a long recv deadline; the sidecar also heartbeats progress frames
        # during the download (main.py) to keep the watchdog armed.
        try:
            v = float(os.environ.get("OMNIVOICE_POCKETTTS_RECV_TIMEOUT_S", "600"))
        except (ValueError, TypeError):
            return 600.0
        if not math.isfinite(v):  # reject inf/nan so the deadline can't be disabled
            return 600.0
        return max(30.0, v)

    @property
    def sample_rate(self) -> int:
        return self._DEFAULT_SAMPLE_RATE

    @property
    def supported_languages(self) -> list[str]:
        # Protocol tag; six languages (en/fr/de/pt/it/es), one model per
        # language, selected via the language kwarg.
        return ["multi"]


__all__ = ["PocketTTSBackend"]
