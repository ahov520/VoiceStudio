"""Vocal-separation engine registry — local Demucs plus cloud backends.

Until now vocal separation was a hardcoded ``python -m demucs.separate``
subprocess inside the dub ingest pipeline (and ``/system/clean-audio``).
This module lifts that into a small registry so the separation stage can run
on a cloud service instead — without changing the pipeline's contract:

* stems land at ``{out_dir}/vocals.wav`` (+ ``{out_dir}/no_vocals.wav`` when
  the engine produces a background bed),
* progress surfaces as integer percents (the frontend keeps listening to the
  existing ``demucs_*`` SSE events),
* any failure lets the caller fall back to the mixed track, exactly like a
  local Demucs crash always has.

Engines:

* ``demucs-local`` (default) — Meta Demucs ``htdemucs``, two stems, on this
  machine. The subprocess logic moved verbatim from ``dub_pipeline``.
* ``mvsep`` — mvsep.com's separation API (upload → queue → download). Returns
  BOTH vocals and instrumental, so dub exports keep their background bed.
  Needs an MVSEP API token (Settings → Cloud providers).
* ``elevenlabs-isolation`` — ElevenLabs Voice Isolator. Returns the voice
  track ONLY (``no_vocals_path=None``); downstream already degrades cleanly
  (``has_bg=False``: onset snapping stays off, exports skip the bed). Good
  for mic cleanup and speech-only sources; prefer MVSEP for music-heavy dubs.

Interface: ``separate()`` is an async generator yielding ``("progress", pct)``
tuples and exactly one final ``("done", vocals_path, no_vocals_path|None)``.
Task cancellation (dub abort) propagates through the generator's await
points; the local engine's subprocess is additionally registered under the
job id so ``kill_job_procs`` can terminate it, and MVSEP jobs get a
best-effort remote cancel.

Selection: env ``OMNIVOICE_SEPARATION_BACKEND`` → settings row
``separation.backend`` (Settings → Vocal separation) → ``demucs-local``. An
unavailable choice (e.g. a key that was cleared) falls back to local Demucs
at use time rather than guaranteeing a failed stage.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import sys
import time
from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional

logger = logging.getLogger("omnivoice.separation")

_SEPARATION_BACKEND_KEY = "separation.backend"
_MVSEP_SEP_TYPE_KEY = "separation.mvsep.sep_type"

DEFAULT_BACKEND_ID = "demucs-local"

#: MVSEP separation type — 40 = "BS Roformer (vocals, instrumental)", whose
#: default model scores the highest vocal SDR on MVSEP's own table; exactly
#: the two stems the dub pipeline wants. Overridable per user (Settings →
#: Vocal separation, or MVSEP_SEP_TYPE) for people who want a specific model.
DEFAULT_MVSEP_SEP_TYPE = "40"


def resolve_mvsep_sep_type() -> str:
    from services import settings_store
    raw = (
        os.environ.get("MVSEP_SEP_TYPE")
        or settings_store.get_text(_MVSEP_SEP_TYPE_KEY)
        or DEFAULT_MVSEP_SEP_TYPE
    ).strip()
    return raw if raw.isdigit() else DEFAULT_MVSEP_SEP_TYPE


class SeparationBackend(ABC):
    """Every separation engine exposes the same async-generator surface."""

    id: str = "base"
    display_name: str = "Base separation"
    #: "local" (runs on this machine) or "cloud" (audio leaves the machine).
    category: str = "local"
    #: cloud_providers id whose key this engine needs, or None.
    needs_key: Optional[str] = None
    #: Whether the engine produces a background/instrumental stem. When False
    #: the pipeline behaves like a vocals-only separation (has_bg=False).
    returns_background: bool = True

    @classmethod
    @abstractmethod
    def is_available(cls) -> tuple[bool, str]:
        ...

    @abstractmethod
    def separate(
        self,
        input_path: str,
        out_dir: str,
        *,
        job_id: str,
        timeout: float = 1800.0,
    ) -> AsyncIterator[tuple]:
        """Async generator: ``("progress", int)`` events, then exactly one
        ``("done", vocals_path, no_vocals_path|None)``. Raises on failure —
        callers keep their existing fall-back-to-mixed-audio handling."""


# ── Local Demucs (the previous inline implementation, verbatim) ─────────────


class DemucsLocalBackend(SeparationBackend):
    id = "demucs-local"
    display_name = "Demucs (local)"
    category = "local"
    needs_key = None
    returns_background = True

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        import importlib.util
        if importlib.util.find_spec("demucs") is None:
            return False, "demucs package not installed. Reinstall VoiceStudio (uv sync)."
        return True, "ready"

    async def separate(
        self, input_path: str, out_dir: str, *, job_id: str, timeout: float = 1800.0,
    ) -> AsyncIterator[tuple]:
        # Lazy import: dub_pipeline owns the subprocess plumbing (semaphore,
        # proc registry, sync-pipe fallback) and imports THIS module inside
        # its ingest function — a module-level import here would be a cycle.
        from services.dub_pipeline import run_proc_streaming_stderr
        from services.model_manager import get_best_device

        demucs_cmd = [
            sys.executable, "-m", "demucs.separate",
            "--two-stems", "vocals", "-n", "htdemucs", "-d", get_best_device(),
            input_path, "-o", out_dir,
        ]
        rc = -1
        stderr_full = b""
        last_pct = -1
        # demucs writes a tqdm progress bar to stderr as "  42%|████      | …"
        # — surface each new integer percent so the UI shows a bar instead of
        # a static spinner during the multi-minute separation step.
        async for evt in run_proc_streaming_stderr(job_id, demucs_cmd, timeout=timeout):
            if evt[0] == "stderr":
                m = re.search(r"(\d{1,3})%", evt[1])
                if m:
                    pct = max(0, min(100, int(m.group(1))))
                    if pct != last_pct:
                        last_pct = pct
                        yield ("progress", pct)
            elif evt[0] == "done":
                rc, stderr_full = evt[1], evt[2]
        if rc != 0:
            raise RuntimeError(stderr_full.decode(errors="replace")[:500])
        # Stems land under the INPUT's basename inside out_dir/htdemucs/.
        demucs_out = os.path.join(
            out_dir, "htdemucs",
            os.path.splitext(os.path.basename(input_path))[0],
        )
        vocals_path = os.path.join(out_dir, "vocals.wav")
        no_vocals_path = os.path.join(out_dir, "no_vocals.wav")
        if not os.path.exists(os.path.join(demucs_out, "vocals.wav")):
            raise RuntimeError("demucs finished but produced no vocals stem")
        shutil.move(os.path.join(demucs_out, "vocals.wav"), vocals_path)
        if os.path.exists(os.path.join(demucs_out, "no_vocals.wav")):
            shutil.move(os.path.join(demucs_out, "no_vocals.wav"), no_vocals_path)
        else:
            no_vocals_path = None
        shutil.rmtree(os.path.join(out_dir, "htdemucs"), ignore_errors=True)
        yield ("done", vocals_path, no_vocals_path)


# ── MVSEP (mvsep.com) ───────────────────────────────────────────────────────

#: Coarse stage → percent mapping for the poll loop. MVSEP doesn't report
#: fine-grained progress, so the bar moves between stages rather than within.
_MVSEP_STAGE_PCT = {
    "waiting": 12,
    "distributing": 30,
    "processing": 60,
    "merging": 85,
}
_MVSEP_POLL_INTERVAL_S = 5.0


class MVSEPBackend(SeparationBackend):
    id = "mvsep"
    display_name = "MVSEP (cloud)"
    category = "cloud"
    needs_key = "mvsep"
    returns_background = True

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        from services import cloud_providers
        if not cloud_providers.has_key("mvsep"):
            return False, (
                "Configure an MVSEP API token in Settings → Cloud providers "
                "(or set MVSEP_API_TOKEN)"
            )
        return True, "ready"

    @staticmethod
    def _fail(message: str, token: str) -> RuntimeError:
        from core.scrub import scrub_text
        if token and len(token) >= 8:
            message = message.replace(token, "•••")
        return RuntimeError(f"MVSEP separation failed: {scrub_text(message)[:300]}")

    async def separate(
        self, input_path: str, out_dir: str, *, job_id: str, timeout: float = 1800.0,
    ) -> AsyncIterator[tuple]:
        from services import cloud_providers

        token = cloud_providers.resolve_api_key("mvsep")
        if not token:
            raise RuntimeError(
                "No MVSEP API token configured — add one in Settings → Cloud providers"
            )
        base = cloud_providers.mvsep_base_url()
        sep_type = resolve_mvsep_sep_type()
        deadline = time.monotonic() + timeout

        import httpx

        job_hash: Optional[str] = None
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(120.0, connect=15.0), follow_redirects=True
            ) as client:
                logger.info(
                    "MVSEP separation: uploading %s (sep_type=%s)",
                    os.path.basename(input_path), sep_type,
                )
                with open(input_path, "rb") as fh:
                    resp = await client.post(
                        f"{base}/api/separation/create",
                        files={"audiofile": (os.path.basename(input_path), fh,
                                             "application/octet-stream")},
                        data={
                            "api_token": token,
                            "sep_type": sep_type,
                            "output_format": "1",  # WAV stems
                            "is_demo": "0",
                        },
                    )
                body = self._json_or_fail(resp, token)
                if not body.get("success"):
                    data = body.get("data") or {}
                    raise self._fail(str(data.get("message") or "job creation rejected"), token)
                job_hash = ((body.get("data") or {}).get("hash") or "").strip()
                if not job_hash:
                    raise self._fail("job created but no hash returned", token)
                yield ("progress", 10)

                # Poll until done. Inline (not a helper) so stage changes can
                # be yielded as progress events from the generator itself.
                last_pct = 10
                files: list = []
                while True:
                    if time.monotonic() > deadline:
                        raise self._fail("timed out waiting for the separation queue", token)
                    resp = await client.get(
                        f"{base}/api/separation/get", params={"hash": job_hash}
                    )
                    body = self._json_or_fail(resp, token)
                    status = str(body.get("status") or "").lower()
                    data = body.get("data") or {}
                    if status == "done":
                        files = data.get("files") or []
                        if not files:
                            raise self._fail("finished but returned no output files", token)
                        break
                    if status in ("failed", "not_found"):
                        raise self._fail(str(data.get("message") or f"job {status}"), token)
                    pct = _MVSEP_STAGE_PCT.get(status, last_pct)
                    if pct != last_pct:
                        last_pct = pct
                        yield ("progress", pct)
                    await asyncio.sleep(_MVSEP_POLL_INTERVAL_S)

                vocals_path, no_vocals_path = await self._download_stems(
                    client, files, out_dir, token
                )
                yield ("progress", 100)
                yield ("done", vocals_path, no_vocals_path)
        except asyncio.CancelledError:
            # Dub abort — refund the queued job if it hasn't started yet.
            if job_hash:
                await self._cancel_remote(base, job_hash, token)
            raise

    def _json_or_fail(self, resp, token) -> dict:
        if resp.status_code in (401, 403):
            raise self._fail(f"HTTP {resp.status_code} — the API token was rejected", token)
        if not (200 <= resp.status_code < 300):
            raise self._fail(f"HTTP {resp.status_code}: {(resp.text or '')[:200]}", token)
        try:
            body = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise self._fail(f"non-JSON response ({type(exc).__name__})", token)
        return body if isinstance(body, dict) else {}

    async def _download_stems(self, client, files, out_dir, token) -> tuple[str, Optional[str]]:
        """Pick the vocals + instrumental entries out of ``data.files`` and
        stream them to ``{out_dir}/vocals.wav`` / ``no_vocals.wav``."""
        def _entry_url(entry) -> str:
            if not isinstance(entry, dict):
                return ""
            return str(entry.get("url") or entry.get("download_url") or "")

        def _entry_name(entry) -> str:
            if isinstance(entry, dict):
                for k in ("filename", "name", "type"):
                    if entry.get(k):
                        return str(entry[k]).lower()
            return _entry_url(entry).lower()

        vocals_url = instrum_url = ""
        for entry in files:
            name = _entry_name(entry)
            url = _entry_url(entry)
            if not url:
                continue
            if ("vocals" in name and "no_vocals" not in name
                    and "instrum" not in name and not vocals_url):
                vocals_url = url
            elif any(k in name for k in ("instrum", "no_vocals", "music")) and not instrum_url:
                instrum_url = url
        if not vocals_url:
            raise self._fail("no vocals stem in the result file list", token)

        vocals_path = os.path.join(out_dir, "vocals.wav")
        await self._download(client, vocals_url, vocals_path, token)
        no_vocals_path: Optional[str] = None
        if instrum_url:
            no_vocals_path = os.path.join(out_dir, "no_vocals.wav")
            await self._download(client, instrum_url, no_vocals_path, token)
        return vocals_path, no_vocals_path

    async def _download(self, client, url, dest, token) -> None:
        try:
            async with client.stream("GET", url) as resp:
                if not (200 <= resp.status_code < 300):
                    raise self._fail(f"stem download failed (HTTP {resp.status_code})", token)
                with open(dest, "wb") as fh:
                    async for chunk in resp.aiter_bytes():
                        fh.write(chunk)
        except RuntimeError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise self._fail(f"stem download failed ({type(exc).__name__})", token)

    async def _cancel_remote(self, base, job_hash, token) -> None:
        """Best-effort remote cancel on dub abort — never raises."""
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    f"{base}/api/separation/cancel",
                    data={"api_token": token, "hash": job_hash},
                )
        except Exception:  # noqa: BLE001
            logger.debug("MVSEP remote cancel failed (ignored)", exc_info=True)


# ── ElevenLabs Voice Isolator ───────────────────────────────────────────────


class ElevenLabsIsolationBackend(SeparationBackend):
    id = "elevenlabs-isolation"
    display_name = "ElevenLabs Voice Isolator (cloud)"
    category = "cloud"
    needs_key = "elevenlabs"
    #: The API returns the isolated VOICE only — there is no instrumental
    #: stem to hand back. Downstream treats this as has_bg=False.
    returns_background = False

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        from services import cloud_providers
        if not cloud_providers.has_key("elevenlabs"):
            return False, (
                "Configure an ElevenLabs API key in Settings → Cloud providers "
                "(or set ELEVENLABS_API_KEY)"
            )
        return True, "ready"

    async def separate(
        self, input_path: str, out_dir: str, *, job_id: str, timeout: float = 1800.0,
    ) -> AsyncIterator[tuple]:
        from core.scrub import scrub_text
        from services import cloud_providers

        key = cloud_providers.resolve_api_key("elevenlabs")
        if not key:
            raise RuntimeError(
                "No ElevenLabs API key configured — add one in Settings → Cloud providers"
            )
        yield ("progress", 5)

        import httpx

        raw_path = os.path.join(out_dir, "isolated_raw")
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(min(timeout, 900.0), connect=15.0),
                follow_redirects=True,
            ) as client:
                with open(input_path, "rb") as fh:
                    resp = await client.post(
                        f"{cloud_providers.elevenlabs_base_url()}/v1/audio-isolation",
                        headers={"xi-api-key": key},
                        files={"audio": (os.path.basename(input_path), fh,
                                         "application/octet-stream")},
                    )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"ElevenLabs voice isolation request failed: {type(exc).__name__}: {exc}"
            ) from exc
        if resp.status_code in (401, 403):
            raise RuntimeError(
                f"ElevenLabs rejected the API key (HTTP {resp.status_code}) — "
                "check Settings → Cloud providers"
            )
        if not (200 <= resp.status_code < 300):
            raise RuntimeError(
                f"ElevenLabs voice isolation failed with HTTP {resp.status_code}: "
                f"{scrub_text((resp.text or '')[:300])}"
            )
        if not resp.content:
            raise RuntimeError("ElevenLabs voice isolation returned no audio")
        with open(raw_path, "wb") as fh:
            fh.write(resp.content)
        yield ("progress", 85)

        # The API answers in a compressed container (mp3 by default) —
        # normalize to WAV so every consumer of vocals_path can read it.
        from services.ffmpeg_utils import find_ffmpeg, run_ffmpeg

        vocals_path = os.path.join(out_dir, "vocals.wav")
        rc, _, stderr = await run_ffmpeg(
            [find_ffmpeg(), "-y", "-i", raw_path, vocals_path], timeout=300.0,
        )
        try:
            os.remove(raw_path)
        except OSError:
            pass
        if rc != 0 or not os.path.exists(vocals_path):
            raise RuntimeError(
                "could not convert the isolated voice track to WAV: "
                f"{(stderr or b'').decode(errors='replace')[:200]}"
            )
        yield ("progress", 100)
        yield ("done", vocals_path, None)


# ── Registry + selection ────────────────────────────────────────────────────

_REGISTRY: dict[str, type[SeparationBackend]] = {
    "demucs-local": DemucsLocalBackend,
    "mvsep": MVSEPBackend,
    "elevenlabs-isolation": ElevenLabsIsolationBackend,
}


def list_backends() -> list[dict]:
    """Every separation engine with availability — Settings → Vocal separation."""
    from core.scrub import scrub_text

    out = []
    for bid, cls in _REGISTRY.items():
        try:
            ok, msg = cls.is_available()
        except Exception:  # noqa: BLE001 — a probe crash must not 500 Settings
            ok, msg = False, "Availability probe failed; check the backend log."
            logger.warning("separation list_backends: probe failed for %s", bid)
        out.append({
            "id": bid,
            "display_name": cls.display_name,
            "available": ok,
            "reason": None if ok else scrub_text(msg),
            "category": cls.category,
            "needs_key": cls.needs_key,
            "returns_background": cls.returns_background,
        })
    return out


def stored_backend_id() -> Optional[str]:
    from services import settings_store
    raw = (settings_store.get_text(_SEPARATION_BACKEND_KEY) or "").strip()
    return raw if raw in _REGISTRY else None


def active_backend_id() -> str:
    """env pin → stored selection → local default."""
    env_pick = (os.environ.get("OMNIVOICE_SEPARATION_BACKEND") or "").strip()
    if env_pick and env_pick in _REGISTRY:
        return env_pick
    return stored_backend_id() or DEFAULT_BACKEND_ID


def set_active_backend(bid: str) -> None:
    from services import settings_store
    if bid not in _REGISTRY:
        raise ValueError(f"Unknown separation backend: {bid!r}. Known: {list(_REGISTRY)}")
    settings_store.set_text(_SEPARATION_BACKEND_KEY, bid)


def get_active_separation_backend() -> SeparationBackend:
    """Instantiate the active engine, falling back to local Demucs when a
    cloud choice is unusable (key cleared, package gone) — a knowably-broken
    engine must not guarantee a failed separation stage."""
    bid = active_backend_id()
    cls = _REGISTRY[bid]
    if bid != DEFAULT_BACKEND_ID:
        try:
            ok, reason = cls.is_available()
        except Exception:  # noqa: BLE001
            ok, reason = False, "availability probe crashed"
        if not ok:
            logger.warning(
                "Separation backend %s unavailable (%s) — falling back to %s",
                bid, reason, DEFAULT_BACKEND_ID,
            )
            cls = _REGISTRY[DEFAULT_BACKEND_ID]
    return cls()


async def separate_collect(
    backend: SeparationBackend,
    input_path: str,
    out_dir: str,
    *,
    job_id: str,
    timeout: float = 1800.0,
) -> tuple[str, Optional[str]]:
    """Drain ``separate()`` ignoring progress — for callers that only want
    the stems (``/system/clean-audio``). Returns (vocals, no_vocals|None)."""
    vocals_path: Optional[str] = None
    no_vocals_path: Optional[str] = None
    async for evt in backend.separate(input_path, out_dir, job_id=job_id, timeout=timeout):
        if evt[0] == "done":
            vocals_path, no_vocals_path = evt[1], evt[2]
    if not vocals_path:
        raise RuntimeError("separation finished without producing a vocals stem")
    return vocals_path, no_vocals_path
