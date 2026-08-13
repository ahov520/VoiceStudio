"""Cloud speech-service credential registry — ElevenLabs / DashScope / MVSEP.

The cloud TTS/ASR/separation engines (services.tts_cloud, services.asr_cloud,
services.separation_backend) share one provider key each, entered once in
Settings → Cloud providers and consumed by every engine of that provider.
This module is the one place that knows the env var names, the encrypted
settings-store secret names, and how to run a cheap "does this key work"
probe per provider.

Resolution precedence for a key (mirrors services.llm_providers):
    1. Environment variable — power-user / CI override, wins always.
    2. Encrypted settings store (UI-entered) — ``settings_store.get_secret``
       under ``cloud_key.<provider_id>``. Never echoed back to the client.

Local-first contract: nothing in this module performs network I/O except the
explicit ``probe()`` (the Settings "Test key" button). Every cloud engine is
opt-in — with no key configured the engines simply report unavailable.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("omnivoice.cloud_providers")

#: settings_store secret name prefix (Fernet-encrypted at rest).
SECRET_PREFIX = "cloud_key."


@dataclass(frozen=True)
class CloudProvider:
    id: str
    display_name: str
    #: Env var names checked (in order) for the API key. First one set wins.
    key_envs: tuple[str, ...]
    signup_url: str = ""
    #: Which capabilities this provider's key unlocks (UI hint only).
    capabilities: tuple[str, ...] = ()


_PROVIDERS: tuple[CloudProvider, ...] = (
    CloudProvider(
        "elevenlabs", "ElevenLabs",
        key_envs=("ELEVENLABS_API_KEY",),
        signup_url="https://elevenlabs.io/app/developers",
        capabilities=("tts", "asr", "separation"),
    ),
    CloudProvider(
        "dashscope", "Alibaba Cloud Model Studio (DashScope)",
        key_envs=("DASHSCOPE_API_KEY",),
        signup_url="https://bailian.console.aliyun.com/?apiKey=1",
        capabilities=("tts", "asr"),
    ),
    CloudProvider(
        "mvsep", "MVSEP",
        key_envs=("MVSEP_API_TOKEN",),
        signup_url="https://mvsep.com/en/profile",
        capabilities=("separation",),
    ),
)

_BY_ID: dict[str, CloudProvider] = {p.id: p for p in _PROVIDERS}


def all_providers() -> tuple[CloudProvider, ...]:
    return _PROVIDERS


def get_provider(pid: str) -> Optional[CloudProvider]:
    return _BY_ID.get(pid)


def _env_first(names: tuple[str, ...]) -> Optional[str]:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return None


def resolve_api_key(pid: str) -> Optional[str]:
    """Env key → encrypted stored key → None. Never logs the value."""
    p = _BY_ID.get(pid)
    if p is None:
        return None
    env_key = _env_first(p.key_envs)
    if env_key:
        return env_key
    from services import settings_store
    return settings_store.get_secret(SECRET_PREFIX + pid)


def key_from_env(pid: str) -> bool:
    p = _BY_ID.get(pid)
    return bool(p and _env_first(p.key_envs))


def has_key(pid: str) -> bool:
    """Whether a key is configured, without decrypting the stored one —
    mirrors llm_providers.has_key()'s no-plaintext-round-trip contract."""
    p = _BY_ID.get(pid)
    if p is None:
        return False
    if _env_first(p.key_envs):
        return True
    from services import settings_store
    return (SECRET_PREFIX + pid) in settings_store.list_secret_names()


def save_key(pid: str, api_key: str) -> None:
    """Persist (encrypted) or clear ('' clears) a provider key."""
    if pid not in _BY_ID:
        raise ValueError(f"unknown cloud provider {pid!r}")
    from services import settings_store
    settings_store.set_secret(SECRET_PREFIX + pid, (api_key or "").strip())


def describe(p: CloudProvider) -> dict:
    """Client-safe descriptor — NEVER includes key material."""
    return {
        "id": p.id,
        "display_name": p.display_name,
        "signup_url": p.signup_url,
        "capabilities": list(p.capabilities),
        "has_key": has_key(p.id),
        "key_from_env": key_from_env(p.id),
    }


# ── Key probes (Settings → Cloud providers → "Test key") ───────────────────
# One cheap authenticated GET per provider. Same structured-verdict contract
# as asr_backend.probe_openai_compat_server: never raises, never echoes the
# key, ``detail`` passes through core.scrub before it can reach the UI.

#: Base URLs, overridable for tests / gateways. MVSEP's default host is
#: geo-steered (Europe → de, North Asia → hk, elsewhere → de2), so the
#: default works everywhere; power users can pin a region via the env.
ELEVENLABS_BASE_URL_ENV = "ELEVENLABS_BASE_URL"
_ELEVENLABS_DEFAULT_BASE = "https://api.elevenlabs.io"
MVSEP_BASE_URL_ENV = "MVSEP_BASE_URL"
_MVSEP_DEFAULT_BASE = "https://mvsep.com"
_DASHSCOPE_PROBE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/models"


def elevenlabs_base_url() -> str:
    return (os.environ.get(ELEVENLABS_BASE_URL_ENV) or _ELEVENLABS_DEFAULT_BASE).rstrip("/")


def mvsep_base_url() -> str:
    return (os.environ.get(MVSEP_BASE_URL_ENV) or _MVSEP_DEFAULT_BASE).rstrip("/")


def _probe_request(pid: str, key: str) -> tuple[str, dict, dict]:
    """(url, headers, params) for the provider's cheapest authenticated GET."""
    if pid == "elevenlabs":
        return f"{elevenlabs_base_url()}/v1/user", {"xi-api-key": key}, {}
    if pid == "dashscope":
        return _DASHSCOPE_PROBE_URL, {"Authorization": f"Bearer {key}"}, {}
    # mvsep — GET /api/app/user validates the token ({success: bool}).
    return f"{mvsep_base_url()}/api/app/user", {}, {"api_token": key}


def probe(pid: str, *, timeout_s: float = 10.0) -> dict:
    """One authenticated round-trip to prove the provider key works.

    Returns ``{ok, status, latency_ms, http_status, detail}`` where ``status``
    is one of: ``unknown_provider``, ``not_configured``, ``ok``,
    ``auth_failed`` (401/403 — or MVSEP's ``success:false`` envelope),
    ``http_error``, ``timeout``, ``unreachable``.
    """
    from time import perf_counter

    out: dict = {
        "ok": False,
        "status": "not_configured",
        "latency_ms": None,
        "http_status": None,
        "detail": None,
    }
    if pid not in _BY_ID:
        out["status"] = "unknown_provider"
        return out
    key = resolve_api_key(pid)
    if not key:
        return out

    import httpx

    url, headers, params = _probe_request(pid, key)
    t0 = perf_counter()
    try:
        with httpx.Client(
            timeout=httpx.Timeout(timeout_s, connect=min(5.0, timeout_s)),
            follow_redirects=True,
        ) as client:
            resp = client.get(url, headers=headers, params=params)
    except httpx.TimeoutException as exc:
        out.update(
            status="timeout",
            latency_ms=round((perf_counter() - t0) * 1000.0, 1),
            detail=_scrub_probe_detail(f"{type(exc).__name__}: {exc}", key),
        )
        return out
    except Exception as exc:  # noqa: BLE001 — ConnectError, SSL, DNS…
        out.update(
            status="unreachable",
            latency_ms=round((perf_counter() - t0) * 1000.0, 1),
            detail=_scrub_probe_detail(f"{type(exc).__name__}: {exc}", key),
        )
        return out

    out["latency_ms"] = round((perf_counter() - t0) * 1000.0, 1)
    out["http_status"] = resp.status_code

    if resp.status_code in (401, 403):
        out["status"] = "auth_failed"
        return out
    if not (200 <= resp.status_code < 300):
        out["status"] = "http_error"
        out["detail"] = _scrub_probe_detail((resp.text or "")[:300], key) or None
        return out

    # MVSEP answers 200 with {"success": false} for a bad token — an HTTP-200
    # auth failure that must not read as green.
    if pid == "mvsep":
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001 — non-JSON 200 still proves reachability
            body = None
        if isinstance(body, dict) and body.get("success") is False:
            out["status"] = "auth_failed"
            return out

    out.update(ok=True, status="ok")
    return out


def _scrub_probe_detail(detail: str, key: str) -> str:
    """UI-safe failure text: redact the exact key (some services echo it in
    error bodies / query-string errors), then the generic scrub pass."""
    from core.scrub import scrub_text
    if key and len(key) >= 8:
        detail = detail.replace(key, "•••")
    return scrub_text(detail)
