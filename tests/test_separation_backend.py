"""Vocal-separation engine registry (services.separation_backend) — local
Demucs (the previous inline dub_pipeline implementation, now an engine),
MVSEP, and ElevenLabs Voice Isolator behind one async-generator surface.

settings_store in-memory, subprocess/HTTP boundaries faked, event loops via
asyncio.run (house convention — see test_stream_stderr_sync_fallback.py).
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
def sb(ss, monkeypatch):
    for var in ("OMNIVOICE_SEPARATION_BACKEND", "MVSEP_SEP_TYPE", "MVSEP_BASE_URL",
                "ELEVENLABS_API_KEY", "MVSEP_API_TOKEN", "ELEVENLABS_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    import importlib
    return importlib.import_module("services.separation_backend")


async def _collect(gen):
    events = []
    async for evt in gen:
        events.append(evt)
    return events


# ── registry + selection ────────────────────────────────────────────────────


def test_registry_lists_local_default_plus_two_cloud_engines(sb):
    rows = sb.list_backends()
    by_id = {r["id"]: r for r in rows}
    assert set(by_id) == {"demucs-local", "mvsep", "elevenlabs-isolation"}
    assert by_id["demucs-local"]["category"] == "local"
    assert by_id["demucs-local"]["needs_key"] is None
    assert by_id["mvsep"]["needs_key"] == "mvsep"
    assert by_id["mvsep"]["returns_background"] is True
    assert by_id["elevenlabs-isolation"]["returns_background"] is False


def test_active_backend_resolution_order(sb, ss, monkeypatch):
    assert sb.active_backend_id() == "demucs-local"  # default
    sb.set_active_backend("mvsep")
    assert sb.active_backend_id() == "mvsep"  # stored selection
    monkeypatch.setenv("OMNIVOICE_SEPARATION_BACKEND", "elevenlabs-isolation")
    assert sb.active_backend_id() == "elevenlabs-isolation"  # env pin wins
    monkeypatch.setenv("OMNIVOICE_SEPARATION_BACKEND", "bogus")
    assert sb.active_backend_id() == "mvsep"  # invalid env pin is ignored


def test_set_active_backend_rejects_unknown_id(sb):
    with pytest.raises(ValueError):
        sb.set_active_backend("bogus")


def test_unusable_cloud_choice_falls_back_to_local_demucs(sb, ss):
    """A selected cloud engine whose key was cleared must not guarantee a
    failed separation stage — use time falls back to local Demucs."""
    sb.set_active_backend("mvsep")  # no MVSEP token configured
    engine = sb.get_active_separation_backend()
    assert engine.id == "demucs-local"
    ss.set_secret("cloud_key.mvsep", "tok-1")
    engine = sb.get_active_separation_backend()
    assert engine.id == "mvsep"


def test_mvsep_sep_type_resolution(sb, ss, monkeypatch):
    assert sb.resolve_mvsep_sep_type() == "40"  # default
    ss.set_text(sb._MVSEP_SEP_TYPE_KEY, "48")
    assert sb.resolve_mvsep_sep_type() == "48"
    ss.set_text(sb._MVSEP_SEP_TYPE_KEY, "not-a-number")
    assert sb.resolve_mvsep_sep_type() == "40"  # garbage → default
    monkeypatch.setenv("MVSEP_SEP_TYPE", "25")
    assert sb.resolve_mvsep_sep_type() == "25"  # env wins


# ── local demucs engine ─────────────────────────────────────────────────────


def test_demucs_local_streams_progress_and_moves_stems(sb, monkeypatch, tmp_path):
    from services import dub_pipeline

    input_path = tmp_path / "audio_hq.wav"
    input_path.write_bytes(b"RIFF")

    captured_cmd: dict = {}

    async def _fake_stream(job_id, cmd, *, timeout=1800.0):
        captured_cmd["job_id"] = job_id
        captured_cmd["cmd"] = cmd
        # Simulate demucs writing stems, then the tqdm bar + exit.
        out_root = tmp_path / "htdemucs" / "audio_hq"
        out_root.mkdir(parents=True)
        (out_root / "vocals.wav").write_bytes(b"v")
        (out_root / "no_vocals.wav").write_bytes(b"b")
        yield ("stderr", " 10%|#         |")
        yield ("stderr", " 10%|#         |")  # duplicate percent — deduped
        yield ("stderr", "100%|##########|")
        yield ("done", 0, b"")

    monkeypatch.setattr(dub_pipeline, "run_proc_streaming_stderr", _fake_stream)
    monkeypatch.setitem(
        sys.modules, "services.model_manager",
        types.SimpleNamespace(get_best_device=lambda: "cpu"),
    )

    engine = sb.DemucsLocalBackend()
    events = asyncio.run(_collect(
        engine.separate(str(input_path), str(tmp_path), job_id="job1")
    ))
    assert [e for e in events if e[0] == "progress"] == [("progress", 10), ("progress", 100)]
    done = events[-1]
    assert done[0] == "done"
    assert done[1] == str(tmp_path / "vocals.wav") and os.path.exists(done[1])
    assert done[2] == str(tmp_path / "no_vocals.wav") and os.path.exists(done[2])
    assert not (tmp_path / "htdemucs").exists()  # scratch dir cleaned up
    assert captured_cmd["job_id"] == "job1"
    assert "-m" in captured_cmd["cmd"] and "demucs.separate" in captured_cmd["cmd"]
    assert "htdemucs" in captured_cmd["cmd"]


def test_demucs_local_nonzero_exit_raises(sb, monkeypatch, tmp_path):
    from services import dub_pipeline

    async def _fake_stream(job_id, cmd, *, timeout=1800.0):
        yield ("done", 2, b"CUDA out of memory")

    monkeypatch.setattr(dub_pipeline, "run_proc_streaming_stderr", _fake_stream)
    monkeypatch.setitem(
        sys.modules, "services.model_manager",
        types.SimpleNamespace(get_best_device=lambda: "cpu"),
    )
    engine = sb.DemucsLocalBackend()
    with pytest.raises(RuntimeError, match="CUDA out of memory"):
        asyncio.run(_collect(
            engine.separate(str(tmp_path / "in.wav"), str(tmp_path), job_id="j")
        ))


# ── MVSEP engine ────────────────────────────────────────────────────────────


class _FakeAsyncResp:
    def __init__(self, status_code=200, json_body=None, text=""):
        self.status_code = status_code
        self._json = json_body
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class _FakeStreamCtx:
    def __init__(self, payload: bytes):
        self._payload = payload
        self.status_code = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_bytes(self):
        yield self._payload


def _fake_async_client(monkeypatch, *, post_responses, get_responses, downloads):
    """httpx.AsyncClient fake: queued POST/GET JSON responses + streamed
    downloads keyed by URL substring."""
    import httpx

    captured = {"posts": [], "gets": [], "streams": []}

    class _Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, files=None, data=None):
            captured["posts"].append({"url": url, "data": data or {}, "files": files})
            return post_responses.pop(0)

        async def get(self, url, params=None):
            captured["gets"].append({"url": url, "params": params or {}})
            return get_responses.pop(0)

        def stream(self, method, url):
            captured["streams"].append(url)
            for key, payload in downloads.items():
                if key in url:
                    return _FakeStreamCtx(payload)
            return _FakeStreamCtx(b"")

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return captured


def test_mvsep_upload_poll_download_flow(sb, ss, monkeypatch, tmp_path):
    ss.set_secret("cloud_key.mvsep", "tok-1")
    monkeypatch.setattr(sb, "_MVSEP_POLL_INTERVAL_S", 0.0)
    input_path = tmp_path / "audio_hq.wav"
    input_path.write_bytes(b"RIFF")

    captured = _fake_async_client(
        monkeypatch,
        post_responses=[
            _FakeAsyncResp(json_body={"success": True, "data": {"hash": "h-123"}}),
        ],
        get_responses=[
            _FakeAsyncResp(json_body={"success": True, "status": "waiting", "data": {}}),
            _FakeAsyncResp(json_body={"success": True, "status": "processing", "data": {}}),
            _FakeAsyncResp(json_body={
                "success": True, "status": "done",
                "data": {"files": [
                    {"url": "https://mvsep.com/dl/xyz_vocals.wav"},
                    {"url": "https://mvsep.com/dl/xyz_instrum.wav"},
                ]},
            }),
        ],
        downloads={"vocals": b"VOC", "instrum": b"BGM"},
    )

    engine = sb.MVSEPBackend()
    events = asyncio.run(_collect(
        engine.separate(str(input_path), str(tmp_path), job_id="j", timeout=60.0)
    ))
    pcts = [e[1] for e in events if e[0] == "progress"]
    assert pcts == [10, 12, 60, 100]  # upload → waiting → processing → done
    done = events[-1]
    assert done[0] == "done"
    assert open(done[1], "rb").read() == b"VOC"
    assert open(done[2], "rb").read() == b"BGM"
    # The create call carried the token, the chosen sep_type and WAV output.
    create = captured["posts"][0]
    assert create["data"]["api_token"] == "tok-1"
    assert create["data"]["sep_type"] == "40"
    assert create["data"]["output_format"] == "1"
    assert captured["gets"][0]["params"] == {"hash": "h-123"}


def test_mvsep_failed_job_raises_scrubbed_error(sb, ss, monkeypatch, tmp_path):
    ss.set_secret("cloud_key.mvsep", "tok-secret-99")
    monkeypatch.setattr(sb, "_MVSEP_POLL_INTERVAL_S", 0.0)
    input_path = tmp_path / "in.wav"
    input_path.write_bytes(b"RIFF")
    _fake_async_client(
        monkeypatch,
        post_responses=[
            _FakeAsyncResp(json_body={"success": True, "data": {"hash": "h"}}),
        ],
        get_responses=[
            _FakeAsyncResp(json_body={
                "success": True, "status": "failed",
                "data": {"message": "credits exhausted for tok-secret-99"},
            }),
        ],
        downloads={},
    )
    engine = sb.MVSEPBackend()
    with pytest.raises(RuntimeError) as ei:
        asyncio.run(_collect(
            engine.separate(str(input_path), str(tmp_path), job_id="j", timeout=60.0)
        ))
    assert "credits exhausted" in str(ei.value)
    assert "tok-secret-99" not in str(ei.value)


def test_mvsep_vocals_only_result_degrades_to_no_background(sb, ss, monkeypatch, tmp_path):
    ss.set_secret("cloud_key.mvsep", "tok-1")
    monkeypatch.setattr(sb, "_MVSEP_POLL_INTERVAL_S", 0.0)
    input_path = tmp_path / "in.wav"
    input_path.write_bytes(b"RIFF")
    _fake_async_client(
        monkeypatch,
        post_responses=[
            _FakeAsyncResp(json_body={"success": True, "data": {"hash": "h"}}),
        ],
        get_responses=[
            _FakeAsyncResp(json_body={
                "success": True, "status": "done",
                "data": {"files": [{"url": "https://mvsep.com/dl/only_vocals.wav"}]},
            }),
        ],
        downloads={"vocals": b"VOC"},
    )
    engine = sb.MVSEPBackend()
    events = asyncio.run(_collect(
        engine.separate(str(input_path), str(tmp_path), job_id="j", timeout=60.0)
    ))
    done = events[-1]
    assert done[0] == "done" and done[2] is None


# ── ElevenLabs isolation engine ─────────────────────────────────────────────


def test_elevenlabs_isolation_returns_vocals_only(sb, ss, monkeypatch, tmp_path):
    import httpx

    ss.set_secret("cloud_key.elevenlabs", "sk-11")
    input_path = tmp_path / "in.wav"
    input_path.write_bytes(b"RIFF")

    captured: dict = {}

    class _IsolationResp:
        status_code = 200
        content = b"MP3DATA"
        text = ""

    class _Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, files=None):
            captured.update(url=url, headers=headers or {})
            return _IsolationResp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    async def _fake_run_ffmpeg(cmd, timeout=300.0):
        # cmd = [ffmpeg, -y, -i, raw, vocals.wav]
        with open(cmd[-1], "wb") as fh:
            fh.write(b"WAVDATA")
        return 0, b"", b""

    from services import ffmpeg_utils
    monkeypatch.setattr(ffmpeg_utils, "run_ffmpeg", _fake_run_ffmpeg)
    monkeypatch.setattr(ffmpeg_utils, "find_ffmpeg", lambda: "ffmpeg")

    engine = sb.ElevenLabsIsolationBackend()
    events = asyncio.run(_collect(
        engine.separate(str(input_path), str(tmp_path), job_id="j")
    ))
    done = events[-1]
    assert done[0] == "done"
    assert done[2] is None  # no background stem, by design
    assert open(done[1], "rb").read() == b"WAVDATA"
    assert captured["url"].endswith("/v1/audio-isolation")
    assert captured["headers"] == {"xi-api-key": "sk-11"}
    assert not os.path.exists(tmp_path / "isolated_raw")  # scratch cleaned


# ── collect helper ──────────────────────────────────────────────────────────


def test_separate_collect_returns_stems_and_raises_without_vocals(sb, tmp_path):
    class _Fake(sb.SeparationBackend):
        id = "fake"
        display_name = "Fake"

        @classmethod
        def is_available(cls):
            return True, "ready"

        async def separate(self, input_path, out_dir, *, job_id, timeout=1800.0):
            yield ("progress", 50)
            yield ("done", str(tmp_path / "v.wav"), None)

    vocals, bg = asyncio.run(sb.separate_collect(
        _Fake(), "in.wav", str(tmp_path), job_id="j"
    ))
    assert vocals.endswith("v.wav") and bg is None

    class _NoDone(sb.SeparationBackend):
        id = "nodone"
        display_name = "NoDone"

        @classmethod
        def is_available(cls):
            return True, "ready"

        async def separate(self, input_path, out_dir, *, job_id, timeout=1800.0):
            yield ("progress", 1)

    with pytest.raises(RuntimeError):
        asyncio.run(sb.separate_collect(_NoDone(), "in.wav", str(tmp_path), job_id="j"))


# ── settings route ──────────────────────────────────────────────────────────


def test_separation_settings_route_roundtrip(sb, ss):
    import importlib
    settings_mod = importlib.import_module("api.routers.settings")

    st = settings_mod.get_separation()
    assert st["active"] == "demucs-local"
    assert {b["id"] for b in st["backends"]} == {"demucs-local", "mvsep", "elevenlabs-isolation"}
    assert st["mvsep_sep_type"] == "40"

    st = settings_mod.set_separation(
        settings_mod._SeparationBody(backend="mvsep", mvsep_sep_type="48")
    )
    assert st["active"] == "mvsep"
    assert st["mvsep_sep_type"] == "48"

    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        settings_mod.set_separation(settings_mod._SeparationBody(backend="bogus"))
    with pytest.raises(HTTPException):
        settings_mod.set_separation(settings_mod._SeparationBody(mvsep_sep_type="abc"))
