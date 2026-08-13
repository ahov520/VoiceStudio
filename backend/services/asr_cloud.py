"""Cloud ASR engines — ElevenLabs Scribe and DashScope (Alibaba Cloud).

Two pure network clients behind the standard :class:`ASRBackend` surface,
complementing the generic ``openai-compat-asr`` engine (#877) with the two
major providers that do NOT speak the OpenAI transcription protocol:

* ``elevenlabs-asr`` — ElevenLabs Scribe (``POST /v1/speech-to-text``): word
  timestamps, 90+ languages, files up to 10 h / 3 GB in one call.
* ``dashscope-asr`` — Alibaba Cloud Model Studio (Bailian) sync recognition
  (Qwen-Audio-3.0-ASR-Flash / Fun-ASR-Flash / Qwen3-ASR-Flash). Reachable
  from mainland China without a proxy. The sync endpoint caps one call at
  5 minutes / 10 MB, so this adapter transparently splits longer audio into
  16 kHz mono WAV chunks with ffmpeg and merges the results with per-chunk
  time offsets.

Both adapt provider responses into this app's Whisper-style
``{chunks, segments, language}`` shape (see ``asr_backend.ASRBackend``).
Credentials resolve via ``services.cloud_providers`` (env → encrypted
store); keys are never logged and never included in raised errors. Neither
engine participates in ASR auto-detect — cloud transcription is explicit
opt-in via the engine matrix (local-first contract).
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from typing import Optional

from services.asr_backend import ASRBackend

logger = logging.getLogger("omnivoice.asr_cloud")


def _env_or_text(env: str, row: str, default: str = "") -> str:
    from services import settings_store
    return os.environ.get(env) or settings_store.get_text(row) or default


# ── ElevenLabs Scribe ───────────────────────────────────────────────────────

_ASR_ELEVENLABS_MODEL_KEY = "asr.elevenlabs.model_id"
_ELEVENLABS_DEFAULT_MODEL = "scribe_v2"

#: Scribe reports ISO 639-2/3 codes; the rest of the app speaks 639-1.
_ISO3_TO_1 = {
    "eng": "en", "zho": "zh", "cmn": "zh", "spa": "es", "fra": "fr",
    "deu": "de", "ita": "it", "por": "pt", "rus": "ru", "jpn": "ja",
    "kor": "ko", "hin": "hi", "ara": "ar", "nld": "nl", "pol": "pl",
    "tur": "tr", "ukr": "uk", "vie": "vi", "tha": "th", "ind": "id",
    "swe": "sv", "ces": "cs", "ell": "el", "fin": "fi", "dan": "da",
    "nor": "no", "hun": "hu", "ron": "ro", "heb": "he", "msa": "ms",
}

#: Sentence-ending punctuation that closes a segment (Latin + CJK ideographic
#: full stop / fullwidth !? / ellipsis, as unicode escapes — repo convention
#: keeps raw CJK out of source files).
_SENTENCE_TERMINATORS = (".", "!", "?", "\u3002", "\uff01", "\uff1f", "\u2026")

#: A silence this long between words starts a new segment.
_SEGMENT_GAP_S = 0.8
#: Hard cap so run-on speech without punctuation still yields dub-able spans.
_SEGMENT_MAX_S = 30.0


def resolve_elevenlabs_asr_model_id() -> str:
    return _env_or_text(
        "ASR_ELEVENLABS_MODEL_ID", _ASR_ELEVENLABS_MODEL_KEY, _ELEVENLABS_DEFAULT_MODEL
    )


def _normalize_language(code: Optional[str]) -> str:
    if not code:
        return "en"
    code = str(code).strip().lower()
    if len(code) == 2:
        return code
    return _ISO3_TO_1.get(code, code[:2] if len(code) > 2 else "en")


def _scribe_words_to_segments(words: list) -> list[dict]:
    """Group Scribe's flat word stream into sentence-ish segments.

    Splits after sentence-ending punctuation, on silences longer than
    :data:`_SEGMENT_GAP_S`, or when a segment exceeds :data:`_SEGMENT_MAX_S`.
    ``audio_event`` entries (laughter etc.) are dropped; ``spacing`` entries
    contribute their text but carry no timing.
    """
    segments: list[dict] = []
    parts: list[str] = []
    seg_words: list[dict] = []
    seg_start: Optional[float] = None
    seg_end: Optional[float] = None

    def flush():
        nonlocal parts, seg_words, seg_start, seg_end
        text = "".join(parts).strip()
        if text:
            segments.append({
                "text": text,
                "start": seg_start if seg_start is not None else 0.0,
                "end": seg_end,
                "words": seg_words,
            })
        parts, seg_words, seg_start, seg_end = [], [], None, None

    for w in words:
        if not isinstance(w, dict):
            continue
        wtype = w.get("type") or "word"
        if wtype == "audio_event":
            continue
        text = w.get("text") or ""
        if wtype == "spacing":
            if parts:
                parts.append(text)
            continue
        start, end = w.get("start"), w.get("end")
        # A long silence starts a new segment BEFORE this word joins one.
        if (
            seg_words
            and start is not None
            and seg_end is not None
            and (start - seg_end) > _SEGMENT_GAP_S
        ):
            flush()
        parts.append(text)
        if seg_start is None and start is not None:
            seg_start = start
        if end is not None:
            seg_end = end
        seg_words.append({"word": text, "start": start, "end": end})
        too_long = (
            seg_start is not None and seg_end is not None
            and (seg_end - seg_start) >= _SEGMENT_MAX_S
        )
        if text.rstrip().endswith(_SENTENCE_TERMINATORS) or too_long:
            flush()
    flush()
    return segments


class ElevenLabsASRBackend(ASRBackend):
    """Remote transcription via ElevenLabs Scribe (word-level timestamps)."""

    id = "elevenlabs-asr"
    display_name = "ElevenLabs Scribe (cloud)"
    gpu_compat = ("cpu",)  # network client only — no local compute

    def __init__(self):
        self._model_id = resolve_elevenlabs_asr_model_id()

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        from services import cloud_providers
        if not cloud_providers.has_key("elevenlabs"):
            return False, (
                "Configure an ElevenLabs API key in Settings → Cloud providers "
                "(or set ELEVENLABS_API_KEY)"
            )
        return True, "ready"

    def transcribe(self, audio_path: str, *, word_timestamps: bool = True) -> dict:
        from services import cloud_providers

        key = cloud_providers.resolve_api_key("elevenlabs")
        if not key:
            raise RuntimeError(
                "No ElevenLabs API key configured — add one in Settings → Cloud providers"
            )
        logger.info(
            "ElevenLabs Scribe transcribing %s (model=%s)", audio_path, self._model_id
        )

        import httpx

        try:
            with open(audio_path, "rb") as fh, httpx.Client(
                timeout=httpx.Timeout(600.0, connect=15.0), follow_redirects=True
            ) as client:
                resp = client.post(
                    f"{cloud_providers.elevenlabs_base_url()}/v1/speech-to-text",
                    headers={"xi-api-key": key},
                    files={"file": (os.path.basename(audio_path), fh, "application/octet-stream")},
                    data={
                        "model_id": self._model_id,
                        "timestamps_granularity": "word",
                        "diarize": "false",
                        "tag_audio_events": "false",
                    },
                )
        except Exception as exc:
            raise RuntimeError(
                f"ElevenLabs Scribe request failed: {type(exc).__name__}: {exc}"
            ) from exc
        if resp.status_code in (401, 403):
            raise RuntimeError(
                f"ElevenLabs rejected the API key (HTTP {resp.status_code}) — "
                "check Settings → Cloud providers"
            )
        if not (200 <= resp.status_code < 300):
            from core.scrub import scrub_text
            raise RuntimeError(
                f"ElevenLabs Scribe failed with HTTP {resp.status_code}: "
                f"{scrub_text((resp.text or '')[:300])}"
            )
        try:
            body = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("ElevenLabs Scribe returned a non-JSON response") from exc
        return self._adapt_response(body)

    @staticmethod
    def _adapt_response(body: dict) -> dict:
        words = body.get("words") or []
        segments = _scribe_words_to_segments(words)
        if not segments:
            text = (body.get("text") or "").strip()
            if text:
                segments = [{"text": text, "start": 0.0, "end": None, "words": []}]
        chunks = [
            {"text": seg["text"], "timestamp": (seg["start"], seg["end"])}
            for seg in segments
        ]
        return {
            "chunks": chunks,
            "segments": segments,
            "language": _normalize_language(body.get("language_code")),
        }


# ── DashScope (Alibaba Cloud Model Studio) sync recognition ─────────────────

_ASR_DASHSCOPE_MODEL_KEY = "asr.dashscope.model"
#: Qwen-Audio-3.0-ASR-Flash: sync, local-file upload via the SDK, sentence
#: timestamps always on. (qwen3-asr-flash also works but returns no
#: timestamps on the sync endpoint; fun-asr-flash-* snapshots work too.)
_DASHSCOPE_DEFAULT_ASR_MODEL = "qwen-audio-3.0-asr-flash"

#: The sync endpoint caps a call at 5 min / 10 MB. 240 s of 16 kHz mono
#: 16-bit WAV is ~7.4 MB — safely inside both limits.
_DASHSCOPE_CHUNK_S = 240


def resolve_dashscope_asr_model() -> str:
    return _env_or_text(
        "ASR_DASHSCOPE_MODEL", _ASR_DASHSCOPE_MODEL_KEY, _DASHSCOPE_DEFAULT_ASR_MODEL
    )


def _as_plain_dict(obj) -> dict:
    """DashScope responses are dict-like SDK objects; normalize defensively."""
    if isinstance(obj, dict):
        return obj
    for attr in ("to_dict",):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                out = fn()
                if isinstance(out, dict):
                    return out
            except Exception:  # noqa: BLE001
                pass
    try:
        return dict(obj)
    except Exception:  # noqa: BLE001
        return {}


def _wav_pcm16_mono_duration_s(path: str, sample_rate: int = 16000) -> float:
    """Duration of one of our own ffmpeg-produced chunks (pcm_s16le mono).
    Header ~44 bytes; exactness doesn't matter beyond chunk-offset math."""
    try:
        payload = max(0, os.path.getsize(path) - 44)
        return payload / float(2 * sample_rate)
    except OSError:
        return 0.0


class DashScopeASRBackend(ASRBackend):
    """Remote transcription via DashScope's synchronous recognition models.

    Handles the endpoint's 5-minute/10 MB per-call cap by re-encoding the
    input to 16 kHz mono WAV and splitting it into ≤240 s chunks (ffmpeg
    segment muxer), then shifting each chunk's sentence timestamps by the
    chunk's start offset so downstream segmentation sees one continuous
    timeline.
    """

    id = "dashscope-asr"
    display_name = "Alibaba Cloud ASR (DashScope, cloud)"
    gpu_compat = ("cpu",)

    def __init__(self):
        self._model = resolve_dashscope_asr_model()

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        import importlib.util

        from services import cloud_providers
        if not cloud_providers.has_key("dashscope"):
            return False, (
                "Configure a DashScope API key in Settings → Cloud providers "
                "(or set DASHSCOPE_API_KEY)"
            )
        if importlib.util.find_spec("dashscope") is None:
            return False, "dashscope package not installed. Install with: uv pip install dashscope"
        return True, "ready"

    # ── chunk prep ──────────────────────────────────────────────────────────

    @staticmethod
    def _split_to_chunks(audio_path: str, out_dir: str) -> list[str]:
        """Re-encode to 16 kHz mono WAV and split into ≤240 s files. A short
        input simply produces one chunk — same code path either way."""
        from services.ffmpeg_utils import find_ffmpeg

        ffmpeg = find_ffmpeg()
        pattern = os.path.join(out_dir, "chunk%04d.wav")
        cmd = [
            ffmpeg, "-y", "-i", audio_path,
            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
            "-f", "segment", "-segment_time", str(_DASHSCOPE_CHUNK_S),
            pattern,
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, timeout=600, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"ffmpeg chunking for DashScope ASR failed: {exc}") from exc
        if proc.returncode != 0:
            stderr = (proc.stderr or b"").decode(errors="replace")[:300]
            raise RuntimeError(f"ffmpeg chunking for DashScope ASR failed: {stderr}")
        chunks = sorted(
            os.path.join(out_dir, f)
            for f in os.listdir(out_dir)
            if f.startswith("chunk") and f.endswith(".wav")
        )
        if not chunks:
            raise RuntimeError("ffmpeg chunking for DashScope ASR produced no output")
        return chunks

    # ── provider call + adaptation ──────────────────────────────────────────

    def _call_chunk(self, chunk_path: str) -> dict:
        from services import cloud_providers

        key = cloud_providers.resolve_api_key("dashscope")
        if not key:
            raise RuntimeError(
                "No DashScope API key configured — add one in Settings → Cloud providers"
            )
        try:
            import dashscope
        except ImportError as exc:
            raise RuntimeError(
                "dashscope package not installed. Install with: uv pip install dashscope"
            ) from exc
        dashscope.api_key = key
        resp = dashscope.MultiModalConversation.call(
            model=self._model,
            messages=[{"role": "user", "content": [{"audio": chunk_path}]}],
        )
        status = getattr(resp, "status_code", 200)
        if status != 200:
            from core.scrub import scrub_text
            code = getattr(resp, "code", "")
            message = getattr(resp, "message", "")
            raise RuntimeError(
                f"DashScope ASR (model={self._model!r}) failed with HTTP {status} "
                f"{code}: {scrub_text(str(message)[:300])}"
            )
        return _as_plain_dict(getattr(resp, "output", None) or {})

    @staticmethod
    def _adapt_output(output: dict, offset_s: float, chunk_dur_s: float) -> tuple[list[dict], Optional[str]]:
        """One chunk's DashScope output → (segments, language|None).

        Handles every shape the sync endpoint is documented to return:
        ``output.output.sentences``/``.sentence`` (Qwen-Audio-3.0/Fun-ASR
        Flash — sentence + word timestamps in ms), ``output.choices`` (Qwen3-
        ASR-Flash — text only), and a bare ``output.text`` fallback.
        """
        def _ms(v):
            try:
                return float(v) / 1000.0
            except (TypeError, ValueError):
                return None

        sentences: list = []
        inner = output.get("output")
        if isinstance(inner, dict):
            if isinstance(inner.get("sentences"), list):
                sentences = inner["sentences"]
            elif isinstance(inner.get("sentence"), dict):
                sentences = [inner["sentence"]]

        language: Optional[str] = None
        segments: list[dict] = []
        for s in sentences:
            if not isinstance(s, dict):
                continue
            text = (s.get("text") or "").strip()
            if not text:
                continue
            begin, end = _ms(s.get("begin_time")), _ms(s.get("end_time"))
            words = []
            for w in s.get("words") or []:
                if not isinstance(w, dict):
                    continue
                wb, we = _ms(w.get("begin_time")), _ms(w.get("end_time"))
                words.append({
                    "word": w.get("text") or "",
                    "start": None if wb is None else wb + offset_s,
                    "end": None if we is None else we + offset_s,
                })
            segments.append({
                "text": text,
                "start": offset_s if begin is None else begin + offset_s,
                "end": (offset_s + chunk_dur_s) if end is None else end + offset_s,
                "words": words,
            })

        if not segments:
            text = ""
            choices = output.get("choices")
            if isinstance(choices, list) and choices:
                message = choices[0].get("message") if isinstance(choices[0], dict) else None
                content = (message or {}).get("content")
                if isinstance(content, list):
                    text = "".join(
                        c.get("text", "") for c in content if isinstance(c, dict)
                    ).strip()
                elif isinstance(content, str):
                    text = content.strip()
                for ann in (message or {}).get("annotations") or []:
                    if isinstance(ann, dict) and ann.get("language"):
                        language = str(ann["language"])
            if not text:
                text = (output.get("text") or "").strip()
            if text:
                segments = [{
                    "text": text,
                    "start": offset_s,
                    "end": offset_s + chunk_dur_s,
                    "words": [],
                }]
        return segments, language

    def transcribe(self, audio_path: str, *, word_timestamps: bool = True) -> dict:
        logger.info("DashScope ASR transcribing %s (model=%s)", audio_path, self._model)
        segments: list[dict] = []
        language: Optional[str] = None
        with tempfile.TemporaryDirectory(prefix="omnivoice_dashscope_asr_") as tmp:
            chunks = self._split_to_chunks(audio_path, tmp)
            offset = 0.0
            for chunk in chunks:
                dur = _wav_pcm16_mono_duration_s(chunk)
                output = self._call_chunk(chunk)
                segs, lang = self._adapt_output(output, offset, dur)
                segments.extend(segs)
                if language is None and lang:
                    language = _normalize_language(lang)
                offset += dur
        chunks_out = [
            {"text": seg["text"], "timestamp": (seg["start"], seg["end"])}
            for seg in segments
        ]
        return {
            "chunks": chunks_out,
            "segments": segments,
            "language": language or "en",
        }
