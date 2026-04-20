# ADR 0016: silero-vad over webrtcvad for Voice Activity Detection

**Status:** Accepted
**Date:** 2026-04-20

## Context

VAD streaming mode (ADR 0015) requires a voice activity detection engine that can process 16 kHz mono PCM in real time, frame-by-frame, and reliably distinguish speech from silence, keyboard noise, and ambient room noise. Three candidates were evaluated.

**webrtcvad / py-webrtcvad**
Google's WebRTC VAD, exposed via the `py-webrtcvad` Python binding. GMM-based, runs on 10/20/30 ms frames at 8/16/32/48 kHz. Very fast (<0.1 ms/frame) and well-understood. However: binary-only decisions (speech/not-speech) with no confidence score, brittle on keyboard and fan noise, and `py-webrtcvad` has been effectively unmaintained since 2020 with no Windows wheels for Python 3.12+.

**silero-vad**
ONNX neural VAD from snakers4, MIT licensed. Runs on 30 ms or 60 ms frames at 8 kHz or 16 kHz. Returns a float confidence score per frame (0.0–1.0) against which a configurable threshold is applied. ~2 MB ONNX model, ~0.5–1 ms/frame on CPU via `onnxruntime`. Community-standard for voice command and transcription pipelines (used in Whisper.cpp, faster-whisper streaming examples, Home Assistant Assist). Pre-built Windows wheels available for all Python 3.9–3.12 targets.

**pyannote.audio**
Full speaker-diarization pipeline with an embedded VAD. Transformer-based, MIT licensed, but requires HuggingFace model download, `torch` ≥ 2.0, and a gating token — overkill for single-speaker utterance segmentation. Rejected without detailed benchmarking.

## Decision

Use **silero-vad** via the `silero-vad` PyPI package (ONNX runtime path, not the torch path).

Configuration surface in `config.toml`:
- `vad_threshold` (float, default 0.5) — speech/silence decision boundary on silero confidence score
- `vad_min_speech_ms` (int, default 250) — minimum speech duration to emit an utterance
- `vad_min_silence_ms` (int, default 400) — silence after speech required before utterance is closed

The model is isolated to the VAD worker thread. `onnxruntime` ONNX sessions are not thread-safe; no sharing across threads.

## Consequences

### Positive
- Neural VAD is substantially more robust to noise than GMM-based webrtcvad; real-world environments with keyboard typing and fan noise do not produce spurious utterances at default threshold.
- Float confidence score enables tunable sensitivity via `config.toml` without code changes.
- MIT licensed, pre-built wheels, actively maintained.
- ~2 MB ONNX model — minimal distribution footprint.

### Negative
- Adds `silero-vad` and `onnxruntime` to the dependency set. `onnxruntime` is ~6 MB installed; not negligible but acceptable.
- ONNX session is not thread-safe and must remain confined to the VAD worker; any future refactor that moves VAD out of a single thread requires re-architecture.
- silero-vad ONNX model must be downloaded on first run (cached in `~/.cache/silero_vad/`); no-network first-run fails.

### Neutral
- `onnxruntime` is CPU-only here; the GPU is already occupied by faster-whisper's CUDA context. CPU VAD inference at <1 ms/frame does not compete for GPU resources.

## Alternatives considered

### webrtcvad
Rejected: no maintained Windows wheel for Python 3.12, binary speech/silence output (no threshold tuning), noise-sensitive.

### pyannote.audio
Rejected: HuggingFace token required, transformer-scale model, over-engineered for utterance segmentation of a single known speaker.

## References
- silero-vad repository: https://github.com/snakers4/silero-vad
- silero-vad PyPI: https://pypi.org/project/silero-vad/
- onnxruntime Python: https://onnxruntime.ai/docs/get-started/with-python.html
- ADR 0015 (VAD streaming mode): `0015-vad-streaming-mode.md`
