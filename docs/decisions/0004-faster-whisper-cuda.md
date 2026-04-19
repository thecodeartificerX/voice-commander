# ADR 0004: faster-whisper on CUDA with small.en model

**Status:** Accepted
**Date:** 2026-04-19

## Context

Voice Commander needs to transcribe short voice commands (typically 1–5 seconds) with sub-second latency on a Windows machine equipped with an NVIDIA GPU. The original `openai-whisper` package uses PyTorch with standard FP32 inference, which is 3–5x slower than optimised alternatives on the same hardware. The `small.en` model was chosen as the sweet spot: it is fast enough for interactive use, accurate enough for a constrained phrase vocabulary, and English-only, which shaves additional model complexity and memory. Any solution must be installable via `uv` without a C/C++ build toolchain.

## Decision

We use `faster-whisper` (backed by the CTranslate2 inference engine) with `device="cuda"`, `compute_type="float16"`, and the `small.en` model. CTranslate2 applies kernel fusion, layer quantisation, and memory-layout optimisations that yield 3–5x faster inference versus the reference PyTorch implementation at identical accuracy. `float16` halves GPU memory bandwidth relative to `float32` with no perceptible accuracy loss for a restricted command vocabulary. Pre-built wheels are available on PyPI for Windows x86-64 + CUDA 12.x, so no local C++ toolchain is required.

## Consequences

### Positive
- Transcription of a 2-second utterance completes in under 200 ms on a mid-range NVIDIA GPU (RTX 30-series), well inside the interactive latency budget.
- `float16` reduces GPU VRAM usage for `small.en` to approximately 500 MB, leaving headroom for other CUDA workloads.
- `faster-whisper` exposes `vad_filter=True`, allowing silence/hallucination suppression with no extra dependency.
- Pure-Python install via `pip`/`uv`; no MSVC build step.

### Negative
- Requires CUDA 12.x runtime DLLs (`cudart64_12`, `cublas64_12`, `cublasLt64_12`) and cuDNN 9.x DLLs on `PATH`; missing DLLs produce a cryptic `OSError` at runtime (documented in `docs/gotchas.md`).
- CPU fallback (`device="cpu"`) is 5–10x slower — acceptable for CI / non-GPU machines, but unsuitable for interactive use.
- CTranslate2 is a third-party engine; upstream changes could lag behind new Whisper model releases.

### Neutral
- Model weights (~150 MB for `small.en`) are downloaded from the Hugging Face Hub on first run and cached in `~/.cache/huggingface`; a one-time network request is required.
- `compute_type="float16"` is not supported on older GPUs (pre-Turing / compute capability < 7.0); those fall back to `int8` automatically via CTranslate2.

## Alternatives considered

### openai-whisper (PyTorch backend)
The original reference implementation from OpenAI. It produces identical transcription quality but runs 3–5x slower on the same GPU due to unoptimised PyTorch kernels, absence of fused attention, and FP32-only inference. For a command loop where the user expects near-instant feedback, this latency is unacceptable. It also requires a heavier PyTorch install (~2 GB).

### whisper.cpp (ggml C++ backend)
An excellent C/C++ port of Whisper with competitive performance and very low memory usage. However, using it from Python requires either a pre-built wheel (not always available for the exact CUDA/Windows combination) or compiling with MSVC, which adds significant developer friction and CI complexity. `faster-whisper` delivers comparable speed with a pure-Python install story.

## References
- Spec: [../superpowers/specs/2026-04-19-voice-commander-design.md](../superpowers/specs/2026-04-19-voice-commander-design.md)
- faster-whisper GitHub: https://github.com/SYSTRAN/faster-whisper
- CTranslate2 docs: https://opennmt.net/CTranslate2/
- NVIDIA cuDNN installation guide: https://docs.nvidia.com/deeplearning/cudnn/install-guide/index.html
