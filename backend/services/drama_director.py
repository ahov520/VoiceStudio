"""Drama Director - AI role assignment + per-line emotion for audiobook dramas.

Turns a raw script into a cast table and an emotion-annotated line list, then
compiles both into a longform script the existing audiobook pipeline renders:

  * ``# chapter`` headings      - chapter boundaries
  * ``[voice:NAME]``            - per-line narrator switch (voice_map resolves
                                  NAME -> profile id at render time)
  * ``[slow]/[fast]/[emphasis]``- SSML-lite delivery markers from emotion
  * ``[pause N]``               - emotion/intensity-scaled silence

The emotion set is a stable contract (same philosophy as services/director.py's
taxonomy): the LLM is an implementation detail. Parsing runs via LLM when one
is configured (JSON output, skill id ``drama_director``, disableable in
Settings -> LLM Skills) and falls back to a deterministic heuristic otherwise.

Everything here is pure (no torch, no I/O) so it unit-tests without a server.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Callable, Optional

from services.llm_backend import get_active_llm_backend, OffBackend

logger = logging.getLogger("omnivoice.drama_director")

# LLM Skills registry id - Settings -> LLM Skills can disable the LLM parse
# or route it to a specific provider. Disabled == the heuristic parser.
_SKILL_ID = "drama_director"

#: Stable emotion contract. Every emotion maps to exactly one SSML-lite
#: marker (or none) plus an instruct token set and a base pause.
EMOTIONS: tuple[str, ...] = (
    "neutral", "calm", "happy", "sad", "angry", "fearful",
    "surprised", "whispered", "shouting", "crying", "sarcastic", "tense",
)

#: emotion -> delivery recipe. ``marker`` is the single SSML-lite tag applied
#: to the whole line (one speed/emphasis marker keeps the parser predictable);
#: ``instruct`` tokens feed the TTS instruct string (director-style);
#: ``pause_ms`` is the base silence after the line (intensity scales it).
_DELIVERY: dict[str, dict] = {
    "neutral":   {"marker": None,          "instruct": [],              "pause_ms": 180},
    "calm":      {"marker": "slow",        "instruct": ["calm"],        "pause_ms": 320},
    "happy":     {"marker": "fast",        "instruct": ["happy", "bright"], "pause_ms": 200},
    "sad":       {"marker": "slow",        "instruct": ["sad", "heavy"], "pause_ms": 460},
    "angry":     {"marker": "fast",        "instruct": ["angry", "sharp"], "pause_ms": 220},
    "fearful":   {"marker": "slow",        "instruct": ["fearful", "trembling"], "pause_ms": 380},
    "surprised": {"marker": "emphasis",    "instruct": ["surprised"],   "pause_ms": 320},
    "whispered": {"marker": "slow",        "instruct": ["whispered", "soft"], "pause_ms": 260},
    "shouting":  {"marker": "fast",        "instruct": ["shouting", "loud"], "pause_ms": 160},
    "crying":    {"marker": "slow",        "instruct": ["crying", "broken"], "pause_ms": 520},
    "sarcastic": {"marker": "emphasis",    "instruct": ["sarcastic", "dry"], "pause_ms": 260},
    "tense":     {"marker": "emphasis",    "instruct": ["tense", "clipped"], "pause_ms": 320},
}

#: Intensity multiplies the base pause and, past a threshold, doubles the
#: instruct adjectives (e.g. "sad, heavy, very heavy").
_INTENSITY_PAUSE_SCALE = 1.4
_INTENSITY_BOOST = 0.7

#: Heuristic speaker line: ``Name: dialogue`` (ASCII or CJK name, <= 24 chars).
_SPEAKER_RE = re.compile(r"^\s*([A-Za-z\u4e00-\u9fff][^:：]{0,23}?)\s*[：:]\s*(.+)$")

_MAX_CAST = 40
_MAX_LINES = 4000
_LLM_TIMEOUT_S = 60.0

#: Instruct adjectives used by the heuristic emotion sniff (director-style).
_HEURISTIC_EMOTION_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("shouting", ("shout", "yell", "scream", "喊", "吼", "大叫")),
    ("crying", ("cry", "sob", "weep", "哭", "啜泣")),
    ("whispered", ("whisper", "murmur", "低语", "耳语", "轻声")),
    ("angry", ("angry", "furious", "rage", "生气", "愤怒", "怒")),
    ("happy", ("happy", "laugh", "joy", "笑", "高兴", "开心")),
    ("sad", ("sad", "sorrow", "grief", "难过", "悲伤", "伤心")),
    ("fearful", ("fear", "terrified", "afraid", "害怕", "恐惧")),
    ("surprised", ("surprised", "shocked", "astonished", "惊讶", "震惊")),
    ("sarcastic", ("sarcastic", "sneer", "讽刺", "讥讽")),
    ("tense", ("tense", "clenched", "紧张", "紧绷")),
    ("calm", ("calm", "steady", "平静", "淡然")),
]


# ── Emotion -> delivery ────────────────────────────────────────────────────


def emotion_to_ssml(emotion: str, intensity: float) -> str:
    """Wrap ``text`` marker - placeholder is applied by the caller.

    Returns the open+close tag pair as ``(open, close)`` strings, or
    ``("", "")`` for neutral.
    """
    marker = _DELIVERY.get(emotion, _DELIVERY["neutral"])["marker"]
    if not marker:
        return "", ""
    return f"[{marker}]", f"[/{marker}]"


def emotion_to_instruct(emotion: str, intensity: float) -> str:
    """Instruct-string tokens for the TTS instruct channel (director-style)."""
    tokens = list(_DELIVERY.get(emotion, _DELIVERY["neutral"])["instruct"])
    if intensity >= _INTENSITY_BOOST and tokens:
        tokens.append("very " + tokens[-1])
    return ", ".join(tokens) if tokens else ""


def emotion_pause_ms(emotion: str, intensity: float) -> int:
    """Silence (ms) after a line: base scaled by intensity, clamped."""
    base = _DELIVERY.get(emotion, _DELIVERY["neutral"])["pause_ms"]
    scaled = base * (1.0 + (_INTENSITY_PAUSE_SCALE - 1.0) * max(0.0, min(1.0, intensity)))
    return int(round(scaled))


def _clamp_intensity(raw) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, v))


def _clamp_emotion(raw) -> str:
    e = str(raw or "").strip().lower()
    return e if e in EMOTIONS else "neutral"


# ── Script parsing ─────────────────────────────────────────────────────────


_LLM_PARSE_PROMPT = """\
You are a drama casting director. Parse the user's script into a cast and a
per-line performance sheet. Reply ONLY with JSON of this exact shape:

{"cast":[{"name":"...","aliases":["..."],"description":"gender/age/personality in a few words"}],
 "lines":[{"speaker":"...","text":"spoken line only","emotion":"...","intensity":0.0-1.0,"stage":"stage direction if any, else empty string"}]}

Rules:
- speaker must reference a cast name (use "旁白"/"Narrator" for narration).
- emotion must be one of: %s
- text is ONLY what is spoken (drop leading dashes, quotes, speaker labels).
- stage directions (动作/神态) go in "stage", never in text.
- Keep every line in order. No preamble, no trailing text.
""" % ", ".join(EMOTIONS)


def _strip_json_fences(raw: str) -> str:
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip(), flags=re.MULTILINE).strip()


def _heuristic_parse(text: str) -> dict:
    """Deterministic parser: ``Name: line`` speaker split + keyword emotion."""
    cast: dict[str, dict] = {}
    lines: list[dict] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            lines.append({"speaker": "Narrator", "text": line.lstrip("#").strip(),
                          "emotion": "neutral", "intensity": 0.3, "stage": ""})
            continue
        m = _SPEAKER_RE.match(line)
        if m:
            speaker = m.group(1).strip()
            spoken = m.group(2).strip()
        else:
            speaker = "Narrator"
            spoken = line
        cast.setdefault(speaker, {"name": speaker, "aliases": [], "description": ""})
        emotion = "neutral"
        lower = spoken.lower()
        for cand, hints in _HEURISTIC_EMOTION_HINTS:
            if any(h in lower for h in hints):
                emotion = cand
                break
        lines.append({"speaker": speaker, "text": spoken,
                      "emotion": emotion, "intensity": 0.5, "stage": ""})
    return {"cast": list(cast.values()), "lines": lines[: _MAX_LINES]}


def _validate_parse(parsed: dict, fallback: dict) -> dict:
    """Coerce an LLM response into the stable shape; drop garbage."""
    try:
        cast_raw = parsed.get("cast") or []
        lines_raw = parsed.get("lines") or []
    except AttributeError:
        return fallback
    if not isinstance(cast_raw, list) or not isinstance(lines_raw, list):
        return fallback
    cast: dict[str, dict] = {}
    for c in cast_raw[: _MAX_CAST]:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        cast[name] = {
            "name": name,
            "aliases": [str(a).strip() for a in (c.get("aliases") or []) if str(a).strip()][:8],
            "description": str(c.get("description") or "").strip()[:200],
        }
    if not cast:
        return fallback
    out_lines: list[dict] = []
    for ln in lines_raw[: _MAX_LINES]:
        if not isinstance(ln, dict):
            continue
        speaker = str(ln.get("speaker") or "Narrator").strip()
        text = str(ln.get("text") or "").strip()
        if not text:
            continue
        if speaker not in cast:
            speaker = "Narrator"
            cast.setdefault("Narrator", {"name": "Narrator", "aliases": [], "description": "旁白"})
        out_lines.append({
            "speaker": speaker,
            "text": text[:500],
            "emotion": _clamp_emotion(ln.get("emotion")),
            "intensity": _clamp_intensity(ln.get("intensity")),
            "stage": str(ln.get("stage") or "").strip()[:200],
        })
    if not out_lines:
        return fallback
    return {"cast": list(cast.values()), "lines": out_lines}


def parse_script_director(text: str, llm: Optional[Callable] = None) -> dict:
    """Public entry: script -> {cast, lines} with per-line emotion.

    ``llm`` is injectable for tests; default resolves the active LLM backend
    through the LLM Skills registry (off/disabled -> heuristic).
    """
    if not text or not text.strip():
        return {"cast": [], "lines": []}
    fallback = _heuristic_parse(text)

    if llm is None:
        from services import llm_skills
        llm = llm_skills.skill_backend(_SKILL_ID, active=lambda: get_active_llm_backend())
    if isinstance(llm, OffBackend):
        return fallback

    try:
        body = llm.chat(system=_LLM_PARSE_PROMPT, user=text, timeout=_LLM_TIMEOUT_S)
    except Exception:
        logger.warning("drama director LLM parse failed; using heuristic parser")
        return fallback

    try:
        parsed = json.loads(_strip_json_fences(body))
    except (json.JSONDecodeError, TypeError):
        logger.warning("drama director got non-JSON from LLM; using heuristic parser")
        return fallback
    return _validate_parse(parsed, fallback)


# ── Cast voice suggestion ──────────────────────────────────────────────────


def suggest_cast_voices(cast: list[dict], profiles: Optional[list[dict]] = None) -> list[dict]:
    """Rank local voice profiles per character by name/description keywords.

    Each cast row gains ``voice``: either ``{"profile_id": ...}`` when a
    profile name/description matches, or ``{"recipe_instruct": "..."}``
    (generated from the character description) plus ``candidates`` (top
    profile matches, empty when no profiles exist).
    """
    profiles = profiles or []
    out = []
    for c in cast:
        name = (c.get("name") or "").lower()
        desc = (c.get("description") or "").lower()
        haystack = f"{name} {desc}"
        scored = []
        for p in profiles:
            pname = str(p.get("name") or "").lower()
            score = 0
            for token in (name or "").split():
                if token and token in pname:
                    score += 3
            for token in re.findall(r"[a-z\u4e00-\u9fff]+", haystack):
                if len(token) >= 2 and token in pname:
                    score += 1
            if score > 0:
                scored.append((score, p))
        scored.sort(key=lambda x: -x[0])
        candidates = [{"id": p["id"], "name": p.get("name") or "", "kind": p.get("kind") or ""}
                      for _, p in scored[:5]]
        voice = {}
        if candidates:
            voice["profile_id"] = candidates[0]["id"]
        else:
            voice["recipe_instruct"] = (
                str(c.get("description") or "").strip() or f"a distinct voice for {c.get('name') or 'character'}"
            )
        out.append({**c, "voice": voice, "candidates": candidates})
    return out


# ── Audiobook script compilation ───────────────────────────────────────────


def build_audiobook_script(cast: list[dict], lines: list[dict], title: str = "Drama") -> str:
    """Compile cast + lines into a longform script with voice/SSML/pause markers.

    ``voice_map`` for the renderer is {character name -> profile id} for cast
    rows that carry ``voice.profile_id``.
    """
    voice_map: dict[str, str] = {}
    for c in cast:
        pid = (c.get("voice") or {}).get("profile_id")
        if pid:
            voice_map[c["name"]] = pid

    out = [f"# {title}"]
    current_speaker = None
    for ln in lines:
        speaker = ln.get("speaker") or "Narrator"
        text = (ln.get("text") or "").strip()
        if not text:
            continue
        if speaker != current_speaker:
            out.append(f"[voice:{speaker}]")
            current_speaker = speaker
        emotion = _clamp_emotion(ln.get("emotion"))
        intensity = _clamp_intensity(ln.get("intensity"))
        open_tag, close_tag = emotion_to_ssml(emotion, intensity)
        pause = emotion_pause_ms(emotion, intensity)
        rendered = f"{open_tag}{text}{close_tag}"
        if pause > 0:
            rendered += f" [pause {pause}]"
        out.append(rendered)
    return "\n".join(out)


def voice_map_for(cast: list[dict]) -> dict[str, str]:
    """{character name -> profile id} for rows with an assigned profile."""
    return {
        c["name"]: c["voice"]["profile_id"]
        for c in cast
        if (c.get("voice") or {}).get("profile_id")
    }
