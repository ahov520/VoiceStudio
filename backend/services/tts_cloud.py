"""Cloud TTS engines — OpenAI-compatible servers, ElevenLabs, DashScope.

Three pure network clients behind the standard :class:`TTSBackend` surface.
No model runs locally, no install beyond the (optional) provider SDK, no GPU
claim — ``gpu_compat=("cpu",)`` and ``min_vram_gb=0``. All of them are
explicit opt-in: with nothing configured they report unavailable and never
touch the network (local-first contract).

Registered lazily in ``services.tts_backend._LAZY_REGISTRY`` so importing the
TTS registry doesn't pull this module (or httpx/openai/dashscope) until a
cloud engine is actually listed or used.

Conventions shared by all three engines:

* ``generate()`` is synchronous blocking HTTP — callers already run it on the
  GPU thread pool, exactly like every local engine.
* Output goes through the normal mastering + ``mark_synthetic`` watermark
  chain in the generation routes; nothing here bypasses it.
* Credentials: ElevenLabs/DashScope keys resolve via
  ``services.cloud_providers`` (env → encrypted store); the OpenAI-compatible
  engine has its own base_url/model/voice/key rows mirroring the ASR twin
  (#877). Keys are never logged and never included in raised errors.
* Do NOT route these through ``services.outbound_http`` — that helper is a
  loopback/trusted-network gate for local sidecars and rejects public hosts
  by design. Plain httpx/OpenAI-SDK clients honor the user's proxy env vars
  (HTTPS_PROXY etc.), which matters for users behind restricted networks.
"""
from __future__ import annotations

import io
import logging
import os
from typing import Optional

import torch

from services.tts_backend import TTSBackend

logger = logging.getLogger("omnivoice.tts_cloud")


# ── Shared helpers ──────────────────────────────────────────────────────────


def _env_or_text(env: str, row: str, default: str = "") -> str:
    from services import settings_store
    return os.environ.get(env) or settings_store.get_text(row) or default


def _decode_audio_bytes(data: bytes, target_sr: int) -> torch.Tensor:
    """Decode container audio (wav/flac/mp3…) to a mono (1, n) tensor at
    ``target_sr``. Mirrors GPTSoVITSBackend's response handling."""
    import torchaudio

    wav, sr = torchaudio.load(io.BytesIO(data))
    if sr != target_sr:
        wav = torchaudio.functional.resample(wav, sr, target_sr)
    if wav.ndim == 1:
        wav = wav.unsqueeze(0)
    elif wav.ndim == 2 and wav.shape[0] > 1:
        wav = wav.mean(dim=0, keepdim=True)
    return wav


def _pcm16_bytes_to_tensor(data: bytes) -> torch.Tensor:
    """Raw 16-bit little-endian mono PCM → (1, n) float tensor in [-1, 1]."""
    import numpy as np

    if len(data) < 2:
        raise RuntimeError("cloud TTS returned an empty audio payload")
    pcm = np.frombuffer(data[: len(data) - (len(data) % 2)], dtype="<i2")
    return torch.from_numpy(pcm.astype("float32") / 32768.0).unsqueeze(0)


# ── OpenAI-compatible TTS (SiliconFlow / OpenAI / self-hosted) ──────────────
# Settings mirror the ASR twin (asr_backend #877): base_url/model/voice are
# plain settings_store text rows; the key is Fernet-encrypted. Env overrides
# win always.

_TTS_OPENAI_COMPAT_BASE_URL_KEY = "tts.openai_compat.base_url"
_TTS_OPENAI_COMPAT_MODEL_KEY = "tts.openai_compat.model"
_TTS_OPENAI_COMPAT_VOICE_KEY = "tts.openai_compat.voice"
_TTS_OPENAI_COMPAT_SECRET_NAME = "tts_openai_compat_key"


def resolve_openai_compat_tts_base_url() -> str:
    return _env_or_text("TTS_OPENAI_COMPAT_BASE_URL", _TTS_OPENAI_COMPAT_BASE_URL_KEY)


def resolve_openai_compat_tts_model() -> str:
    return _env_or_text("TTS_OPENAI_COMPAT_MODEL", _TTS_OPENAI_COMPAT_MODEL_KEY, "tts-1")


def resolve_openai_compat_tts_voice() -> str:
    return _env_or_text("TTS_OPENAI_COMPAT_VOICE", _TTS_OPENAI_COMPAT_VOICE_KEY, "alloy")


def resolve_openai_compat_tts_api_key() -> Optional[str]:
    from services import settings_store
    return os.environ.get("TTS_OPENAI_COMPAT_API_KEY") or settings_store.get_secret(
        _TTS_OPENAI_COMPAT_SECRET_NAME
    )


def openai_compat_tts_has_key() -> bool:
    from services import settings_store
    if os.environ.get("TTS_OPENAI_COMPAT_API_KEY"):
        return True
    return _TTS_OPENAI_COMPAT_SECRET_NAME in settings_store.list_secret_names()


def probe_openai_compat_tts_server(*, timeout_s: float = 8.0) -> dict:
    """``GET {base_url}/models`` reachability probe for the Settings "Test
    connection" button. Same verdict shape and status codes as
    ``asr_backend.probe_openai_compat_server`` — no audio is synthesized,
    the key is never logged or echoed."""
    from time import perf_counter

    from core.scrub import scrub_text

    base = resolve_openai_compat_tts_base_url().strip().rstrip("/")
    mdl = resolve_openai_compat_tts_model().strip()
    key = resolve_openai_compat_tts_api_key()

    out: dict = {
        "ok": False,
        "status": "not_configured",
        "latency_ms": None,
        "http_status": None,
        "models_count": None,
        "model_found": None,
        "detail": None,
    }
    if not base:
        return out
    if not base.startswith(("http://", "https://")):
        out["status"] = "invalid_url"
        return out

    import httpx

    headers = {"Authorization": f"Bearer {key}"} if key else {}
    t0 = perf_counter()
    try:
        with httpx.Client(
            timeout=httpx.Timeout(timeout_s, connect=min(5.0, timeout_s)),
            follow_redirects=True,
        ) as client:
            resp = client.get(f"{base}/models", headers=headers)
    except httpx.TimeoutException as exc:
        out.update(
            status="timeout",
            latency_ms=round((perf_counter() - t0) * 1000.0, 1),
            detail=scrub_text(f"{type(exc).__name__}: {exc}"),
        )
        return out
    except Exception as exc:  # noqa: BLE001 — ConnectError, SSL, DNS…
        out.update(
            status="unreachable",
            latency_ms=round((perf_counter() - t0) * 1000.0, 1),
            detail=scrub_text(f"{type(exc).__name__}: {exc}"),
        )
        return out

    out["latency_ms"] = round((perf_counter() - t0) * 1000.0, 1)
    out["http_status"] = resp.status_code

    if 200 <= resp.status_code < 300:
        out.update(ok=True, status="ok")
        try:
            data = resp.json()
            entries = data.get("data") if isinstance(data, dict) else data
            if isinstance(entries, list):
                ids = [e.get("id") for e in entries if isinstance(e, dict) and e.get("id")]
            else:
                ids = None
        except Exception:  # noqa: BLE001 — non-JSON 200 still proves reachability
            ids = None
        if ids is not None:
            out["models_count"] = len(ids)
            out["model_found"] = mdl in ids if mdl else None
        return out

    if resp.status_code in (401, 403):
        out["status"] = "auth_failed"
    elif resp.status_code in (404, 405, 501):
        out.update(ok=True, status="ok_no_models")
    else:
        out["status"] = "http_error"
        out["detail"] = scrub_text((resp.text or "")[:300]) or None
    return out


class OpenAICompatTTSBackend(TTSBackend):
    """Speech synthesis via any OpenAI-compatible ``POST /v1/audio/speech``
    server — SiliconFlow (CosyVoice2 and friends), OpenAI's own TTS API, or a
    self-hosted box. Preset voices only (the ``voice`` string the server
    expects); no reference-audio cloning through this API."""

    id = "openai-compat-tts"
    display_name = "OpenAI-compatible TTS (remote server)"
    gpu_compat = ("cpu",)  # network client only — no local compute
    min_vram_gb = 0.0
    supports_cloning = False

    def __init__(self):
        self._base_url = resolve_openai_compat_tts_base_url()
        self._model = resolve_openai_compat_tts_model()
        self._voice = resolve_openai_compat_tts_voice()

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        if not resolve_openai_compat_tts_base_url():
            return False, "Configure a server endpoint in Model Catalogue → Engines"
        try:
            import openai  # noqa: F401
        except ImportError:
            return False, "openai package not installed. Install with: uv pip install openai"
        return True, "ready"

    @property
    def sample_rate(self) -> int:
        return 24000

    @property
    def supported_languages(self) -> list[str]:
        return ["multi"]

    def _client(self):
        from openai import OpenAI
        api_key = resolve_openai_compat_tts_api_key() or "not-needed"
        # max_retries=0: same rationale as OpenAICompatASRBackend — the SDK's
        # internal retry ladder would blow past the caller's generate budget.
        return OpenAI(base_url=self._base_url, api_key=api_key, max_retries=0)

    def generate(self, text: str, **kw) -> torch.Tensor:
        voice = (kw.get("voice") or self._voice or "alloy").strip()
        speed = float(kw.get("speed") or 1.0)
        req: dict = {
            "model": self._model,
            "voice": voice,
            "input": text,
            "response_format": "wav",
        }
        if speed != 1.0:
            req["speed"] = speed
        logger.info(
            "OpenAI-compat TTS synthesizing %d chars (base_url=%s, model=%s, voice=%s)",
            len(text), self._base_url, self._model, voice,
        )
        client = self._client()
        try:
            resp = client.audio.speech.create(**req)
            data = getattr(resp, "content", None)
            if data is None:
                data = resp.read()
        except Exception as exc:
            # Never leak the raw SDK/httpx exception object (auth headers,
            # connection internals) — same convention as the ASR twin.
            raise RuntimeError(
                f"OpenAI-compatible TTS server at {self._base_url!r} failed: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if not data:
            raise RuntimeError(
                f"OpenAI-compatible TTS server at {self._base_url!r} returned no audio"
            )
        return _decode_audio_bytes(bytes(data), self.sample_rate)


# ── ElevenLabs TTS ──────────────────────────────────────────────────────────

_TTS_ELEVENLABS_VOICE_KEY = "tts.elevenlabs.voice_id"
_TTS_ELEVENLABS_MODEL_KEY = "tts.elevenlabs.model_id"

#: "Rachel" — the ElevenLabs starter voice present on every account.
_ELEVENLABS_DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"
_ELEVENLABS_DEFAULT_MODEL = "eleven_multilingual_v2"


def resolve_elevenlabs_tts_voice_id() -> str:
    return _env_or_text(
        "TTS_ELEVENLABS_VOICE_ID", _TTS_ELEVENLABS_VOICE_KEY, _ELEVENLABS_DEFAULT_VOICE
    )


def resolve_elevenlabs_tts_model_id() -> str:
    return _env_or_text(
        "TTS_ELEVENLABS_MODEL_ID", _TTS_ELEVENLABS_MODEL_KEY, _ELEVENLABS_DEFAULT_MODEL
    )


def list_elevenlabs_voices(*, timeout_s: float = 15.0) -> dict:
    """Voices the configured ElevenLabs key can use (Settings voice picker).

    Read-only; never raises. ``{ok: True, voices: [{voice_id, name,
    category}]}`` or ``{ok: False, status, detail}`` with the same status
    vocabulary as the provider probes."""
    from core.scrub import scrub_text
    from services import cloud_providers

    key = cloud_providers.resolve_api_key("elevenlabs")
    if not key:
        return {"ok": False, "status": "not_configured", "voices": []}

    import httpx

    try:
        with httpx.Client(timeout=timeout_s, follow_redirects=True) as client:
            resp = client.get(
                f"{cloud_providers.elevenlabs_base_url()}/v1/voices",
                headers={"xi-api-key": key},
            )
    except httpx.TimeoutException:
        return {"ok": False, "status": "timeout", "voices": []}
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False, "status": "unreachable", "voices": [],
            "detail": scrub_text(f"{type(exc).__name__}: {exc}"),
        }
    if resp.status_code in (401, 403):
        return {"ok": False, "status": "auth_failed", "voices": []}
    if not (200 <= resp.status_code < 300):
        return {"ok": False, "status": "http_error", "http_status": resp.status_code, "voices": []}
    try:
        raw = resp.json().get("voices") or []
    except Exception:  # noqa: BLE001
        raw = []
    voices = [
        {
            "voice_id": v.get("voice_id"),
            "name": v.get("name"),
            "category": v.get("category"),
        }
        for v in raw
        if isinstance(v, dict) and v.get("voice_id")
    ]
    return {"ok": True, "status": "ok", "voices": voices}


class ElevenLabsTTSBackend(TTSBackend):
    """ElevenLabs text-to-speech over the REST API.

    Uses the voice_id configured in Settings (any voice from the account's
    voice library — including voices the user cloned on elevenlabs.io).
    Reference-audio cloning is not driven through this adapter, so dub/batch
    jobs that need per-segment cloning gate it out via
    ``supports_cloning=False``.

    Audio is requested as raw PCM (``output_format=pcm_24000``) so no
    mp3 decode is needed on the way in.
    """

    id = "elevenlabs-tts"
    display_name = "ElevenLabs TTS (cloud)"
    gpu_compat = ("cpu",)
    min_vram_gb = 0.0
    supports_cloning = False

    def __init__(self):
        self._voice_id = resolve_elevenlabs_tts_voice_id()
        self._model_id = resolve_elevenlabs_tts_model_id()

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        from services import cloud_providers
        if not cloud_providers.has_key("elevenlabs"):
            return False, (
                "Configure an ElevenLabs API key in Settings → Cloud providers "
                "(or set ELEVENLABS_API_KEY)"
            )
        return True, "ready"

    @property
    def sample_rate(self) -> int:
        return 24000

    @property
    def supported_languages(self) -> list[str]:
        return ["multi"]

    def generate(self, text: str, **kw) -> torch.Tensor:
        from services import cloud_providers

        key = cloud_providers.resolve_api_key("elevenlabs")
        if not key:
            raise RuntimeError(
                "No ElevenLabs API key configured — add one in Settings → Cloud providers"
            )
        voice_id = (kw.get("voice") or self._voice_id).strip()
        base = cloud_providers.elevenlabs_base_url()
        url = f"{base}/v1/text-to-speech/{voice_id}"
        payload: dict = {"text": text, "model_id": self._model_id}
        speed = float(kw.get("speed") or 1.0)
        if speed != 1.0:
            # voice_settings.speed: 0.7–1.2 per the API; clamp rather than 400.
            payload["voice_settings"] = {"speed": max(0.7, min(1.2, speed))}
        logger.info(
            "ElevenLabs TTS synthesizing %d chars (voice=%s, model=%s)",
            len(text), voice_id, self._model_id,
        )

        import httpx

        try:
            with httpx.Client(timeout=120.0, follow_redirects=True) as client:
                resp = client.post(
                    url,
                    params={"output_format": "pcm_24000"},
                    headers={"xi-api-key": key},
                    json=payload,
                )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"ElevenLabs TTS request failed: {type(exc).__name__}: {exc}"
            ) from exc
        if resp.status_code in (401, 403):
            raise RuntimeError(
                "ElevenLabs rejected the API key (HTTP "
                f"{resp.status_code}) — check Settings → Cloud providers"
            )
        if not (200 <= resp.status_code < 300):
            from core.scrub import scrub_text
            raise RuntimeError(
                f"ElevenLabs TTS failed with HTTP {resp.status_code}: "
                f"{scrub_text((resp.text or '')[:300])}"
            )
        return _pcm16_bytes_to_tensor(resp.content)


# ── DashScope (Alibaba Cloud Model Studio) CosyVoice TTS ────────────────────

_TTS_DASHSCOPE_MODEL_KEY = "tts.dashscope.model"
_TTS_DASHSCOPE_VOICE_KEY = "tts.dashscope.voice"

_DASHSCOPE_DEFAULT_MODEL = "cosyvoice-v2"
_DASHSCOPE_DEFAULT_VOICE = "longxiaochun_v2"


def resolve_dashscope_tts_model() -> str:
    return _env_or_text("TTS_DASHSCOPE_MODEL", _TTS_DASHSCOPE_MODEL_KEY, _DASHSCOPE_DEFAULT_MODEL)


def resolve_dashscope_tts_voice() -> str:
    return _env_or_text("TTS_DASHSCOPE_VOICE", _TTS_DASHSCOPE_VOICE_KEY, _DASHSCOPE_DEFAULT_VOICE)


class DashScopeTTSBackend(TTSBackend):
    """CosyVoice / Qwen-TTS speech synthesis on Alibaba Cloud Model Studio
    (Bailian), via the official ``dashscope`` SDK's non-streaming blocking call.

    Reachable from mainland China without a proxy. Model and voice versions
    must match (cosyvoice-v2 → *_v2 voices, cosyvoice-v3-* → v3 voices…) —
    both are configurable in Settings. Preset/system voices only through this
    adapter (voice enrollment/cloning is not driven from here).
    """

    id = "dashscope-tts"
    display_name = "Alibaba Cloud CosyVoice (DashScope, cloud)"
    gpu_compat = ("cpu",)
    min_vram_gb = 0.0
    supports_cloning = False

    def __init__(self):
        self._model = resolve_dashscope_tts_model()
        self._voice = resolve_dashscope_tts_voice()

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        import importlib.util

        from services import cloud_providers
        if not cloud_providers.has_key("dashscope"):
            return False, (
                "Configure a DashScope API key in Settings → Cloud providers "
                "(or set DASHSCOPE_API_KEY)"
            )
        if importlib.util.find_spec("dashscope") is None:
            return False, "dashscope package not installed. Install with: uv pip install dashscope"
        return True, "ready"

    @property
    def sample_rate(self) -> int:
        return 24000

    @property
    def supported_languages(self) -> list[str]:
        return ["multi"]

    def generate(self, text: str, **kw) -> torch.Tensor:
        from services import cloud_providers

        key = cloud_providers.resolve_api_key("dashscope")
        if not key:
            raise RuntimeError(
                "No DashScope API key configured — add one in Settings → Cloud providers"
            )
        try:
            import dashscope
            from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer
        except ImportError as exc:
            raise RuntimeError(
                "dashscope package not installed. Install with: uv pip install dashscope"
            ) from exc

        fmt = getattr(AudioFormat, "WAV_24000HZ_MONO_16BIT", None)
        if fmt is None:  # pragma: no cover — enum present in every current SDK
            raise RuntimeError(
                "This dashscope SDK version lacks the WAV_24000HZ_MONO_16BIT "
                "output format — upgrade with: uv pip install -U dashscope"
            )

        voice = (kw.get("voice") or self._voice).strip()
        speed = max(0.5, min(2.0, float(kw.get("speed") or 1.0)))
        dashscope.api_key = key
        logger.info(
            "DashScope TTS synthesizing %d chars (model=%s, voice=%s)",
            len(text), self._model, voice,
        )
        try:
            synth = SpeechSynthesizer(
                model=self._model, voice=voice, format=fmt, speech_rate=speed,
            )
            data = synth.call(text)
        except Exception as exc:  # noqa: BLE001 — SDK raises assorted types
            from core.scrub import scrub_text
            raise RuntimeError(
                f"DashScope TTS (model={self._model!r}, voice={voice!r}) failed: "
                f"{scrub_text(f'{type(exc).__name__}: {exc}')}"
            ) from exc
        if not data:
            detail = ""
            last = getattr(synth, "last_response", None)
            if last is not None:
                from core.scrub import scrub_text
                detail = f" ({scrub_text(str(last)[:200])})"
            raise RuntimeError(
                f"DashScope TTS returned no audio for model={self._model!r}, "
                f"voice={voice!r}{detail}"
            )
        return _decode_audio_bytes(bytes(data), self.sample_rate)
