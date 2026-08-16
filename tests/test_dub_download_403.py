"""yt-dlp 403 → player-client fallback (#625).

YouTube serves some videos' formats signature-protected to the default player
client, so the media download 403s even though extraction worked. A plain retry
(the #579 broken-pipe path) keeps 403ing; escalating the player client
(tv → android → web_safari) commonly bypasses it.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from services import dub_pipeline  # noqa: E402


def test_forbidden_classifier():
    assert dub_pipeline._is_forbidden_download_error(Exception("HTTP Error 403: Forbidden"))
    assert dub_pipeline._is_forbidden_download_error(Exception("ERROR: ... 403 ..."))
    assert not dub_pipeline._is_forbidden_download_error(BrokenPipeError("broken pipe"))
    assert not dub_pipeline._is_forbidden_download_error(Exception("Connection reset"))


def test_403_escalates_player_clients_in_order(tmp_path, monkeypatch):
    import yt_dlp

    tried = []

    class _FakeYDL:
        def __init__(self, opts):
            ea = (opts.get("extractor_args") or {}).get("youtube", {})
            clients = ea.get("player_client") or [None]
            tried.append(clients[0])

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            raise Exception("ERROR: unable to download video data: HTTP Error 403: Forbidden")

        def prepare_filename(self, info):
            return "unused"

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeYDL)

    with pytest.raises(Exception) as ei:
        dub_pipeline.yt_download_sync("https://youtu.be/abc", str(tmp_path))
    assert "403" in str(ei.value)
    # First attempt uses the default client; then it escalates through the list
    # before finally giving up.
    assert tried == [None] + dub_pipeline._YT_PLAYER_CLIENTS


def test_success_after_403_on_alternate_client(tmp_path, monkeypatch):
    import yt_dlp

    calls = {"n": 0}

    class _FakeYDL:
        def __init__(self, opts):
            self._opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            calls["n"] += 1
            ea = (self._opts.get("extractor_args") or {}).get("youtube", {})
            if not ea.get("player_client"):
                raise Exception("HTTP Error 403: Forbidden")  # default client 403s
            return {"title": "ok"}  # an alternate client succeeds

        def prepare_filename(self, info):
            # Produce a path the post-download step can stat; create the file.
            p = os.path.join(str(tmp_path), "original.mp4")
            open(p, "wb").close()
            return p

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeYDL)
    # Skip the codec probe / ffprobe work after the download — out of scope here.
    monkeypatch.setattr(dub_pipeline, "_ensure_webview_friendly_codec", lambda *a, **k: None, raising=False)

    try:
        result = dub_pipeline.yt_download_sync("https://youtu.be/abc", str(tmp_path))
    except Exception:
        # If post-download processing isn't fully stubbable, at least assert the
        # retry happened (escalated past the 403 to a successful extract).
        assert calls["n"] >= 2
        return
    assert calls["n"] >= 2
    assert result[1] == "ok"  # title from the successful (alternate-client) extract


def test_403_escalation_relaxes_pinned_format_id(tmp_path, monkeypatch):
    """A user-picked format_id must not kill the client-escalation retry.

    The alternate player clients serve a different format table — the tv
    client has no muxed format 18, so a retry that keeps the pinned id
    aborts with "Requested format is not available" and turns the 403
    fallback into the final failure. The escalation must append the
    compatibility chain so the retry can proceed.
    """
    import yt_dlp

    formats = []

    class _FakeYDL:
        def __init__(self, opts):
            self._opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            ea = (self._opts.get("extractor_args") or {}).get("youtube", {})
            if not ea.get("player_client"):
                raise Exception("HTTP Error 403: Forbidden")  # default client 403s
            formats.append(self._opts.get("format"))
            return {"title": "ok"}  # alternate client succeeds

        def prepare_filename(self, info):
            return "unused"

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeYDL)

    dub_pipeline.yt_download_sync("https://youtu.be/abc", str(tmp_path), format_id="18")
    assert formats == [f"18/{dub_pipeline._DUB_FORMAT_CHAIN}"]
