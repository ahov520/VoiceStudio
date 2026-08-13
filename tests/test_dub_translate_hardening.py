"""/dub/translate hardening — persistence-on-translate, fast-LLM rate-limit
handling, and bounded Argos language-pack downloads.

The three "translation mysteriously aborts / evaporates" classes this pins:

* translations only reached ``job["segments_i18n"]`` at GENERATE time, so a
  finished translate vanished on refresh/restart;
* the fast LLM path fanned out to the whole CPU pool with no concurrency cap
  and burned its single retry instantly inside the same 429 window;
* argostranslate downloads its language packs with timeout-less urllib
  sockets, hanging the request forever on a blocked network.

House convention: direct handler calls, SDK/network boundaries faked, no
TestClient.
"""
from __future__ import annotations

import asyncio
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")


@pytest.fixture
def dt(monkeypatch):
    import importlib

    mod = importlib.import_module("api.routers.dub_translate")
    monkeypatch.delenv("OMNIVOICE_LLM_CONCURRENCY", raising=False)
    return mod


def _req(dt_mod, **overrides):
    from schemas.requests import TranslateRequest

    payload = {
        "segments": [
            {"id": "1", "text": "hello there", "start": 0.0, "end": 2.0},
            {"id": "2", "text": "second line", "start": 2.0, "end": 4.0},
        ],
        "target_lang": "es",
        "provider": "openai",
        "source_lang": "en",
        "quality": "fast",
        "auto_glossary": False,
        "reflect": False,
        **overrides,
    }
    return TranslateRequest(**payload)


# ── segments_i18n persistence on translate ──────────────────────────────────


def test_persist_writes_successful_rows_only(dt, monkeypatch):
    job: dict = {"segments": []}  # non-empty — an empty dict reads as "no job"
    saved: list = []
    monkeypatch.setattr(dt, "_get_job", lambda jid: job if jid == "job1" else None)
    monkeypatch.setattr(dt, "_save_job", lambda jid, j: saved.append((jid, j)))

    resp = {
        "translated": [
            {"id": "1", "text": "hola"},
            {"id": "2", "text": "segunda"},
            {"id": "3", "text": "kept-source", "error": "llm-failed"},
            {"id": "4", "text": "   "},
        ],
        "target_lang": "es",
    }
    out = dt._persist_translated_texts(_req(dt, job_id="job1"), resp)
    assert out is resp  # passthrough — the response shape is untouched
    assert job["segments_i18n"]["es"] == {"1": "hola", "2": "segunda"}
    assert saved and saved[0][0] == "job1"


def test_persist_is_additive_across_calls(dt, monkeypatch):
    """Batched translates land one slice at a time — each call must merge
    into the language map, not replace it (the retry-failed path depends on
    the earlier rows surviving)."""
    job: dict = {"segments_i18n": {"es": {"1": "hola"}}}
    monkeypatch.setattr(dt, "_get_job", lambda jid: job)
    monkeypatch.setattr(dt, "_save_job", lambda jid, j: None)

    dt._persist_translated_texts(
        _req(dt, job_id="job1"),
        {"translated": [{"id": "2", "text": "segunda"}], "target_lang": "es"},
    )
    assert job["segments_i18n"]["es"] == {"1": "hola", "2": "segunda"}


def test_persist_skips_without_job_and_never_raises(dt, monkeypatch):
    def _boom(*a):  # pragma: no cover — must not be called without a job_id
        raise AssertionError("no job_id → no job lookup")

    monkeypatch.setattr(dt, "_get_job", _boom)
    resp = {"translated": [{"id": "1", "text": "x"}], "target_lang": "es"}
    assert dt._persist_translated_texts(_req(dt, job_id=None), resp) is resp

    # A crashing store must degrade to a no-op, not fail the translate.
    monkeypatch.setattr(dt, "_get_job", lambda jid: (_ for _ in ()).throw(RuntimeError("db down")))
    assert dt._persist_translated_texts(_req(dt, job_id="job1"), resp) is resp


# ── fast-LLM concurrency gate ───────────────────────────────────────────────


def test_llm_fast_gate_sizes_from_env_and_is_reused(dt, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_LLM_CONCURRENCY", "2")
    g1 = dt._get_llm_fast_gate()
    g2 = dt._get_llm_fast_gate()
    assert g1 is g2
    assert dt._llm_fast_gate_size == 2
    monkeypatch.setenv("OMNIVOICE_LLM_CONCURRENCY", "4")
    g3 = dt._get_llm_fast_gate()
    assert g3 is not g1
    assert dt._llm_fast_gate_size == 4
    # Garbage falls back to the default instead of crashing the route.
    monkeypatch.setenv("OMNIVOICE_LLM_CONCURRENCY", "not-a-number")
    assert dt._get_llm_fast_gate() is not None
    assert dt._llm_fast_gate_size == 6


# ── fast-LLM 429 handling through the route ────────────────────────────────


class _RateLimited(Exception):
    status_code = 429

    def __init__(self):
        super().__init__("rate limited")
        self.response = types.SimpleNamespace(headers={"Retry-After": "3"})


def _fake_llm_handle(create_fn):
    client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create_fn))
    )
    return types.SimpleNamespace(client=client, model="test-model", timeout=5)


def test_fast_llm_waits_out_retry_after_then_succeeds(dt, monkeypatch):
    from services import llm_skills

    calls = {"n": 0}

    def _create(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _RateLimited()
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="hola"))]
        )

    monkeypatch.setattr(llm_skills, "resolve_skill_client", lambda skill: _fake_llm_handle(_create))
    sleeps: list = []
    monkeypatch.setattr(dt.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(dt.random, "uniform", lambda a, b: 0.0)

    req = _req(dt, segments=[{"id": "1", "text": "hello", "start": 0.0, "end": 2.0}])
    out = asyncio.run(dt.dub_translate(req))
    assert out["translated"][0]["text"] == "hola"
    assert "error" not in out["translated"][0]
    # The retry waited for the provider's Retry-After hint (3s, capped).
    assert sleeps and sleeps[0] == pytest.approx(3.0)
    assert calls["n"] == 2


def test_fast_llm_two_failures_keep_source_with_scrubbed_error(dt, monkeypatch):
    from services import llm_skills

    def _create(**kw):
        raise _RateLimited()

    monkeypatch.setattr(llm_skills, "resolve_skill_client", lambda skill: _fake_llm_handle(_create))
    monkeypatch.setattr(dt.time, "sleep", lambda s: None)

    req = _req(dt, segments=[{"id": "1", "text": "hello", "start": 0.0, "end": 2.0}])
    out = asyncio.run(dt.dub_translate(req))
    row = out["translated"][0]
    assert row["text"] == "hello"  # source kept, never dropped
    assert row["error"]


# ── Argos language-pack download bounds ─────────────────────────────────────


def _fake_argos(monkeypatch, *, index_raises=None):
    """Install a fake argostranslate into sys.modules: no packages installed,
    and the index update either raises (blocked network) or yields nothing."""
    pkg = types.ModuleType("argostranslate.package")
    pkg.get_installed_packages = lambda: []
    pkg.get_available_packages = lambda: []

    def _update_index():
        if index_raises is not None:
            raise index_raises
        return None

    pkg.update_package_index = _update_index
    pkg.install_from_path = lambda p: None
    trans = types.ModuleType("argostranslate.translate")
    trans.translate = lambda text, a, b: text
    root = types.ModuleType("argostranslate")
    root.package = pkg
    root.translate = trans
    monkeypatch.setitem(sys.modules, "argostranslate", root)
    monkeypatch.setitem(sys.modules, "argostranslate.package", pkg)
    monkeypatch.setitem(sys.modules, "argostranslate.translate", trans)


def test_argos_download_socket_error_is_actionable_and_timeout_restored(dt, monkeypatch):
    import socket

    _fake_argos(monkeypatch, index_raises=TimeoutError("timed out"))
    sentinel = 123.0
    socket.setdefaulttimeout(sentinel)
    try:
        req = _req(dt, provider="argos",
                   segments=[{"id": "1", "text": "hello", "start": 0.0, "end": 2.0}])
        out = asyncio.run(dt.dub_translate(req))
        row = out["translated"][0]
        assert row["text"] == "hello"
        assert "language pack" in row["error"]
        # The bounded window restored the previous process-wide default.
        assert socket.getdefaulttimeout() == sentinel
    finally:
        socket.setdefaulttimeout(None)


def test_argos_missing_pack_error_names_the_pair(dt, monkeypatch):
    _fake_argos(monkeypatch)
    req = _req(dt, provider="argos",
               segments=[{"id": "1", "text": "hello", "start": 0.0, "end": 2.0}])
    out = asyncio.run(dt.dub_translate(req))
    assert "No Argos package available" in out["translated"][0]["error"]
