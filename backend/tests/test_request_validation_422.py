"""A binary body to a JSON endpoint answers 422 — and never lands on disk (#1513).

Fail-before/pass-after: without ``main.request_validation_exception_handler``
FastAPI's default RequestValidationError handler runs. It jsonable-encodes the
error's ``input`` — the raw request body — and strict UTF-8 decoding of binary
raises UnicodeDecodeError *inside the handler*: the client sees a 500 instead
of a 422, and the escaping traceback (caught by the global Exception handler)
writes the whole request body into the crash log / error journal — user audio
on disk, in the very file bug reports ask people to paste.
"""
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel

import main as main_mod

BINARY_BODY = b"\x80\x81\x82\xff" * 64  # not valid UTF-8, recognisably long


def _app():
    """A JSON-body route wired to main's REAL exception handlers — the same
    pair production registers, so the 500-inside-the-error-handler failure
    mode reproduces exactly when the validation handler is absent."""
    app = FastAPI()

    class _Body(BaseModel):
        text: str

    @app.post("/json-route")
    async def _json_route(body: _Body):  # pragma: no cover — never reached
        return {"ok": True}

    app.add_exception_handler(
        RequestValidationError, main_mod.request_validation_exception_handler
    )
    app.add_exception_handler(Exception, main_mod.global_exception_handler)
    return app


@pytest.fixture
def client():
    with TestClient(_app(), raise_server_exceptions=False) as c:
        yield c


def test_binary_multipart_body_returns_422_not_500(client):
    resp = client.post(
        "/json-route", files={"f": ("bin.dat", BINARY_BODY, "application/octet-stream")}
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail, "detail must still carry the validation errors"
    # The body is reported by size, never echoed.
    assert BINARY_BODY not in resp.content
    assert any("bytes of binary data" in str(err.get("input", "")) for err in detail)


def test_binary_body_never_reaches_crash_log_or_journal(tmp_path, monkeypatch, client):
    """The 500 path wrote the escaping UnicodeDecodeError — request body and
    all — through the crash log and error journal. A handled 422 must not."""
    from core import error_journal

    crash_log = tmp_path / "crash.log"
    monkeypatch.setattr(main_mod, "CRASH_LOG_PATH", str(crash_log))
    recorded = []
    monkeypatch.setattr(
        error_journal, "record", lambda *a, **k: recorded.append(a) or {}
    )

    resp = client.post(
        "/json-route", files={"f": ("clip.wav", BINARY_BODY, "audio/wav")}
    )

    assert resp.status_code == 422
    assert not crash_log.exists()
    assert recorded == []


def test_text_validation_error_still_answers_422_with_loc_and_msg(client):
    resp = client.post("/json-route", json={"wrong_field": 1})
    assert resp.status_code == 422
    err = resp.json()["detail"][0]
    assert err["loc"], "loc survives sanitization"
    assert err["msg"], "msg survives sanitization"


def test_allowed_origin_gets_cors_headers_on_422(client):
    """Hand-built error responses must go through _cors_headers_for — without
    it the browser reports a bare CORS failure instead of the 422 detail."""
    resp = client.post(
        "/json-route",
        json={"wrong_field": 1},
        headers={"Origin": "tauri://localhost"},
    )
    assert resp.status_code == 422
    assert resp.headers.get("Access-Control-Allow-Origin") == "tauri://localhost"


# ── _scrub_validation_value: the sanitizer itself ───────────────────────────


def test_scrub_reports_binary_by_size_only():
    out = main_mod._scrub_validation_value(b"\x80" * 145_000)
    assert out == "<145000 bytes of binary data>"


def test_scrub_truncates_long_strings():
    out = main_mod._scrub_validation_value("x" * 10_000)
    assert len(out) < 300
    assert out.startswith("x" * main_mod._VALIDATION_VALUE_MAX_CHARS)
    assert "more chars" in out


def test_scrub_walks_containers_shallowly_and_bounds_them():
    value = {"clip": b"\x80\x81", "rows": list(range(100)), "note": "ok"}
    out = main_mod._scrub_validation_value(value)
    assert out["clip"] == "<2 bytes of binary data>"
    assert len(out["rows"]) == main_mod._VALIDATION_MAX_ITEMS + 1  # + ellipsis
    assert out["note"] == "ok"


def test_scrub_stringifies_non_jsonable_ctx_values():
    # pydantic parks exception objects in ctx["error"]; they must come out as
    # bounded text, not explode jsonable_encoder.
    out = main_mod._scrub_validation_value(ValueError("boom"))
    assert isinstance(out, str)
    assert "boom" in out


def test_scrub_bounds_depth_without_decoding_nested_bytes():
    nested = {"a": {"b": {"c": {"d": {"clip": b"\x80" * 1000}}}}}
    out = main_mod._scrub_validation_value(nested)
    # Beyond max depth everything is a bounded repr — escaped, never decoded.
    import json

    text = json.dumps(out)
    assert len(text) < 2_000
