# VoiceStudio — CosyVoice Engine

CosyVoice is one of the multilingual TTS engines VoiceStudio can drive. It does
zero-shot voice cloning across 9+ languages with separate models for "base",
"instruct", and "SFT" use cases.

## Install

CosyVoice runs in a dependency-isolated sidecar so it cannot poison the main
VoiceStudio Python environment. Configure it from the in-app **Model Catalogue
→ Engines** tab:

1. Open **Model Catalogue → Engines**.
2. Download/select the `Fun-CosyVoice3-0.5B-2512` model.
3. Select **CosyVoice** in the **Voice Cloning** engine picker. On first use,
   the app creates a private compatibility venv under its app-data directory
   and installs the upstream-compatible pins (`transformers==4.51.3`,
   `numpy==1.26.4`, `onnxruntime==1.18.0`, `wetext==0.0.4`, and
   `x-transformers==2.11.24`). `uv` must be installed for this one-time step.

If you already cloned CosyVoice and prepared a compatible `.venv`, set
`OMNIVOICE_COSYVOICE_DIR` to the clone root (or directly to that venv) before
starting VoiceStudio; the user environment is preferred over the managed one.

CosyVoice 3 requires a reference recording for local synthesis. The adapter
adds the model's required prompt boundary internally, so enter the reference
transcript as normal text; do not add `<|endofprompt|>` yourself.

The dedicated venv keeps CosyVoice's transformer pins from clashing with
IndexTTS / ChatterboxTTS / SonicTranslate (see
[troubleshooting.md](../install/troubleshooting.md#10-indextts--cosyvoice--chatterboxtts-clash)).

## Common errors

### `Model not found: CosyVoice-300M-Instruct`

The first synthesis call downloads the weights from HuggingFace. If the
download was interrupted, the manifest can be inconsistent. **Fix:** delete
`~/.cache/huggingface/hub/models--FunAudioLLM--CosyVoice-300M*` and retry —
the engine re-downloads cleanly.

### `HfHubHTTPError: 401 Client Error`

CosyVoice models are not gated as of `v1.x`, but the underlying download
goes through `huggingface_hub` which still wants a token for rate-limit
buckets. Set one — see
[docs/setup/huggingface-token.md](../setup/huggingface-token.md).

### `RuntimeError: CUDA out of memory` on first synthesise

The CosyVoice-300M-Instruct path peaks at ~4.5 GB VRAM. If you're on an 8 GB
GPU and also have a browser open, that's tight. **Fix:** close other CUDA
apps, or pick a smaller variant (CosyVoice-300M without instruct).

## Troubleshooting

- **Issue [#55](https://github.com/debpalash/VoiceStudio/issues/55):**
  CosyVoice install clashing with IndexTTS — fixed in v0.3+ via per-engine
  venvs.
- For other errors, capture the splash-screen log (Settings → Logs → Backend)
  and open a bug report with **Settings → Help → Report a bug** (Phase 5
  ships the auto-report path).
