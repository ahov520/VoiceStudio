# Cloud engines — TTS, ASR, and vocal separation

VoiceStudio stays local-first: every cloud engine on this page is an explicit
opt-in that does nothing until you configure it. When one is active, the text
or audio for that job is sent to the provider you chose — the engine list and
this page say so plainly.

One key per provider, entered once in **Settings → Cloud providers**, unlocks
every engine of that provider. Keys are stored encrypted on your machine
(never echoed back to the UI); environment variables always win over saved
keys, which is the CI/power-user path.

| Provider | Env var | Unlocks |
|---|---|---|
| ElevenLabs | `ELEVENLABS_API_KEY` | `elevenlabs-tts`, `elevenlabs-asr`, `elevenlabs-isolation` |
| Alibaba Cloud Model Studio (DashScope) | `DASHSCOPE_API_KEY` | `dashscope-tts`, `dashscope-asr` |
| MVSEP | `MVSEP_API_TOKEN` | `mvsep` vocal separation |

## TTS

Configure each engine in **Model Catalogue → Engines** (TTS tab) — the panels
sit below the engine matrix — then activate it with the engine row's **Use**
button. All three re-read their settings on every generation (no restart),
run no local model (`gpu_compat: cpu`, zero VRAM), and their output still goes
through the normal mastering + AudioSeal watermark chain.

- **`openai-compat-tts`** — any OpenAI-compatible `POST /v1/audio/speech`
  server: SiliconFlow (`https://api.siliconflow.cn/v1`, CosyVoice2 and
  friends), OpenAI's own API, or a self-hosted box. Server URL / model /
  voice / optional key, plus a Test connection button that probes `/models`
  without synthesizing anything.
- **`elevenlabs-tts`** — speaks with any voice in your ElevenLabs voice
  library (including voices you cloned on elevenlabs.io); the panel loads the
  list so you can pick by name. Audio is requested as raw PCM, so nothing is
  transcoded on the way in. Env overrides: `TTS_ELEVENLABS_VOICE_ID`,
  `TTS_ELEVENLABS_MODEL_ID`.
- **`dashscope-tts`** — CosyVoice / Qwen-TTS on Alibaba Cloud Model Studio
  (百炼), reachable from mainland China without a proxy. Requires the
  `dashscope` package (`uv pip install dashscope`). Model and voice versions
  must match: `cosyvoice-v2` → `longxiaochun_v2`-style voices,
  `cosyvoice-v3-*` → v3 voices. Env overrides: `TTS_DASHSCOPE_MODEL`,
  `TTS_DASHSCOPE_VOICE`.

None of the cloud TTS engines does reference-audio cloning
(`supports_cloning: false`), so dub/batch jobs that need per-segment cloning
gate them out up front with an actionable message — same contract as
KittenTTS.

## ASR

Both cloud transcribers adapt provider output into the same Whisper-style
shape every local engine produces, and neither ever wins auto-detect — you
pick them deliberately in **Model Catalogue → Engines** (ASR tab).

- **`elevenlabs-asr`** — ElevenLabs Scribe: word-level timestamps, 90+
  languages, files up to 10 h / 3 GB in one call. Words are grouped into
  sentence-ish segments (punctuation / 0.8 s silences / 30 s cap) so dub
  segmentation sees the same shape WhisperX gives it. Model override:
  `ASR_ELEVENLABS_MODEL_ID` (default `scribe_v2`).
- **`dashscope-asr`** — Alibaba Cloud sync recognition, default
  `qwen-audio-3.0-asr-flash` (sentence timestamps always on; override with
  `ASR_DASHSCOPE_MODEL`). The sync API caps one call at 5 minutes / 10 MB, so
  the engine transparently re-encodes to 16 kHz mono WAV, splits into ≤240 s
  chunks, and shifts each chunk's timestamps so downstream segmentation sees
  one continuous timeline. Requires the `dashscope` package.

The generic **`openai-compat-asr`** engine (see
[openai-compatible-asr.md](openai-compatible-asr.md)) remains the right
choice for OpenAI's Whisper API, SiliconFlow SenseVoice, or any self-hosted
server that speaks `POST /v1/audio/transcriptions`.

## Vocal separation

**Settings → Vocal separation** picks the engine used by dub prep and mic
cleanup (`OMNIVOICE_SEPARATION_BACKEND` pins it over the UI choice). The
pipeline contract is unchanged whichever engine runs: stems land as
`vocals.wav` (+ `no_vocals.wav` when the engine produces a background bed),
progress drives the same prep bar, and any failure falls back to the mixed
track exactly like a local Demucs crash always has. A selected cloud engine
whose key was cleared falls back to local Demucs at use time instead of
guaranteeing a failed stage.

- **`demucs-local`** (default) — Meta Demucs `htdemucs` on this machine,
  vocals + background. Unchanged behaviour, now just one engine among three.
- **`mvsep`** — mvsep.com's separation API: upload → queue → download. Returns
  BOTH vocals and instrumental, so dub exports keep their background bed.
  The algorithm is selectable (`sep_type`, default 40 = BS Roformer — the
  highest vocal SDR on MVSEP's own table; also `MVSEP_SEP_TYPE`).
  `MVSEP_BASE_URL` can pin a regional endpoint (`https://hk.mvsep.com` is
  closest to East Asia); the default host geo-steers automatically. Aborting
  a dub job also cancels the queued MVSEP job (credits are refunded when it
  had not started).
- **`elevenlabs-isolation`** — ElevenLabs Voice Isolator. Returns the voice
  track ONLY: downstream degrades cleanly (`has_bg=false` — onset snapping
  stays off, exports skip the background bed), and the Settings row says so.
  Good for mic cleanup and speech-heavy sources; prefer MVSEP for
  music-heavy dubs.

## Proxies and restricted networks

All cloud clients honor the standard proxy environment variables
(`HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY`) — relevant for ElevenLabs and
MVSEP from mainland China. DashScope needs no proxy from mainland China.
