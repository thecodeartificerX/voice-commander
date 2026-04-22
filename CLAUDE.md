# CLAUDE.md — Voice Commander

Canonical entry point for any AI agent or human working on this project. **Read this file first**, then lazy-load the topical references from the map below as you need them.

## What we're building

A voice-driven command launcher for Windows — like Talon Voice, but you say the actual command ("copy", "open spotify", "search for cats") instead of memorizing spoken shortcuts. A local-first daemon: no cloud, no third-party speech service.

**One-line flow:** press Scroll Lock → speak → VAD auto-segments on silence → LLM returns a plan → Dispatcher fires it → Scroll Lock to end session.

**Current state.** LLM-driven tool-calling is the default and only routing path. Every transcript goes to `LLMRouter.route()`, which POSTs to a local LM Studio endpoint and returns an ordered plan of tool calls from the nine-verb catalog (ADR 0043). The system prompt carries three few-shot examples templated from `[llm].default_browser` (ADR 0044). `rapidfuzz` is retained only for grounding tool arguments (`focus(target)` / `open(target)`) inside the pure-function `resolver` module — never for routing. There is no offline fuzzy-match fallback: if LM Studio is down, every utterance miss-chimes. An in-process `EventBus` broadcasts daemon state changes (session, tool_fired, miss, heartbeat) via an SSE `/events` endpoint. A separate `voice_sprite` process consumes these events and renders an animated pixel-art companion (pyglet, click-through overlay) that mirrors daemon state.

## Core principles (non-negotiable)

1. **Docs before code.** Every architectural decision, library choice, and gotcha is written down in `docs/` before the corresponding code is written. ADRs are authored at the time of the decision, not retroactively.
2. **Phased delivery.** Features ship behind validation gates. Automated tests green **and** a human has validated the behaviour end-to-end before a feature is "done."
3. **Modular, swappable subsystems.** Each module has one job and a narrow interface. Any subsystem must be unit-testable without spinning up the whole daemon.
4. **Boil the ocean.** No workarounds, no "temporarily hard-coded," no dangling threads. Done means: tests pass, docs updated, ADR filed, PR merged.

## Architecture at a glance

```
HotkeyCtrl ──toggle──▶ StreamingRecorder ──NDArray──▶ Transcriber ──text──▶ LLMRouter ─plan─▶ Dispatcher ──▶ tool fn
   pynput             sounddevice+Resampler+VADGate   faster-whisper        httpx→LM Studio  run_plan()    tools/*
                      (device-native→16kHz, silero)    (CUDA, small.en)    (few-shot prompt) + FeedbackSink
                                                                               │miss          + resolver.* (param grounding)
                                                                               ▼
                                                                          feedback.on_miss()
                                                        ┌──────────────────────┘
                                                        ▼
                                                   EventBus ──SSE /events──▶ voice_sprite (separate process)
                                                   (pub/sub)                 pyglet + StateMachine
```

Five long-lived threads (PortAudio callback → VAD worker → pipeline worker, hotkey listener, plus 1 Hz heartbeat) connected by thread-safe queues. Feedback is audio miss chimes (`winsound`) supplemented by an on-screen sprite companion (separate process via SSE). Tools live under `src/voice_commander/tools/` and register via a bare `@tool` decorator + sidecar `.toml`; the registry auto-discovers them on daemon start.

Full subsystem contracts and type stubs live in [`docs/architecture.md`](docs/architecture.md).

## Agent reference map

Lazy-load the file that matches your question. Do not read the whole tree up front.

| Topic | File |
|---|---|
| Subsystem contracts, type stubs, thread topology, data flow | [`docs/architecture.md`](docs/architecture.md) |
| Locked technical decisions — one-line summary per choice | [`docs/agents/technical-decisions.md`](docs/agents/technical-decisions.md) |
| Repo layout tree + directory invariants | [`docs/agents/repo-layout.md`](docs/agents/repo-layout.md) |
| ADRs — one per locked decision | [`docs/decisions/`](docs/decisions/) |
| Windows traps, CUDA DLL quirks, threading pitfalls | [`docs/gotchas.md`](docs/gotchas.md) |
| Every dependency + rationale | [`docs/libraries.md`](docs/libraries.md) |
| Four-layer test pyramid, per-phase validation | [`docs/testing-strategy.md`](docs/testing-strategy.md) |
| Vendored upstream framework docs | [`docs/references/`](docs/references/) |
| Design specs from brainstorming | [`docs/superpowers/specs/`](docs/superpowers/specs/) |
| Implementation plans | [`docs/superpowers/plans/`](docs/superpowers/plans/) |
| User-facing install, config, contributing guide | [`README.md`](README.md) |

When you add a new `docs/agents/*.md` file (or any new top-level reference doc), append a row here so future agents can find it.

## Workflow for agents and humans

1. **Spec** → [`docs/superpowers/specs/`](docs/superpowers/specs/) (written during brainstorming).
2. **Plan** → [`docs/superpowers/plans/`](docs/superpowers/plans/) (written by the `superpowers:writing-plans` skill from the spec).
3. **Tickets** → GitHub issues. One issue per testable unit of work.
4. **Execute** → claim an issue, implement, run validation, open PR. Humans validate at every phase boundary.
5. **Document** → any new decision gets an ADR in [`docs/decisions/`](docs/decisions/) at the time of the decision, with its summary row added to [`docs/agents/technical-decisions.md`](docs/agents/technical-decisions.md).

## Rules for AI agents

- **Sub-agents do the typing.** Use Sonnet for coding, Haiku for research / summarization / tool-calling, Opus only when Sonnet stalls.
- **Research before implementing.** If a library is new to the repo, land its reference in [`docs/references/`](docs/references/) before writing code against it. Parallelise research with Haiku sub-agents.
- **Parallelize aggressively.** Two tasks without a dependency run concurrently, in one message.
- **Never skip validation.** A phase is not done until the human confirms.
- **Never invent library APIs.** If unsure, read the vendored reference or fetch the current upstream docs. No guessing.
- **Ask, don't assume.** When requirements are unclear, stop and ask.
