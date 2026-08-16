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
    # Placeholder stream (vcodec none + acodec none) is filtered out. The
    # video-only DASH row is returned with an audio fallback selector so the
    # frontend's single-value picker cannot download a silent video.
    ids = [f["id"] for f in out["formats"]]
    assert ids == ["100+bestaudio/best", "80"], ids
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
    assert "100+bestaudio/best" in ids


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
            format_id="100+bestaudio/best",
        )

    assert _FakeYDL.captured.get("format") == "100+bestaudio/best", (
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


def test_bilibili_ref_preserves_bvid_case_and_anthology_page():
    ref = dub_pipeline._bilibili_ref(
        "https://www.bilibili.com/video/BV13x41117TL?p=7"
    )

    assert ref == {"bvid": "BV13x41117TL", "page": 7}
    assert dub_pipeline._bilibili_qn_from_selector("30064+bestaudio/best") == 64
    assert dub_pipeline._bilibili_ref("https://example.com/video/BV13x41117TL") is None


def test_bilibili_api_fallback_resolves_selected_anthology_page(monkeypatch):
    calls = []

    def fake_api(path, query, cookie_file=None):
        calls.append((path, query))
        if path == "x/web-interface/view":
            return {
                "bvid": "BV13x41117TL",
                "title": "Example anthology",
                "duration": 99,
                "pic": "https://example.com/thumb.jpg",
                "owner": {"name": "Uploader"},
                "pages": [
                    {"page": 1, "cid": 101, "part": "First"},
                    {"page": 2, "cid": 202, "part": "Second"},
                ],
            }
        assert path == "x/player/playurl"
        assert query["cid"] == 202
        return {
            "accept_quality": [80, 64],
            "support_formats": [
                {"quality": 80, "new_description": "1080P"},
                {"quality": 64, "new_description": "720P"},
            ],
        }

    monkeypatch.setattr(dub_pipeline, "_bilibili_api_json", fake_api)
    info, ref, page = dub_pipeline._bilibili_info_from_api(
        "https://www.bilibili.com/video/BV13x41117TL?p=2"
    )

    assert ref == {"bvid": "BV13x41117TL", "page": 2}
    assert page["cid"] == 202
    assert info["title"].endswith("p02 Second")
    assert [row["id"] for row in info["formats"]] == ["80", "64"]
    assert calls[1][1]["bvid"] == "BV13x41117TL"


def test_resolve_uses_bilibili_api_after_webpage_412(monkeypatch):
    import yt_dlp

    class _FailingYDL(_FakeYDL):
        def extract_info(self, url, download=False, process=False):
            raise RuntimeError("HTTP Error 412: Precondition Failed")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FailingYDL)
    monkeypatch.setattr(
        dub_pipeline,
        "_bilibili_info_from_api",
        lambda url, cookie_file=None: ({
            "title": "API fallback",
            "uploader": "Uploader",
            "duration": 1,
            "thumbnail": "",
            "extractor_key": "BiliBili",
            "webpage_url": url,
            "formats": [{"id": "64"}],
        }, {"bvid": "BV13x41117TL", "page": 1}, {"cid": 1}),
    )

    out = dub_pipeline.resolve_video_info(
        "https://www.bilibili.com/video/BV13x41117TL"
    )

    assert out["title"] == "API fallback"
    assert out["formats"][0]["id"] == "64"


def test_download_uses_bilibili_api_after_webpage_412(tmp_path, monkeypatch):
    import yt_dlp

    class _FallbackYDL:
        last_opts = None

        def __init__(self, opts):
            type(self).last_opts = dict(opts)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=True, process=True):
            raise RuntimeError("HTTP Error 412: Precondition Failed")

        def download(self, urls):
            with open(self.last_opts["outtmpl"], "wb") as output:
                output.write(b"test mp4")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FallbackYDL)
    monkeypatch.setattr(dub_pipeline, "find_ffmpeg", lambda: None)
    monkeypatch.setattr(dub_pipeline, "_ensure_browser_playable_mp4", lambda path: path)
    monkeypatch.setattr(
        dub_pipeline,
        "_bilibili_info_from_api",
        lambda url, cookie_file=None: ({
            "title": "API fallback",
            "formats": [{"id": "64"}],
        }, {"bvid": "BV13x41117TL", "page": 1}, {"cid": 1}),
    )
    monkeypatch.setattr(
        dub_pipeline,
        "_bilibili_play_info",
        lambda ref, cid, qn=80, cookie_file=None: {
            "durl": [{"url": "https://video.example/media.mp4"}],
        },
    )

    path, title, subtitles = dub_pipeline.yt_download_sync(
        "https://www.bilibili.com/video/BV13x41117TL",
        str(tmp_path),
        format_id="64",
    )

    assert path.endswith("original.mp4")
    assert os.path.isfile(path)
    assert title == "API fallback"
    assert subtitles == []
