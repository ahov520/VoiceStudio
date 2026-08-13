"""/audiobook/import must parse in a worker thread, not on the event loop.

A multi-hundred-page PDF (pypdf) or a big EPUB takes seconds to minutes to
parse; the route used to run that inline in an ``async def``, freezing every
other request (SSE streams, audio playback, health checks) until the import
finished. The route now offloads parsing via ``asyncio.to_thread`` — these
tests pin that contract with the house direct-handler-call convention.
"""
from __future__ import annotations

import asyncio
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")


def _upload(name: str, payload: bytes):
    from fastapi import UploadFile

    return UploadFile(file=io.BytesIO(payload), filename=name)


@pytest.fixture
def ab():
    import importlib

    return importlib.import_module("api.routers.audiobook")


def test_import_runs_parsing_via_to_thread(ab, monkeypatch):
    calls: list[str] = []
    real_to_thread = asyncio.to_thread

    async def spy(fn, *args, **kwargs):
        calls.append(getattr(fn, "__name__", str(fn)))
        return await real_to_thread(fn, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", spy)
    out = asyncio.run(
        ab.audiobook_import(file=_upload("book.txt", "Chapter 1\nHello world.".encode()))
    )
    assert out["chapters"] >= 1
    assert "Hello world." in out["text"]
    # Both the format-specific parse and the plan parse ran off the loop.
    assert "_parse_import_payload" in calls
    assert "parse_audiobook_script" in calls


def test_import_rejects_empty_and_oversized_before_threading(ab, monkeypatch):
    from fastapi import HTTPException

    async def boom(fn, *a, **k):  # pragma: no cover — must never be reached
        raise AssertionError("validation failures must not spawn worker threads")

    monkeypatch.setattr(asyncio, "to_thread", boom)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(ab.audiobook_import(file=_upload("book.txt", b"")))
    assert ei.value.status_code == 400

    big = b"x" * (ab._IMPORT_MAX_BYTES + 1)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(ab.audiobook_import(file=_upload("book.txt", big)))
    assert ei.value.status_code == 400


def test_import_parse_errors_surface_as_400(ab):
    with pytest.raises(Exception) as ei:
        asyncio.run(ab.audiobook_import(file=_upload("broken.epub", b"not really an epub")))
    assert getattr(ei.value, "status_code", None) == 400
