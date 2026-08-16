"""Drama Director service tests - role assignment + emotion -> delivery mapping.

Covers the stable contract: emotion mapping (SSML-lite / instruct / pause),
heuristic script parsing (ASCII + CJK speakers), LLM JSON parsing with
fallbacks, cast voice suggestion, and the audiobook script compilation.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from services import drama_director as dd  # noqa: E402


# ── Emotion -> delivery ────────────────────────────────────────────────────


def test_emotion_maps_to_single_ssml_marker():
    assert dd.emotion_to_ssml("angry", 0.6) == ("[fast]", "[/fast]")
    assert dd.emotion_to_ssml("sad", 0.5) == ("[slow]", "[/slow]")
    assert dd.emotion_to_ssml("surprised", 0.4) == ("[emphasis]", "[/emphasis]")
    assert dd.emotion_to_ssml("neutral", 0.5) == ("", "")
    # Unknown emotions degrade to neutral, never crash.
    assert dd.emotion_to_ssml("raging", 1.0) == ("", "")


def test_emotion_instruct_and_pause_scale_with_intensity():
    assert dd.emotion_to_instruct("sad", 0.3) == "sad, heavy"
    assert dd.emotion_to_instruct("sad", 0.9) == "sad, heavy, very heavy"
    assert dd.emotion_to_instruct("neutral", 1.0) == ""
    base = dd.emotion_pause_ms("sad", 0.0)
    loud = dd.emotion_pause_ms("sad", 1.0)
    assert loud > base  # intensity lengthens the pause


# ── Heuristic parsing ──────────────────────────────────────────────────────


def test_heuristic_parse_speakers_and_narrator():
    script = "\n".join([
        "林晚: 你走吧，我不想再见到你。",
        "她转身背对门口。",
        "老陈: 别这样。",
        "Narrator: The door closed.",
    ])
    parsed = dd._heuristic_parse(script)
    names = {c["name"] for c in parsed["cast"]}
    assert names == {"林晚", "老陈", "Narrator"}
    assert [l["speaker"] for l in parsed["lines"]] == ["林晚", "Narrator", "老陈", "Narrator"]
    assert parsed["lines"][0]["text"] == "你走吧，我不想再见到你。"


def test_heuristic_parse_sniffs_emotion_keywords():
    script = "王五: 你给我滚！(大喊)"
    parsed = dd._heuristic_parse(script)
    # 喊 -> shouting
    assert parsed["lines"][0]["emotion"] == "shouting"


# ── LLM JSON parsing + fallbacks ───────────────────────────────────────────


class _FakeLLM:
    def __init__(self, body):
        self._body = body

    def chat(self, **kw):
        return self._body


def test_llm_parse_accepts_fenced_json_and_clamps_values():
    body = """```json
    {"cast":[{"name":"林晚","aliases":["晚晚"],"description":"女主，清冷"}],
     "lines":[
       {"speaker":"林晚","text":"你走吧。","emotion":"sad","intensity":0.9,"stage":"转身"},
       {"speaker":"路人","text":"你好。","emotion":"furious","intensity":"high","stage":""}
     ]}
    ```"""
    parsed = dd.parse_script_director("ignored", llm=_FakeLLM(body))
    assert parsed["cast"][0]["name"] == "林晚"
    assert parsed["lines"][0]["emotion"] == "sad"
    assert parsed["lines"][0]["intensity"] == 0.9
    # Unknown speaker -> Narrator; unknown emotion -> neutral; bad intensity -> 0.5
    assert parsed["lines"][1]["speaker"] == "Narrator"
    assert parsed["lines"][1]["emotion"] == "neutral"
    assert parsed["lines"][1]["intensity"] == 0.5


def test_llm_non_json_falls_back_to_heuristic():
    parsed = dd.parse_script_director("林晚: 走吧。", llm=_FakeLLM("sorry, no json here"))
    assert parsed["cast"][0]["name"] == "林晚"
    assert parsed["lines"][0]["text"] == "走吧。"


def test_llm_exception_falls_back_to_heuristic():
    class _Boom:
        def chat(self, **kw):
            raise RuntimeError("provider down")

    parsed = dd.parse_script_director("老陈: 别这样。", llm=_Boom())
    assert parsed["cast"][0]["name"] == "老陈"


# ── Cast voice suggestion ──────────────────────────────────────────────────


def test_suggest_cast_voices_ranks_profiles_and_generates_recipe():
    cast = [{"name": "林晚", "aliases": [], "description": "女主，清冷年轻女声"}]
    profiles = [
        {"id": "p1", "name": "林晚", "kind": "design"},
        {"id": "p2", "name": "老陈", "kind": "clone"},
    ]
    out = dd.suggest_cast_voices(cast, profiles)
    assert out[0]["voice"]["profile_id"] == "p1"
    assert out[0]["candidates"][0]["id"] == "p1"

    # No matching profiles -> recipe instruct from the description.
    out2 = dd.suggest_cast_voices(cast, [{"id": "p9", "name": "路人甲", "kind": "clone"}])
    assert "profile_id" not in out2[0]["voice"]
    assert out2[0]["voice"]["recipe_instruct"] == "女主，清冷年轻女声"


# ── Audiobook script compilation ───────────────────────────────────────────


def test_build_audiobook_script_emits_voice_ssml_pause():
    cast = [
        {"name": "林晚", "voice": {"profile_id": "p1"}},
        {"name": "Narrator", "voice": {"recipe_instruct": "旁白"}},
    ]
    lines = [
        {"speaker": "Narrator", "text": "夜很深。", "emotion": "calm", "intensity": 0.4, "stage": ""},
        {"speaker": "林晚", "text": "你走吧！", "emotion": "angry", "intensity": 0.8, "stage": "摔门"},
    ]
    script = dd.build_audiobook_script(cast, lines, title="测试剧")
    assert script.startswith("# 测试剧")
    assert "[voice:Narrator]" in script
    assert "[voice:林晚]" in script
    assert "[fast]你走吧！[/fast]" in script
    assert "[slow]夜很深。[/slow]" in script
    assert "[pause " in script
    assert dd.voice_map_for(cast) == {"林晚": "p1"}
