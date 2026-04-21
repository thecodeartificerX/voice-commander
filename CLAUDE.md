# CLAUDE.md — Voice Commander

This file is the durable context for any AI agent or human working on this project. Read it before doing anything.

## What we're building

A voice-driven command launcher for Windows — like Talon Voice, but you say the **actual command** ("copy", "new tab", "focus browser") instead of memorizing spoken shortcuts. A local-first daemon, no cloud, no LLM in the MVP.

**One-line flow:** press Scroll Lock → speak naturally → VAD auto-segments on silence → commands fire immediately → press Scroll Lock to end session.

**End-state vision (beyond MVP):** natural utterances with arguments ("open readme in the projects folder") routed via a small local LLM doing tool-calling. We get the fuzzy-match MVP working first; the LLM layer comes later.

## Core principles (non-negotiable)

1. **Docs before code.** Every architectural decision, library choice, and gotcha is written down in `docs/` before the corresponding code is written. ADRs are written when the decision is made, not retroactively.
2. **Phased delivery, each phase separately testable.** No phase is "done" until both automated tests pass AND the human has validated the behavior end-to-end.
3. **Tickets drive execution.** Every phase is broken into Kaizen OS subquests (via the `kaizenos-cli` skill). Agents and humans work through tickets one at a time. No parallel phase skipping.
4. **Modular, swappable subsystems.** Each module has one job and a narrow interface. You must be able to unit-test any subsystem without spinning up the whole daemon.
5. **Boil the ocean.** No workarounds, no "temporarily hard-coded", no dangling threads. When a thing is done, it's actually done — tests, docs, ADR, ticket closed.

## Architecture (locked)

```
HotkeyCtrl ──toggle──▶ StreamingRecorder ──NDArray──▶ Transcriber ──text──▶ Matcher ──▶ Dispatcher ──▶ tool fn
   pynput             sounddevice+Resampler+VADGate   faster-whisper       rapidfuzz       invokes      tools/*
                      (device-native→16kHz, silero)    (CUDA, small.en)                  + FeedbackSink
                                                                                          (chime + log)
```

Subsystems connected by thread-safe queues. Four long-lived threads: PortAudio callback thread pushes raw PCM to `raw_q`; VAD worker thread drains `raw_q`, resamples 48k→16k via soxr, runs silero-vad, and emits complete utterance ndarrays to `utt_q`; pipeline worker thread drains `utt_q` and runs transcribe→match→dispatch; hotkey listener thread fires session open/close. Main thread only orchestrates. Feedback is audio-only — `winsound` chimes for session start/stop/miss plus structured logs; no visual notifications (see ADR 0013).

Tools live in `src/voice_commander/tools/*.py` and register themselves via a `@tool(phrases=[...])` decorator. The registry auto-discovers them on daemon start. Adding a new tool = drop a file.

## Locked technical decisions

| Decision | Choice | Why |
|---|---|---|
| Package manager | `uv` | Fast, reproducible, pyproject.toml-native |
| Hotkey | Scroll Lock, single-tap toggle | Non-printable, won't conflict with typing |
| Hotkey listener | `pynput` (primary) / `keyboard` (fallback) | Supports Scroll Lock, cross-platform path |
| Audio capture | `sounddevice`, device-native rate, mono float32 | Opens `InputStream` at device's `default_samplerate` (e.g. 48 kHz for WASAPI); raw frames pushed to `raw_q` |
| Resampler | `soxr.ResampleStream`, device-native → 16 kHz, HQ | True streaming resampler with internal filter state; one instance per session. ADR 0017. |
| VAD engine | `silero-vad` ONNX, 512-sample 16 kHz frames, `onnxruntime` on CPU | Neural VAD, float confidence score, configurable threshold; robust to keyboard/fan noise. ADR 0016. |
| VAD gate | `VADGate` — pre-roll buffering + utterance accumulation + max-utterance guard | Pre-roll captures audio before speech-start; max-utterance guard prevents unbounded buffers |
| Session model | Toggle: Scroll Lock opens session, second press closes it; optional mute key suspends/resumes stream within a session (two independent flags: `session_active`, `muted`) | While open, VAD auto-segments; zero keypresses between commands. ADR 0015, 0025. |
| Transcription | `faster-whisper` `small.en` on **CUDA**; utterance ndarray passed directly | Sub-second latency on NVIDIA GPU; ndarray handoff skips temp-file I/O. ADR 0018. |
| Fuzzy match | `rapidfuzz`, threshold ~85 | Fast, no ML deps, good-enough for Phase 1 |
| Tool registry | `@tool` decorator + auto-discovery | Phrases live next to code; zero boilerplate to add tools |
| Feedback | Windows `.wav` chimes via `winsound` only (no visual toasts) | Fire-and-forget, non-interruptive. Toasts dropped in ADR 0013 (UX + CUDA-init fragility). |
| Debug artifact | `outputs/last_utterance.wav` — async overwrite per utterance | Written fire-and-forget after enqueuing for transcription; for post-mortem inspection only. ADR 0018, 0019. |
| Config | `config.toml` at project root | Tweak threshold/hotkey/model/VAD params without editing code |
| CUDA DLL loading | `nvidia-cublas-cu12` + `nvidia-cudnn-cu12` pip packages + `_cuda_setup.register()` preloads DLLs via `ctypes.WinDLL` before `faster_whisper` import | Venv self-contained; no system CUDA install needed; sidesteps Windows native DLL-search quirks. ADR 0012. |

Every one of these has (or will have) a full ADR in `docs/decisions/`.

## Phase plan

Each phase ends in a validation gate. No skipping.

- **Phase 0** — Scaffolding + full docs (no code yet). *Validation: doc review.*
- **Phase 1** — Hotkey + audio capture to WAV, start/stop chimes. *Validation: press key, speak, verify WAV.*
- **Phase 2** — Transcription wired (CUDA, `small.en`, model preloaded). *Validation: transcript appears in log within ~1s.*
- **Phase 3** — Router + registry + one tool (`copy`) end-to-end with audio-chime feedback. *Validation: say "copy" with text selected → clipboard updates.*
- **Phase 4** — Full MVP toolset (14 tools: clipboard, window, browser, system). Tune phrases + threshold. Miss-beep on low confidence. *Validation: full command run-through, measure miss rate.*
- **Phase 5** — Hardening: config file, retention cleanup, system tray icon, error recovery, complete unit + integration test suite. *Validation: green suite + stability soak.*
- **Phase 6+ (future)** — Local LLM intent router for argument-bearing commands, per-app command sets, wake word.

## MVP toolset (Phase 4)

**Clipboard:** copy, paste, cut, select all
**Window/focus:** focus browser (default browser), focus terminal, minimize, maximize
**Browser:** new tab, close tab, reopen tab, reload
**System:** lock screen, take screenshot

## Repo layout

```
voice-commander/
├── pyproject.toml          # uv-managed
├── config.toml             # user-tweakable runtime settings
├── CLAUDE.md               # this file
├── README.md
├── docs/
│   ├── index.md            # doc table of contents
│   ├── architecture.md
│   ├── decisions/          # ADRs 0001–0019 — one per big choice
│   ├── gotchas.md          # Windows traps, CUDA DLLs, threading pitfalls
│   ├── libraries.md        # every dep + why
│   ├── testing-strategy.md
│   ├── references/         # vendored framework docs (faster-whisper, rapidfuzz, etc.)
│   └── superpowers/
│       ├── specs/          # design docs from brainstorming
│       └── plans/          # implementation plans from writing-plans
├── src/voice_commander/
│   ├── __main__.py         # daemon entrypoint
│   ├── daemon.py           # StreamingDaemon + build_streaming_daemon factory
│   ├── hotkey.py
│   ├── streaming_recorder.py  # VAD streaming audio capture (replaces recorder.py)
│   ├── resampler.py           # soxr streaming resampler wrapper
│   ├── vad_gate.py            # silero-vad pre-roll + utterance accumulation
│   ├── transcriber.py
│   ├── matcher.py
│   ├── dispatcher.py
│   ├── feedback.py
│   ├── registry.py
│   ├── config.py
│   └── tools/
│       ├── clipboard.py
│       ├── window.py
│       ├── browser.py
│       └── system.py
├── tests/
│   ├── unit/               # one file per src module
│   └── integration/        # canned-WAV end-to-end
├── outputs/                # rolling WAVs (gitignored)
└── assets/sounds/          # start.wav, stop.wav, miss.wav
```

## Subsystem contracts (summary — full details in `docs/architecture.md`)

- `HotkeyController(bindings: dict[str, Callable])` — pynput listener with multi-binding dispatch table; single Listener, one callback per registered key. An optional mute key (configurable, disabled by default) suspends the audio stream within a session without ending it — two independent flags (`session_active`, `muted`). See ADR 0025.
- `Resampler(src_rate, dst_rate)` — streaming soxr resampler; `process(chunk) -> ndarray`; `flush() -> ndarray`; `reset()`. One instance per session; not shared across threads.
- `VADGate(model, threshold, ...)` — silero-vad + pre-roll ring buffer + utterance accumulation; `process(frame_16k) -> ndarray|None`; returns completed utterance on speech-end or max-utterance guard; `reset()` for clean session start.
- `StreamingRecorder(device, channels, vad_gate, utterance_sink)` — owns `sd.InputStream`; `open_session()` starts stream + VAD worker thread; `close_session()` stops stream and joins VAD worker; calls `utterance_sink(ndarray)` on the VAD worker thread when a complete utterance is detected.
- `Transcriber(model_size, device)` — `load()` once; `transcribe(audio: ndarray|Path) -> TranscriptionResult`.
- `ToolRegistry` — `@tool(phrases=[...])` decorator; `discover(pkg)` auto-imports.
- `Matcher(registry, threshold)` — rapidfuzz `match(utterance) -> MatchResult`.
- `Dispatcher(feedback)` — runs tool fn; reports to feedback sink.
- `FeedbackSink` — chimes + log. Swap to `NullFeedbackSink` in tests.
- `StreamingDaemon` — the only place concretes meet. Owns `utt_q` and pipeline thread; `run(hotkey_key)` blocks until shutdown.

## Workflow for agents and humans

1. **Spec** lives in `docs/superpowers/specs/`. Written during brainstorming.
2. **Plan** lives in `docs/superpowers/plans/`. Written by the `superpowers:writing-plans` skill from the spec.
3. **Tickets** live in Kaizen OS (area → epic → subquests) via the `kaizenos-cli` skill. Each subquest = one testable unit of work.
4. **Execution**: agents claim the next open subquest, complete it, run validation, mark done, move to the next. Human validates at every phase boundary.
5. **Documentation**: any new decision gets an ADR in `docs/decisions/` at the time of the decision, not after.

## Rules for AI agents

- **Sub-agents do the typing.** Use Sonnet sub-agents for coding, Haiku for research/summarization, Opus only when Sonnet stalls.
- **Research before implementing.** Reference docs for every library land in `docs/references/` before the library is used. An army of Haiku sub-agents gathers them in parallel.
- **Parallelize aggressively.** Any two tasks without a dependency run concurrently.
- **Never skip validation.** A phase is not done until the human confirms.
- **Never invent library APIs.** If unsure, read the vendored reference in `docs/references/` or fetch the current docs. No guessing.
- **Ask, don't assume.** When requirements are unclear, stop and ask.

## Current status

- [x] Brainstorm complete
- [x] Design doc written to `docs/superpowers/specs/`
- [x] Reference docs gathered in `docs/references/`
- [x] Kaizen OS area + subquests created
- [x] Implementation plan written to `docs/superpowers/plans/`
- [x] Phase 0 (scaffolding + docs) complete
- [x] Phase 1 (hotkey + audio) complete
- [x] Phase 2 (transcription) complete
- [x] Phase 3 (router + first tool) complete — delivered as part of Phase 4
- [x] Phase 4 (full MVP toolset) complete
- [x] Phase 5 (hardening) complete
- [x] VAD streaming refactor complete — `Recorder` → `StreamingRecorder` + `VADGate` + `Resampler`; `Phase*Daemon` → `StreamingDaemon` (ADRs 0015–0019)
