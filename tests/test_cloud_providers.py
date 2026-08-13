"""Cloud provider credential registry (services.cloud_providers) — the shared
env → encrypted-store key resolution + probe used by every cloud engine
(ElevenLabs TTS/ASR/isolation, DashScope TTS/ASR, MVSEP separation).

settings_store backed by in-memory dicts, httpx faked at the client boundary
(no network) — house convention, same as test_asr_openai_compat_877.py:
direct handler calls, no TestClient, so the loopback auth guard isn't in play.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")


@pytest.fixture
def ss(monkeypatch):
    from services import settings_store as _ss

    text: dict[str, str] = {}
    secrets: dict[str, str] = {}
    monkeypatch.setattr(_ss, "get_text", lambda k, default=None: text.get(k, default))
    monkeypatch.setattr(_ss, "set_text", lambda k, v: text.__setitem__(k, v))
    monkeypatch.setattr(_ss, "get_secret", lambda n: secrets.get(n))
    monkeypatch.setattr(
        _ss, "set_secret", lambda n, v: secrets.__setitem__(n, v) if v else secrets.pop(n, None)
    )
    monkeypatch.setattr(_ss, "list_secret_names", lambda: list(secrets))
    return _ss


@pytest.fixture
def cp(ss, monkeypatch):
    for var in ("ELEVENLABS_API_KEY", "DASHSCOPE_API_KEY", "MVSEP_API_TOKEN",
                "ELEVENLABS_BASE_URL", "MVSEP_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    import importlib
    return importlib.import_module("services.cloud_providers")


@pytest.fixture
def settings_mod(cp):
    import importlib
    return importlib.import_module("api.routers.settings")


def _fake_httpx(monkeypatch, *, status_code=200, json_data=None, text="", raise_exc=None):
    import httpx

    captured: dict = {}

    class _Resp:
        def __init__(self):
            self.status_code = status_code
            self.text = text

        def json(self):
            if json_data is None:
                raise ValueError("no json")
            return json_data

    class _Client:
        def __init__(self, **kw):
            captured["client_kwargs"] = kw

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None, params=None):
            captured["url"] = url
            captured["headers"] = headers or {}
            captured["params"] = params or {}
            if raise_exc is not None:
                raise raise_exc
            return _Resp()

    monkeypatch.setattr(httpx, "Client", _Client)
    return captured


# ── key resolution ───────────────────────────────────────────────────────────


def test_resolve_key_env_wins_over_store(cp, ss, monkeypatch):
    ss.set_secret(cp.SECRET_PREFIX + "elevenlabs", "sk-stored")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk-env")
    assert cp.resolve_api_key("elevenlabs") == "sk-env"
    assert cp.key_from_env("elevenlabs") is True


def test_resolve_key_falls_back_to_encrypted_store(cp, ss):
    ss.set_secret(cp.SECRET_PREFIX + "mvsep", "tok-stored")
    assert cp.resolve_api_key("mvsep") == "tok-stored"
    assert cp.resolve_api_key("dashscope") is None
    assert cp.resolve_api_key("nope") is None


def test_has_key_checks_existence_without_decrypting(cp, ss, monkeypatch):
    calls = []
    monkeypatch.setattr(
        ss, "get_secret",
        lambda n: calls.append(n) or None,
    )
    ss.set_secret(cp.SECRET_PREFIX + "dashscope", "sk-x")
    assert cp.has_key("dashscope") is True
    assert calls == []  # existence check must not round-trip the plaintext


def test_save_key_roundtrip_and_empty_clears(cp, ss):
    cp.save_key("elevenlabs", "sk-abc")
    assert cp.has_key("elevenlabs") is True
    cp.save_key("elevenlabs", "")
    assert cp.has_key("elevenlabs") is False
    with pytest.raises(ValueError):
        cp.save_key("nope", "sk-x")


def test_describe_never_contains_key_material(cp, ss):
    cp.save_key("elevenlabs", "sk-secret-value")
    d = cp.describe(cp.get_provider("elevenlabs"))
    assert d["has_key"] is True
    assert "sk-secret-value" not in str(d)


# ── probes ──────────────────────────────────────────────────────────────────


def test_probe_not_configured_and_unknown(cp):
    assert cp.probe("elevenlabs")["status"] == "not_configured"
    assert cp.probe("nope")["status"] == "unknown_provider"


def test_probe_elevenlabs_sends_xi_api_key_header(cp, ss, monkeypatch):
    cp.save_key("elevenlabs", "sk-11")
    captured = _fake_httpx(monkeypatch, json_data={})
    out = cp.probe("elevenlabs")
    assert out["ok"] is True and out["status"] == "ok"
    assert captured["url"].endswith("/v1/user")
    assert captured["headers"] == {"xi-api-key": "sk-11"}
    assert "sk-11" not in str(out)


def test_probe_dashscope_uses_compatible_mode_models(cp, ss, monkeypatch):
    cp.save_key("dashscope", "sk-ds")
    captured = _fake_httpx(monkeypatch, json_data={})
    out = cp.probe("dashscope")
    assert out["ok"] is True
    assert captured["url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1/models"
    assert captured["headers"] == {"Authorization": "Bearer sk-ds"}


def test_probe_mvsep_http200_success_false_is_auth_failed(cp, ss, monkeypatch):
    cp.save_key("mvsep", "tok-bad")
    _fake_httpx(monkeypatch, json_data={"success": False})
    out = cp.probe("mvsep")
    assert out["ok"] is False
    assert out["status"] == "auth_failed"


def test_probe_mvsep_token_travels_as_query_param(cp, ss, monkeypatch):
    cp.save_key("mvsep", "tok-good")
    captured = _fake_httpx(monkeypatch, json_data={"success": True})
    out = cp.probe("mvsep")
    assert out["ok"] is True
    assert captured["params"] == {"api_token": "tok-good"}
    assert "tok-good" not in str(out)


def test_probe_classifies_auth_timeout_unreachable(cp, ss, monkeypatch):
    import httpx

    cp.save_key("elevenlabs", "sk-x")
    _fake_httpx(monkeypatch, status_code=401)
    assert cp.probe("elevenlabs")["status"] == "auth_failed"
    _fake_httpx(monkeypatch, raise_exc=httpx.ConnectTimeout("slow"))
    assert cp.probe("elevenlabs")["status"] == "timeout"
    _fake_httpx(monkeypatch, raise_exc=httpx.ConnectError("refused"))
    assert cp.probe("elevenlabs")["status"] == "unreachable"


def test_probe_http_error_detail_redacts_the_key(cp, ss, monkeypatch):
    cp.save_key("elevenlabs", "sk-leaky-key-123")
    _fake_httpx(monkeypatch, status_code=500, text="server error echoing sk-leaky-key-123")
    out = cp.probe("elevenlabs")
    assert out["status"] == "http_error"
    assert "sk-leaky-key-123" not in (out["detail"] or "")


# ── settings routes ─────────────────────────────────────────────────────────


def test_route_lists_all_three_providers(settings_mod):
    out = settings_mod.list_cloud_providers()
    ids = [p["id"] for p in out["providers"]]
    assert ids == ["elevenlabs", "dashscope", "mvsep"]
    assert all(p["has_key"] is False for p in out["providers"])


def test_route_saves_key_and_never_echoes_it(settings_mod):
    out = settings_mod.save_cloud_provider(
        "mvsep", settings_mod._CloudProviderBody(api_key="tok-777")
    )
    row = next(p for p in out["providers"] if p["id"] == "mvsep")
    assert row["has_key"] is True
    assert "tok-777" not in str(out)
    # '' clears; None leaves unchanged
    settings_mod.save_cloud_provider("mvsep", settings_mod._CloudProviderBody(api_key=None))
    assert next(
        p for p in settings_mod.list_cloud_providers()["providers"] if p["id"] == "mvsep"
    )["has_key"] is True
    settings_mod.save_cloud_provider("mvsep", settings_mod._CloudProviderBody(api_key=""))
    assert next(
        p for p in settings_mod.list_cloud_providers()["providers"] if p["id"] == "mvsep"
    )["has_key"] is False


def test_route_rejects_unknown_provider(settings_mod):
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        settings_mod.save_cloud_provider("nope", settings_mod._CloudProviderBody(api_key="x"))
    with pytest.raises(HTTPException):
        settings_mod.test_cloud_provider("nope")


def test_route_test_probes_persisted_key(settings_mod, cp, monkeypatch):
    settings_mod.save_cloud_provider(
        "elevenlabs", settings_mod._CloudProviderBody(api_key="sk-live")
    )
    captured = _fake_httpx(monkeypatch, json_data={})
    out = settings_mod.test_cloud_provider("elevenlabs")
    assert out["ok"] is True
    assert captured["headers"] == {"xi-api-key": "sk-live"}
    assert "sk-live" not in str(out)
