# CLAUDE.md — Voice Commander

This file is the durable context for any AI agent or human working on this project. Read it before doing anything.

## What we're building

A voice-driven command launcher for Windows — like Talon Voice, but you say the **actual command** ("copy", "new tab", "focus browser") instead of memorizing spoken shortcuts. A local-first daemon, no cloud, no LLM in the MVP.

**One-line flow:** press Scroll Lock → speak → press Scroll Lock → the matching tool runs.

**End-state vision (beyond MVP):** natural utterances with arguments ("open readme in the projects folder") routed via a small local LLM doing tool-calling. We get the fuzzy-match MVP working first; the LLM layer comes later.

## Core principles (non-negotiable)

1. **Docs before code.** Every architectural decision, library choice, and gotcha is written down in `docs/` before the corresponding code is written. ADRs are written when the decision is made, not retroactively.
2. **Phased delivery, each phase separately testable.** No phase is "done" until both automated tests pass AND the human has validated the behavior end-to-end.
3. **Tickets drive execution.** Every phase is broken into Kaizen OS subquests (via the `kaizenos-cli` skill). Agents and humans work through tickets one at a time. No parallel phase skipping.
4. **Modular, swappable subsystems.** Each module has one job and a narrow interface. You must be able to unit-test any subsystem without spinning up the whole daemon.
5. **Boil the ocean.** No workarounds, no "temporarily hard-coded", no dangling threads. When a thing is done, it's actually done — tests, docs, ADR, ticket closed.

## Architecture (locked)

```
HotkeyCtrl ──toggle──▶ Recorder ──WAV──▶ Transcriber ──text──▶ Matcher ──▶ Dispatcher ──▶ tool fn
   pynput             sounddevice         faster-whisper       rapidfuzz       invokes      tools/*
                                           (CUDA, small.en)                  + FeedbackSink
                                                                              (chime + log)
```

Subsystems connected by a thread-safe queue. Hotkey listener, audio capture, and the transcribe→match→dispatch worker each run on their own thread. Main thread only orchestrates. Feedback is audio-only — `winsound` chimes for start/stop/miss plus structured logs; no visual notifications (see ADR 0013).

Tools live in `src/voice_commander/tools/*.py` and register themselves via a `@tool(phrases=[...])` decorator. The registry auto-discovers them on daemon start. Adding a new tool = drop a file.

## Locked technical decisions

| Decision | Choice | Why |
|---|---|---|
| Package manager | `uv` | Fast, reproducible, pyproject.toml-native |
| Hotkey | Scroll Lock, single-tap toggle | Non-printable, won't conflict with typing |
| Hotkey listener | `pynput` (primary) / `keyboard` (fallback) | Supports Scroll Lock, cross-platform path |
| Audio capture | `sounddevice`, device-native rate, mono WAV | Opens stream at device's `default_samplerate` (e.g. 48 kHz for WASAPI); Phase-2 transcription resamples to 16 kHz internally |
| Transcription | `faster-whisper` `small.en` on **CUDA** | Sub-second latency on NVIDIA GPU |
| Fuzzy match | `rapidfuzz`, threshold ~85 | Fast, no ML deps, good-enough for Phase 1 |
| Tool registry | `@tool` decorator + auto-discovery | Phrases live next to code; zero boilerplate to add tools |
| Feedback | Windows `.wav` chimes via `winsound` only (no visual toasts) | Fire-and-forget, non-interruptive. Toasts dropped in ADR 0013 (UX + CUDA-init fragility). |
| Recording retention | Single overwriting file `outputs/recorded.wav` — newest only | Simpler; transcriber always reads one fixed path; no retention subsystem needed |
| Config | `config.toml` at project root | Tweak threshold/hotkey/model without editing code |
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
│   ├── decisions/          # ADRs — one per big choice
│   ├── gotchas.md          # Windows traps, CUDA DLLs, threading pitfalls
│   ├── libraries.md        # every dep + why
│   ├── testing-strategy.md
│   ├── references/         # vendored framework docs (faster-whisper, rapidfuzz, etc.)
│   └── superpowers/
│       ├── specs/          # design docs from brainstorming
│       └── plans/          # implementation plans from writing-plans
├── src/voice_commander/
│   ├── __main__.py         # daemon entrypoint
│   ├── daemon.py           # wires subsystems, owns the queue
│   ├── hotkey.py
│   ├── recorder.py
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

- `HotkeyController(key, on_toggle)` — pynput listener, fires callback on toggle.
- `Recorder(output_dir, channels, device)` — `start()` queries device native rate, records; `stop() -> Path` writes WAV at that rate.
- `Transcriber(model_size, device)` — `load()` once; `transcribe(wav) -> TranscriptionResult`.
- `ToolRegistry` — `@tool(phrases=[...])` decorator; `discover(pkg)` auto-imports.
- `Matcher(registry, threshold)` — rapidfuzz `match(utterance) -> MatchResult`.
- `Dispatcher(feedback)` — runs tool fn; reports to feedback sink.
- `FeedbackSink` — chimes + log. Swap to `NullFeedbackSink` in tests.
- `Daemon` — the only place concretes meet. Owns the queue and threads.

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
- [ ] Phase 3 (router + first tool) complete
- [ ] Phase 4 (full MVP toolset) complete
- [ ] Phase 5 (hardening) complete
