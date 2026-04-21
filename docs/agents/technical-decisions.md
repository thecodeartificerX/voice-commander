# Locked Technical Decisions

One-line summary of each non-negotiable choice, with a link to the full ADR. Lazy-referenced from `CLAUDE.md`.

When you touch any row, also update the corresponding ADR (never the other way around — ADRs are the source of truth).

| Decision | Choice | Why | ADR |
|---|---|---|---|
| Package manager | `uv` | Fast, reproducible, pyproject.toml-native | [0011](../decisions/0011-uv-package-manager.md) |
| Hotkey | Scroll Lock, single-tap toggle | Non-printable; will not collide with typing | [0001](../decisions/0001-scroll-lock-hotkey.md) |
| Hotkey listener | `pynput` (primary), `keyboard` (fallback) | Supports Scroll Lock; cross-platform path | [0002](../decisions/0002-pynput-over-keyboard.md) |
| Audio capture | `sounddevice`, device-native rate, mono float32 | `InputStream` opens at device rate (e.g. 48 kHz WASAPI); raw frames pushed to `raw_q` | [0003](../decisions/0003-sounddevice-over-pyaudio.md) |
| Resampler | `soxr.ResampleStream`, device-native → 16 kHz, HQ | True streaming resampler with internal filter state; one instance per session | [0017](../decisions/0017-soxr-streaming-resampler.md) |
| VAD engine | `silero-vad` ONNX, 512-sample 16 kHz frames | Neural VAD with float confidence; robust to keyboard/fan noise | [0016](../decisions/0016-silero-vad-over-webrtcvad.md) |
| VAD gate | `VADGate` — pre-roll buffer + utterance accumulation + max-utterance guard | Pre-roll captures onset; max-utterance bounds buffer growth | [0015](../decisions/0015-vad-streaming-mode.md) |
| Session model | Scroll Lock toggles session; optional mute key suspends/resumes stream within a session (two flags: `session_active`, `muted`) | VAD auto-segments between keypresses; mute coexists with external dictation apps | [0015](../decisions/0015-vad-streaming-mode.md), [0025](../decisions/0025-mute-hotkey-for-external-dictation.md) |
| Transcription | `faster-whisper` `small.en` on CUDA; ndarray hand-off | Sub-second latency on NVIDIA GPU; skips temp-file I/O on the hot path | [0004](../decisions/0004-faster-whisper-cuda.md), [0018](../decisions/0018-ndarray-handoff-to-whisper.md) |
| Fuzzy match | `rapidfuzz`, `WRatio`, threshold ≈85 | Fast, no ML deps, good-enough without a language model | [0005](../decisions/0005-rapidfuzz-matching.md) |
| Tool registry | `@tool` decorator + auto-discovery; sidecar `.toml` per group | Phrases live next to code; metadata hot-reloadable without restart | [0006](../decisions/0006-tool-decorator-registry.md), [0021](../decisions/0021-sidecar-toml-per-tool.md), [0024](../decisions/0024-bare-tool-decorator-migration.md) |
| Feedback | `winsound` miss chimes only (no visual toasts, no success chime) | Fire-and-forget, non-interruptive. WinRT toasts dropped for UX + CUDA-init fragility. | [0007](../decisions/0007-windows-toasts-over-tkinter.md), [0008](../decisions/0008-winsound-for-chimes.md), [0013](../decisions/0013-drop-winrt-toasts-audio-only-feedback.md), [0014](../decisions/0014-miss-only-chimes.md) |
| Debug artifact | `outputs/last_utterance.wav` — async overwrite per utterance | Fire-and-forget; post-mortem inspection only | [0018](../decisions/0018-ndarray-handoff-to-whisper.md), [0019](../decisions/0019-supersede-single-shot-recorder.md) |
| Config | `config.toml` + optional `config.local.toml` deep-merged overlay | Tracked defaults; per-machine overrides stay out of git | *(spec §5)* |
| CUDA DLL loading | `nvidia-cublas-cu12` + `nvidia-cudnn-cu12` pip wheels + `_cuda_setup.register()` preloads DLLs via `ctypes.WinDLL` | Sidesteps Windows DLL-search / stale-PATH quirks so every entry point loads the same DLLs. **System CUDA Toolkit 12.x + cuDNN 9.x MSI install on `PATH` is still required** (wheels supplement, do not replace). | [0012 (amended)](../decisions/0012-cuda-dll-bundling.md) |
| Threading model | Four long-lived threads (PortAudio callback → VAD worker → pipeline worker, plus hotkey listener) connected by `queue.Queue` | PortAudio callback has a real-time deadline; cross-thread work dispatched via queues only | [0010](../decisions/0010-threading-model.md) |
| Phased delivery | Each phase ends in an automated test gate **and** a human validation gate | No feature is "done" until both pass | [0009](../decisions/0009-phased-delivery-with-hitl-gates.md) |
| Web UI | Embedded FastAPI + HTMX on a uvicorn daemon thread | Zero build step; partial-page updates; hot-reloads registry metadata | [0020](../decisions/0020-web-ui-embedded-fastapi.md), [0022](../decisions/0022-htmx-over-spa.md), [0023](../decisions/0023-metadata-only-hot-reload.md) |

All ADRs live in [`../decisions/`](../decisions/). Add a new row here whenever you add a new ADR.
