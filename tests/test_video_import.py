"""Video URL resolve + format-override download (Bilibili / Douyin / any yt-dlp host).

Covers the /dub/ingest-url/resolve contract at the service layer:
- resolve_video_info() returns a compact, JSON-safe summary (no raw yt-dlp
  blobs), filters placeholder streams, dedupes by format id and caps rows.
- resolve failures surface a stable RuntimeError with a short message.
- yt_download_sync() honors a user-picked format_id instead of the default
  h264+aac chain, and the ingest pipeline forwards it.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from services import dub_pipeline  # noqa: E402


def _make_info(formats=None, **overrides):
    info = {
        "id": "BV1xx411c7mD",
        "title": "Example video",
        "uploader": "Some Uploader",
        "duration": 125.4,
        "thumbnail": "https://example.com/thumb.jpg",
        "extractor_key": "BiliBili",
        "webpage_url": "https://www.bilibili.com/video/BV1xx411c7mD",
        "formats": formats
        if formats is not None
        else [
            {"format_id": "100", "format_note": "1080p", "ext": "mp4", "height": 1080, "fps": 30,
             "vcodec": "avc1.640032", "acodec": "none", "filesize": 1000000},
            {"format_id": "80", "format_note": "DASH audio", "ext": "m4a", "height": None, "fps": None,
             "vcodec": "none", "acodec": "mp4a.40.2", "filesize": 500000},
            {"format_id": "30000", "format_note": "storyboard", "ext": "mhtml", "height": None, "fps": None,
             "vcodec": "none", "acodec": "none", "filesize": None},
        ],
    }
    info.update(overrides)
    return info


class _FakeYDL:
    captured: dict = {}
    info: dict

    def __init__(self, opts):
        type(self).captured = dict(opts)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=True, process=True):
        return self.info


def test_resolve_returns_sanitized_summary(monkeypatch):
    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeYDL)
    _FakeYDL.info = _make_info()

    out = dub_pipeline.resolve_video_info("https://www.bilibili.com/video/BV1xx411c7mD")

    assert out["title"] == "Example video"
    assert out["uploader"] == "Some Uploader"
    assert out["duration"] == 125  # float -> int seconds
    assert out["extractor_key"] == "BiliBili"
    # Placeholder stream (vcodec none + acodec none) filtered out; video row
    # sorts before the audio-only row.
    ids = [f["id"] for f in out["formats"]]
    assert ids == ["100", "80"], ids
    row = out["formats"][0]
    assert row["height"] == 1080 and row["vcodec"] == "avc1.640032"


def test_resolve_dedupes_and_caps_formats(monkeypatch):
    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeYDL)
    dup = {"format_id": "100", "format_note": "1080p dup", "ext": "mp4", "height": 1080,
           "fps": 30, "vcodec": "avc1", "acodec": "none", "filesize": 1}
    _FakeYDL.info = _make_info(formats=[
        {"format_id": str(i), "format_note": f"h{i}", "ext": "mp4", "height": i,
         "fps": 30, "vcodec": "avc1", "acodec": "none", "filesize": 1}
        for i in range(1080, 0, -10)
    ] + [dup])

    out = dub_pipeline.resolve_video_info("https://example.com/v")

    assert len(out["formats"]) == dub_pipeline._RESOLVE_MAX_FORMATS
    ids = [f["id"] for f in out["formats"]]
    assert len(ids) == len(set(ids)), "duplicate format ids must be deduped"
    assert "100" in ids


def test_resolve_failure_raises_stable_runtime_error(monkeypatch):
    import yt_dlp

    class _FailingYDL(_FakeYDL):
        def extract_info(self, url, download=True, process=True):
            raise RuntimeError("Unsupported URL: https://example.com/nope")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FailingYDL)
    with pytest.raises(RuntimeError) as excinfo:
        dub_pipeline.resolve_video_info("https://example.com/nope")
    assert "Could not resolve" in str(excinfo.value)


def test_download_uses_format_id_override(tmp_path, monkeypatch):
    import yt_dlp

    monkeypatch.setattr(dub_pipeline, "find_ffmpeg", lambda: None)
    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeYDL)
    _FakeYDL.info = _make_info()

    with pytest.raises(Exception):
        dub_pipeline.yt_download_sync(
            "https://www.bilibili.com/video/BV1xx411c7mD",
            str(tmp_path),
            format_id="100+80",
        )

    assert _FakeYDL.captured.get("format") == "100+80", (
        "a user-picked format_id must replace the default chain"
    )


def test_download_keeps_default_chain_without_format_id(tmp_path, monkeypatch):
    import yt_dlp

    monkeypatch.setattr(dub_pipeline, "find_ffmpeg", lambda: None)
    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeYDL)
    _FakeYDL.info = _make_info()

    with pytest.raises(Exception):
        dub_pipeline.yt_download_sync("https://www.bilibili.com/video/BV1xx411c7mD", str(tmp_path))

    fmt = _FakeYDL.captured.get("format")
    assert isinstance(fmt, str) and "avc1" in fmt, "default compatibility chain must remain"
