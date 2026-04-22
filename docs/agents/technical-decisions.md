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
| Routing strategy | LLM-only via local LM Studio; all transcripts routed through `LLMRouter` unconditionally | Simpler single path; LLM handles all utterances including simple commands; rapidfuzz hybrid removed | [0040](../decisions/0040-llm-only-routing-replaces-hybrid.md) |
| Fuzzy match scope | `rapidfuzz` retained for **parameter resolution only** (`focus(target)` / `open(target)` grounding inside `resolver.py`); removed from routing | `WRatio` is the right scoring function for fuzzy tool arguments; reimplementing it would be more code than the dependency costs | [0041](../decisions/0041-rapidfuzz-for-parameter-resolution.md) |
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
| Routing strategy | LLM-only — all transcripts routed through `LLMRouter.route()` directly from the pipeline worker; no wrapper | Hybrid router removed; single path simplifies architecture; scoped rapidfuzz to parameter resolution | [0040](../decisions/0040-llm-only-routing-replaces-hybrid.md) |
| ~~Hybrid routing~~ | ~~rapidfuzz first (≥95); escalate to LLM on miss~~ | Superseded by ADR-0040 | [0026](../decisions/0026-hybrid-routing-rapidfuzz-then-llm.md) *(superseded)* |
| ~~rapidfuzz threshold when LLM enabled~~ | ~~95 (up from 85)~~ | Superseded by ADR-0040 | [0027](../decisions/0027-rapidfuzz-threshold-tightening.md) *(superseded)* |
| Parameter resolver | `resolver.py` exports pure `resolve_window(target)` + `resolve_app(target)` functions; module-level `_set_config(cfg.llm)` hook injects fuzzy thresholds once at daemon startup; cache for Start-Menu + AppsFolder populated lazily | Pure-per-call API stays unit-testable; thresholds are a config knob, not a code edit; COM enumeration cost paid once on first `open` | [0042](../decisions/0042-resolver-module-design.md) |
| Primitive catalog | Nine verbs (LLM-visible names): `focus`, `type`, `open`, `close`, `close_window`, `press`, `wait`, `click`, `no_match` — plus bonus `scroll`. `type` / `open` register via `@tool(name=...)` from Python symbols `type_text` / `open_target` to avoid shadowing builtins. | Minimal orthogonal catalog; fits under small-MoE tool-calling reliability ceiling; composable into any voice-command plan | [0043](../decisions/0043-nine-verb-primitive-catalog.md) |
| Few-shot system prompt | `_SYSTEM_PROMPT_TEMPLATE` module constant in `llm_router.py`; 3 examples; `{default_browser}` substituted from `LLMConfig` at `LLMRouter.__init__`; ~680 prefill tokens including tools-array JSON; warmup seeds KV cache with this exact prefix. Legacy tool names (`focus_browser`, `new_tab`, `type_text`, `press_keys`) banned — guarded by `test_prompt_no_legacy_tool_names`. | One editable place for prompt; per-user browser preference without code changes; warmup guarantees first-call latency matches steady state | [0044](../decisions/0044-few-shot-system-prompt.md) |
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
| Focus tool `settle_ms` | `settle_ms = 200` on the `focus` verb (former `focus_browser` / `focus_terminal` / `focus_window` tools collapsed into a single catalog entry — ADR 0043) | Extra 50 ms over previous 150 ms value accounts for Windows 11 paint latency after `AttachThreadInput`-based focus; only applied on success, never after `FocusWindowError` | [0037](../decisions/0037-focus-window-attachthreadinput-workaround.md), [0033](../decisions/0033-per-tool-settle-ms-plus-wait-primitive.md) |

## Daemon Lifecycle

| Decision | Choice | Why | ADR |
|---|---|---|---|
| Single-instance lock | OS-level file locking (`msvcrt`/`fcntl`) + fixed ctypes HANDLE truncation + PID:GUID + cleanup handlers | Advisory file lock auto-released on crash; ctypes `c_void_p` for 64-bit HANDLE correctness | [0039](../decisions/0039-single-instance-lock-hardening.md) |

## Sprite Companion

| Decision | Choice | Why | ADR |
|---|---|---|---|
| Sprite isolation | Separate OS process via SSE, not in-process | Crash isolation; daemon has zero sprite knowledge | [0045](../decisions/0045-sprite-separate-process-via-sse.md) |
| Sprite renderer | pyglet 2.x | Per-pixel alpha, sprite-sheet primitives, HWND access, no Qt bloat | [0046](../decisions/0046-pyglet-over-tkinter-pyqt-web-overlay.md) |
| Character sheet format | Grid PNG + sidecar TOML | Art regen = image gen + TOML edit, no code changes | [0047](../decisions/0047-charsheet-grid-format-sidecar-toml.md) |
| Outbound telemetry | In-daemon EventBus + FastAPI SSE `/events` | Single broadcast channel for sprite, future dashboard, analytics | [0048](../decisions/0048-eventbus-sse-outbound-telemetry.md) |
| Miss chimes | Retained alongside sprite | Sprite supplements audio, does not replace it | [0049](../decisions/0049-miss-chimes-retained.md) |
| Pyglet transparent-overlay recipe | `sample_buffers=0` + `glClearColor` in `on_draw` every frame + `glBlendFuncSeparate` + per-sprite `blend_src/dest` + no manual `SetLayeredWindowAttributes` + no `DwmExtendFrameIntoClientArea` + startup `alpha_size` log | Six independent Win11 traps all produce the same opaque-black-background symptom; must be mitigated together or transparency fails | [0050](../decisions/0050-pyglet-transparent-overlay-windows-recipe.md) |
| Command HUD surface | Standalone chat-log overlay rendered in the same pyglet window as the sprite | Glanceable RPG-chat feedback per command; sharing the window avoids a second HWND | [0051](../decisions/0051-command-hud-overlay.md) |
| HUD summarization | Hybrid: rule table covers nine-verb catalog, LLM fallback on errors / unknown verbs | Cheap hot path, rich error explanations, bounded worst case | [0052](../decisions/0052-hybrid-rule-llm-summarization.md) |
| Multi-monitor docking | 30 Hz `GetCursorPos` poll → `MonitorFromPoint` → `rcWork` → `set_location` + `set_size`; PMv2 awareness mandatory | Sprite + HUD track the cursor; taskbar-excluded docking works on any taskbar edge | [0053](../decisions/0053-sprite-follows-cursor-monitor.md) |

All ADRs live in [`../decisions/`](../decisions/). Add a new row here whenever you add a new ADR.
