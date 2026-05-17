# CLAUDE.md — Voice Commander

Canonical entry point for any AI agent or human working on this project. **Read this file first**, then lazy-load the topical references from the map below as you need them.

## Documentation discipline — non-negotiable

**Docs are updated in the SAME change as the code. Not "later", not "a follow-up". A change is not done until its docs are.** Stale documentation is a defect, ranked the same as a failing test.

When you change anything, update the matching docs *before* calling the work complete:

- **New feature or behaviour change** → write or update an ADR in `docs/decisions/`, refresh the **Current state** section below, and add/update its row in `docs/agents/technical-decisions.md`.
- **Config changed** → update `config.toml.example`, including the inline comment explaining each key.
- **Touched a module, class, or function** → its docstrings must still be true afterwards. A stale docstring is a bug — fix it in the same edit, do not leave it.
- **New subsystem or a non-obvious multi-step flow** → add an overview doc under `docs/` and link it from `docs/index.md`.
- **Used an external library or framework** → vendor the researched API reference into `docs/references/` and cite it from the code that depends on it.

Any reviewer — human or agent — must reject code whose docs are stale, missing, or contradict the implementation. If you find a doc that no longer matches the code, that is a defect: fix it, never ignore it.

## What we're building

A voice-driven command launcher for Windows — like Talon Voice, but you say the actual command ("copy", "open spotify", "search for cats") instead of memorizing spoken shortcuts. A local-first daemon: no cloud, no third-party speech service.

**One-line flow:** press Scroll Lock → speak → VAD auto-segments on silence → VerbRouter matches command/primitive → Dispatcher fires it → Scroll Lock to end session.

**Current state.** `VerbRouter` is the sole routing path: every transcript runs through `VerbRouter.route()`, which first attempts to exact-match the punctuation-stripped, lowercased transcript against all registered command/workflow names and their synonyms (`entry.phrases`) — longest token-count match wins, and names with underscores match spoken words (`close_window` matches "close window") — then falls back to primitive verb routing (`click`, `scroll`, `focus X`, `open X`, `type X`, `press X`, `wait N`). There is no LLM routing path (removed by ADR 0082). Verb misses chime once. A `chain` meta-verb (`chain copy paste save`) — see ADR 0085 — fans one utterance into a multi-step `Plan` whose user steps are interleaved with synthetic `wait(ms=255)` steps flagged `ToolCall.internal=True` so the Dispatcher executes them but suppresses their `tool_fired` events and HUD lines. When a primitive verb (`focus`, `open`, …) is said without an argument and a `BarePickerProvider` is registered for it (ADR 0083), the router emits a synthetic `__picker.open` step that the daemon intercepts to open an on-screen numbered modal of candidate targets; the next utterance is coerced to an integer ("three" / "3") and dispatched as the chosen item's pre-built `Plan`. The picker is a sub-state of the running voice session — the mic stays hot, the session continues after selection. The framework lives under `src/voice_commander/picker/` and ships two providers today: `focus` (backed by an MRU ring buffer driven by a `WinEventHook(EVENT_SYSTEM_FOREGROUND)`) and `tabs` (ADR 0084 — UI Automation enumeration of the foreground Chromium browser's page-tab strip; saying "tabs." opens a numbered modal of the current tabs, saying the number activates that tab via `SelectionItemPattern.Select()`. Empty list when foreground is not a recognised Chromium browser ⇒ miss-chime, matching the "brings up nothing if the active window doesn't have tabs" spec). The `tabs` primitive is `internal=true`/picker-only; not LLM-visible. The Chromium allowlist excludes Electron apps (VS Code, Slack, Discord) so their incidental TabControls don't masquerade as browser tabs. The sprite process renders the modal via two new SSE events: `picker.open` (payload `{verb, items: [{n, label}]}`) and `picker.close` (payload `{verb, reason}`). `rapidfuzz` survives only inside `resolver.resolve_window` / `resolve_app` for grounding `focus(target)` / `open(target)`. The tool catalogue is exactly 11 primitives — 7 action (`focus`, `type`, `open`, `press`, `wait`, `click`, `scroll`) plus 4 perception (`get_active_window_title`, `get_cursor_pos`, `read_clipboard`, `ocr_region`). **Dictation mode** (ADR 0086) is a voice-session sub-state: saying bare "dictate" or pressing Right Ctrl (`dictation_key`, default `ctrl_r`) during an active session enters dictation; VAD audio accumulates in `DictationSession`; saying the end word "done" (exact standalone, configurable via `[dictation] end_word`) or a second Right Ctrl press triggers exit. On exit, the concatenated audio is encoded to a 16 kHz mono WAV and POSTed to a remote whisper.cpp `/inference` endpoint (`[dictation] endpoint`, default `http://192.168.4.200:8765/inference`); the transcription is inserted at the cursor via a clipboard round-trip (`Ctrl+V`); `outputs/dictation/last.wav` + `last.txt` are retained for web re-transcribe at `/page/dictation`. The in-session mute toggle (ADR 0025) is removed; `HotkeyConfig.mute_key` was renamed `dictation_key`; dictation adds no new LLM-visible primitive. **Elements mode** (ADR 0087) is a one-shot voice-session sub-state: saying "element"/"elements" during an active session scans the foreground window's UI Automation tree on a worker thread, publishes an `elements.show` event so the sprite draws a numbered click-through overlay on every clickable control (buttons, links, list/tab/tree items, edits, …), and left-clicks (coordinate click via `pyautogui` on the element's centre) the control whose number the next utterance names; any non-number utterance or an 8 s timeout dismisses the overlay (`elements.hide`). The package lives under `src/voice_commander/elements/` (UIA scanner with a pure filter/index core, `ElementsSession` state machine, spoken-number parser, clicker, desktop/monitor helpers) plus `src/voice_sprite/elements_overlay.py`; it adds an `[elements]` config section (`max_elements`, `scan_timeout_s`, `hint_timeout_s`) and a `uiautomation` dependency, and no new LLM-visible primitive. Browser page content is visible to the scanner only when Chrome/Edge run with `--force-renderer-accessibility`; native app UI works unconditionally. An in-process `EventBus` broadcasts daemon state changes (session, tool_fired, miss, heartbeat, plan_outcome, transcript) via an SSE `/events` endpoint; the `transcript` event is published immediately after transcription with `{text, confidence}` (ADR 0079). A separate `voice_sprite` process consumes these events and renders an animated pixel-art companion (pyglet, click-through overlay) that mirrors daemon state. The HUD overlay renders a light-blue info-status line for each `transcript` event, followed by the `tool_fired` line(s), then a `plan_outcome` line when status is non-ok — per-utterance order: transcript → tool_fired(s) → plan_outcome (ADR 0079). `CursorDock` snaps the sprite window to the cursor's current monitor work area at 30 Hz, keeping the companion visible across multi-monitor desktops.

**Node-graph system (PR #51, ADRs 0062–0068).** Commands and workflows are authored as directed acyclic graphs via the Builder UI (`/page/builder`). Each graph is a canonical JSON file with typed nodes, control-flow edges (`ok`/`error`/`true`/`false`), and data-flow wires carrying return values between nodes. `GraphRuntime` executes these DAGs — walking nodes in topological order, resolving kwargs from data wires, and supporting `control.branch`, `control.foreach`, and cross-graph calls (`command.X`, `workflow.X`). Graphs register as `ToolEntry` closures so `VerbRouter` can match them by name and synonym. A `llm_visible` field exists in the graph JSON schema for backward compatibility but is no longer used by the registrar (LLM router removed, ADR 0082). Perception primitives — `read_clipboard`, `get_active_window_title`, `get_cursor_pos`, and `ocr_region(x,y,w,h)` (ADR 0066, winrt primary / Tesseract fallback) — provide the observation layer that branch nodes need for conditional execution. Legacy linear `WorkflowDef.steps[]` workflows are supported at runtime alongside the new graph format; editing one in the Builder UI converts it to a DAG. The Builder UI uses a per-category visual taxonomy (ADR 0069) — five gradient color families and shape modifiers defined in CSS custom properties, with port semantic colors applied via `data-port-name` attribute selectors. The Builder palette sidebar was renamed from "Pipeline" to "Primitives" (ADR 0078) and partitions nodes into two lists sourced from `/graph/palette`: "Primitives" lists the 7 action primitives (`click`, `focus`, `open`, `press`, `scroll`, `type`, `wait`) and "Perception" lists the 4 observation primitives (`read_clipboard`, `get_active_window_title`, `get_cursor_pos`, `ocr_region`). Every utterance is captured as a span tree in `outputs/runs.db`, streamed live to `/page/runs`, the Builder UI overlay, and the `vc debug` / `vc tail` CLIs (ADR 0070).

**Builder React SPA (ADR 0071).** `/page/builder` now serves a **React 18.3 + Vite 5 + TypeScript SPA** (`web/builder-ui/`), built to `src/voice_commander/web/static/builder/`. **React Flow** replaces Drawflow on the canvas. The SPA is powered by three Zustand stores (`graphStore`, `runsStore`, `uiStore`) and ships an **n8n-style runs panel** with SSE live tail (`run.appended`), structured rows with status icons, a span-tree drawer, and a copy-as-prompt export. The **PropertiesPane empty state** (no node selected) is a `GraphSettingsPanel` editing graph-level `description` + `synonyms` via a chip-style `SynonymsEditor` (ADR 0080) — authors register Whisper mistranscriptions (e.g. `P.A.C.T.` → `paste`) without leaving the Builder; selecting a node swaps to the existing `KwargsForm`. A **3-bucket runtime error taxonomy** (`program` / `wiring` / `infra`) is implemented in `observability/errors.py` and tags spans and runs at source (`llm` bucket removed by ADR 0082); schema v1→v2 adds `error_category` columns to `runs` and `spans` tables. The SPA requires a one-time build step: `cd web/builder-ui && pnpm install && pnpm build` (or `make builder-install && make builder-build`). Fresh clones without the build show a friendly stub page.

## Core principles (non-negotiable)

1. **Docs before code.** Every architectural decision, library choice, and gotcha is written down in `docs/` before the corresponding code is written. ADRs are authored at the time of the decision, not retroactively.
2. **Phased delivery.** Features ship behind validation gates. Automated tests green **and** a human has validated the behaviour end-to-end before a feature is "done."
3. **Modular, swappable subsystems.** Each module has one job and a narrow interface. Any subsystem must be unit-testable without spinning up the whole daemon.
4. **Boil the ocean.** No workarounds, no "temporarily hard-coded," no dangling threads. Done means: tests pass, docs updated, ADR filed, PR merged.
5. **Local-only infrastructure — Windows-only project.** This is a Windows-native daemon (winsound chimes, pynput hotkeys, sounddevice via WASAPI, scroll-lock, `winrt` OCR). All CI, automation, validation, and tooling MUST run on the developer's local Windows machine or a self-hosted Windows runner. **Do not add GitHub-hosted CI runners** (`runs-on: ubuntu-latest`, `runs-on: windows-latest`, etc.) — they pollute the repo with workarounds for native deps that don't exist on the target platform. If CI is desired, register a self-hosted Windows runner and use `runs-on: [self-hosted, Windows]`.

## Architecture at a glance

```
HotkeyCtrl ─toggle─▶ StreamingRecorder ─ndarray─▶ Transcriber ─text─▶ VerbRouter ─plan─▶ Dispatcher ──▶ tool fn
  pynput            sounddevice + soxr +          faster-whisper      registry match   run_plan()     + resolver.*
                    silero-vad (48k→16k)          (CUDA, small.en)    + synonyms        + FeedbackSink    (focus/open)
                                                       │ transcript    + primitives                    │ graph tool?
                                                       ▼ event                                          ▼
                                                  EventBus                                         GraphRuntime
                                                  (pub/sub)                                        (DAG executor)
                                                       │miss
                                                       ▼
                                                  feedback.on_miss()
                                                       │
                                                       ▼
                                                  EventBus ──SSE /events──▶ voice_sprite (separate process)
                                                  (pub/sub)                  pyglet + StateMachine + ChatLogRenderer
                                                                             + CursorDock (30 Hz) + Summarizer
```

Five long-lived threads (PortAudio callback → VAD worker → pipeline worker, hotkey listener, plus 1 Hz heartbeat) connected by thread-safe queues. Feedback is audio miss chimes (`winsound`) supplemented by an on-screen sprite companion (separate process via SSE). Tools live under `src/voice_commander/tools/` and register via a bare `@tool` decorator + sidecar `.toml`; the registry auto-discovers them on daemon start. Graph-backed commands and workflows live under `commands/` as canonical JSON and register as `ToolEntry` closures via `registrar.py`; the Builder UI (`/page/builder`, React Flow canvas) is the primary authoring surface. Perception primitives (`tools/ocr.py`, `tools/perception.py`) provide observation inputs for graph branch nodes.

Full subsystem contracts and type stubs live in [`docs/architecture.md`](docs/architecture.md).

## Agent reference map

Lazy-load the file that matches your question. Do not read the whole tree up front.

| Topic | File |
|---|---|
| Subsystem contracts, type stubs, thread topology, data flow | [`docs/architecture.md`](docs/architecture.md) |
| Locked technical decisions — one-line summary per choice | [`docs/agents/technical-decisions.md`](docs/agents/technical-decisions.md) |
| Repo layout tree + directory invariants | [`docs/agents/repo-layout.md`](docs/agents/repo-layout.md) |
| **Visual end-to-end validation protocol — MANDATORY for any user-visible feature** | [`docs/agents/visual-e2e-testing.md`](docs/agents/visual-e2e-testing.md) |
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
- **Visual E2E for user-visible features — non-negotiable.** Any feature that touches pixels, focus state, audio, hotkeys, or daemon ↔ sprite ↔ web-UI IPC ships with an automated harness in `scripts/` that drives the feature the way a real user would, captures evidence (screenshot, foreground hwnd, log), and asserts on that evidence. Unit tests are necessary but not sufficient. Read the protocol — including the eight rules and the picker reference implementation — at [`docs/agents/visual-e2e-testing.md`](docs/agents/visual-e2e-testing.md) **before** writing any harness code. Patterns to copy: `scripts/picker_modal_smoke.py` (in-process render smoke) and `scripts/picker_visual_e2e.py` (subprocess sprite + SSE + PrintWindow + GetForegroundWindow assertion).
