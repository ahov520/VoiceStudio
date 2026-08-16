"""Subtitle-first transcription seeding (Bilibili AI 字幕 / YouTube captions).

The /dub/transcribe-stream?use_subtitle=<lang> path parses the job's
downloaded VTT files and skips local ASR entirely. These tests pin the
service helper: language matching, VTT parsing, and the [] fallback.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from api.routers import dub_core  # noqa: E402
from services import dub_pipeline  # noqa: E402

VTT_ZH = """WEBVTT

00:00:01.000 --> 00:00:03.500
你好世界

00:00:04.000 --> 00:00:06.000
<00:00:05.000>再见
"""

VTT_EN = """WEBVTT

00:00:00.500 --> 00:00:02.000
Hello world
"""


def _write_job_vtt(job_id: str, name: str, content: str) -> None:
    job_dir = dub_pipeline.safe_job_dir(job_id)
    os.makedirs(job_dir, exist_ok=True)
    with open(os.path.join(job_dir, name), "w", encoding="utf-8") as fh:
        fh.write(content)


def test_subtitle_seed_matches_lang_hint():
    job = "subseed01"
    _write_job_vtt(job, "original.zh.vtt", VTT_ZH)
    _write_job_vtt(job, "original.en.vtt", VTT_EN)
    segs = dub_core._segments_from_job_subtitles(job, "zh")
    assert len(segs) == 2
    assert segs[0]["text"] == "你好世界"
    assert segs[0]["start"] == 1.0
    assert segs[1]["text"] == "再见"
    assert segs[1]["speaker_id"] == "Speaker 1"


def test_subtitle_seed_falls_back_to_first_vtt():
    job = "subseed02"
    _write_job_vtt(job, "original.en.vtt", VTT_EN)
    segs = dub_core._segments_from_job_subtitles(job, "zh")  # hint misses
    assert len(segs) == 1
    assert segs[0]["text"] == "Hello world"


def test_subtitle_seed_returns_empty_without_vtt():
    job = "subseed03"
    os.makedirs(dub_pipeline.safe_job_dir(job), exist_ok=True)
    assert dub_core._segments_from_job_subtitles(job, "zh") == []
