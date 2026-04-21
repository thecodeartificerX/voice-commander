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

## LLM Router

| Decision | Choice | Why | ADR |
|---|---|---|---|
| Routing strategy | rapidfuzz first (≥95 confidence); escalate to LLM on miss | Preserves ~1 ms hot path for simple commands; unlocks natural-language routing for the long tail | [0026](../decisions/0026-hybrid-routing-rapidfuzz-then-llm.md) |
| rapidfuzz threshold when LLM enabled | 95 (up from 85) | Tighter gate pushes ambiguous matches to LLM instead of wrong hot-path dispatch | [0027](../decisions/0027-rapidfuzz-threshold-tightening.md) |
| LLM backend | LM Studio OpenAI-compatible endpoint (`http://localhost:1234/v1`) | Already the standard local-model GUI on Windows; identical OpenAI tool-call request/response shape | [0028](../decisions/0028-lm-studio-openai-endpoint.md) |
| First router model | Gemma 4 E4B Q4_K_M (`google/gemma-4-e4b`) | ~4 GB VRAM, ~510 ms warm on RTX 3060+, native tool-calling, one-click LM Studio install | [0029](../decisions/0029-gemma-4-e4b-as-first-model.md) |
| LLM request history | Stateless — fresh two-message request per utterance | Voice commands are self-contained imperatives; history inflates prompt tokens and pollutes context | [0030](../decisions/0030-stateless-per-call-requests.md) |
| Unroutable utterances | `tool_choice="required"` + `no_match(reason)` escape tool | Forces a structured response always; `no_match` gives the model a valid tool to call when uncertain | [0031](../decisions/0031-tool-choice-required-plus-no-match.md) |
| Execution model | One-shot plan — all tool calls returned in a single LLM response, executed sequentially | Voice commands are deterministic sequences; multi-turn agentic loops cost 1.5 s–6 s per chain | [0032](../decisions/0032-one-shot-plan-not-agentic-loop.md) |
| Inter-step timing | Per-tool `settle_ms` TOML key + LLM-callable `wait(ms)` primitive | Tool-inherent delay encoded once at authoring time; LLM can add extra pacing for unusual chains | [0033](../decisions/0033-per-tool-settle-ms-plus-wait-primitive.md) |
| Tool argument source of truth | Python type hints + sidecar TOML `[tool.args.<name>]` sub-tables | Single authoring point; `inspect.signature` derives JSON schema; no extra deps | [0034](../decisions/0034-sig-plus-toml-as-single-source-of-truth.md) |
| Commander skill for argument-bearing tools | Extended interview: args, `settle_ms`, `llm_only`; post-write validator run | Prevents sig↔TOML drift at creation time rather than at daemon start | [0035](../decisions/0035-commander-skill-contract-extension.md) |
| Startup validation | `build_streaming_daemon()` validates sig↔TOML consistency; `--validate` CLI flag for CI | Fail-loud at load time; wrong arg names or missing descriptions crash before any utterance runs | [0036](../decisions/0036-startup-validator-tool-drift.md) |
| LLM warmup | `warmup()` POSTs a real `/v1/chat/completions` with system prompt + tools array, `tool_choice="none"`, `max_tokens=1`; dedicated `warmup_timeout_ms = 5000` config field | `GET /v1/models` does not seed the KV cache; cold first call timed out in production; warmup POST pre-populates the prefix cache so the first real call is warm | [0038](../decisions/0038-llm-router-warmup-real-chat-completion.md) |

## Window / Focus Primitives

| Decision | Choice | Why | ADR |
|---|---|---|---|
| `SetForegroundWindow` hardening | `AttachThreadInput(foreground_tid, target_tid, True)` + `AllowSetForegroundWindow(ASFW_ANY)` + `GetForegroundWindow()` post-call verification; raises `FocusWindowError` on verified failure | Windows 11 foreground-lockout causes silent failure; chains continued with keystrokes landing in wrong window; verified raise halts plan cleanly | [0037](../decisions/0037-focus-window-attachthreadinput-workaround.md) |
| Focus tool `settle_ms` | `settle_ms = 200` on `focus_browser`, `focus_terminal`, `focus_window` | Extra 50 ms over previous 150 ms value accounts for Windows 11 paint latency after `AttachThreadInput`-based focus; only applied on success, never after `FocusWindowError` | [0037](../decisions/0037-focus-window-attachthreadinput-workaround.md), [0033](../decisions/0033-per-tool-settle-ms-plus-wait-primitive.md) |

## Daemon Lifecycle

| Decision | Choice | Why | ADR |
|---|---|---|---|
| Single-instance lock | OS-level file locking (`msvcrt`/`fcntl`) + fixed ctypes HANDLE truncation + PID:GUID + cleanup handlers | Advisory file lock auto-released on crash; ctypes `c_void_p` for 64-bit HANDLE correctness | [0039](../decisions/0039-single-instance-lock-hardening.md) |

All ADRs live in [`../decisions/`](../decisions/). Add a new row here whenever you add a new ADR.
