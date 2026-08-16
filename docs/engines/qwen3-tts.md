# VoiceStudio — Qwen3-TTS Engine

Qwen3-TTS-12Hz-1.7B-Base (Apache-2.0) is Qwen's open multilingual TTS with
**zero-shot voice cloning** from a reference clip. VoiceStudio drives it
through a subprocess-isolated sidecar with **two inference paths**:

| Path | How it runs | Best for |
|---|---|---|
| **transformers** (default) | Official HF checkpoint via `transformers` in a dedicated venv | Best quality, full cloning; needs ~4-6 GB VRAM or CPU |
| **GGUF Q8** | llama.cpp's `llama-tts` binary + a Q8_0 quantised GGUF | Low VRAM / no torch; ~1.9 GB talker + codec |

Supported languages: `zh, en, de, it, pt, es, ja, ko, fr, ru`
(auto-detected from text in transformers mode; explicit `--tts-lang` in GGUF
mode).

## Install

### Transformers path (one command, then automatic)

```bash
# 1. Make sure uv is on PATH (or in your app-private install).
# 2. That's it — the first time you pick the engine, VoiceStudio bootstraps
#    engines/qwen3_tts/.venv (torch + transformers) and the model weights
#    (~3 GB) download from HuggingFace on the first synthesis.
```

Power users can pre-create their own venv and point at it:

```bash
uv venv /path/to/qwen3-tts-venv
uv pip install --python /path/to/qwen3-tts-venv/bin/python torch transformers soundfile scipy numpy
export OMNIVOICE_QWEN3_TTS_DIR=/path/to/qwen3-tts-venv
```

Optional: `OMNIVOICE_QWEN3_TTS_MODEL` overrides the HF repo id (default
`Qwen/Qwen3-TTS-12Hz-1.7B-Base`).

### GGUF Q8 path (llama.cpp)

```bash
# 1. Build llama.cpp's tts tool (has Qwen3-TTS support):
#    https://github.com/ggml-org/llama.cpp/tree/master/tools/tts
#    e.g. cmake --build build --target llama-tts
# 2. Point VoiceStudio at the binary (and optionally a local .gguf):
export OMNIVOICE_QWEN3_GGUF_BIN=/path/to/llama-tts
export OMNIVOICE_QWEN3_GGUF_MODEL=/path/to/qwen3-tts-12hz-1.7b-base-q8_0.gguf   # optional
#    (without OMNIVOICE_QWEN3_GGUF_MODEL the HF repo
#     ggml-org/Qwen3-TTS-12Hz-1.7B-Base-GGUF auto-downloads)
```

Recommended quantised repos: `cstr/qwen3-tts-1.7b-base-GGUF` (Q8_0 talker,
1.9 GB) or `ggml-org/Qwen3-TTS-12Hz-1.7B-Base-GGUF`. The mode is selected
automatically when a GGUF binary/model is configured; force it with
`OMNIVOICE_QWEN3_TTS_MODE=transformers|gguf`.

## Usage

1. Open **Model Catalogue → Engines** and find **Qwen3-TTS 1.7B**.
2. Install (transformers) or configure the GGUF binary — the row flips
   available once the venv/binary exists.
3. Use it in **Voice Cloning** (it needs a reference clip for timbre —
   zero-shot clone, no training) or any generation picker.

## Common errors

### `llama-tts binary not found`

GGUF mode is configured but the binary isn't reachable. Build llama.cpp's
`llama-tts` or set `OMNIVOICE_QWEN3_GGUF_BIN`. Switch back with
`OMNIVOICE_QWEN3_TTS_MODE=transformers` (and let the venv bootstrap).

### `Qwen3-TTS venv not found`

The transformers venv hasn't been created. Install `uv`, then trigger any
synthesis — the bootstrap creates `engines/qwen3_tts/.venv` and installs
torch + transformers (several minutes on a cold cache). Or pre-create it and
set `OMNIVOICE_QWEN3_TTS_DIR`.

### Slow first synthesis

The first generate downloads the model weights (~3 GB) from HuggingFace.
Subsequent runs load from the HF cache. In GGUF mode the first run also
downloads the GGUF repo.

### License

Apache-2.0 (weights + code) — free for commercial use, no gating.
