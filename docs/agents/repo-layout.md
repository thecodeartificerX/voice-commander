# Repo Layout

One-line description per directory and key file. Lazy-referenced from `CLAUDE.md`.

```
voice-commander/
├── pyproject.toml                  # uv-managed dependencies + tool config
├── uv.lock                         # committed lockfile
├── config.toml                     # single source of truth — all runtime settings
├── start.ps1                       # Windows launcher + device picker
├── CLAUDE.md                       # canonical entry point for agents / humans
├── README.md                       # user-facing pitch, install, contributing
│
├── docs/
│   ├── index.md                    # guided reading order
│   ├── architecture.md             # subsystem diagram + full component contracts
│   ├── libraries.md                # every dependency + why
│   ├── gotchas.md                  # Windows traps, CUDA DLL quirks, threading pitfalls
│   ├── testing-strategy.md         # four-layer test pyramid + validation gates
│   ├── agents/                     # agent-oriented lazy references (this folder)
│   │   ├── repo-layout.md          # this file
│   │   └── technical-decisions.md  # one-line summary of each locked decision
│   ├── decisions/                  # ADRs 0001–00NN, one per locked decision
│   ├── references/                 # vendored upstream framework docs
│   └── superpowers/
│       ├── specs/                  # design docs from brainstorming
│       └── plans/                  # implementation plans
│
├── src/voice_commander/
│   ├── __main__.py                 # daemon entrypoint
│   ├── daemon.py                   # StreamingDaemon + build_streaming_daemon factory
│   ├── hotkey.py                   # pynput multi-binding listener
│   ├── streaming_recorder.py       # sd.InputStream + VAD worker thread
│   ├── resampler.py                # soxr streaming resampler wrapper
│   ├── vad_gate.py                 # silero-vad + pre-roll + utterance accumulation
│   ├── transcriber.py              # faster-whisper wrapper (imports _cuda_setup first)
│   ├── _cuda_setup.py              # ctypes DLL preloading shim (Windows-only)
│   ├── matcher.py                  # rapidfuzz phrase matcher
│   ├── plan.py                     # Plan + ToolCall value objects for LLM router
│   ├── dispatcher.py               # runs matched tool, reports to feedback sink
│   ├── feedback.py                 # Windows / Null / Capturing feedback sinks
│   ├── llm_router.py               # LM Studio tool-call planner (httpx client)
│   ├── prompt_template.txt         # external system prompt template (hot-reloadable)
│   ├── registry.py                 # @tool decorator + auto-discovery
│   ├── tool_metadata.py            # sidecar TOML read/write + per-tool file locking
│   ├── tool_schema.py              # Python sig → OpenAI JSON Schema generator
│   ├── config.py                   # config loader + deep-merge + validation
│   ├── single_instance.py          # one-daemon guard
│   ├── validator.py                # startup sig/TOML drift checker (7 rules)
│   ├── tools/                      # @tool groups (auto-discovered at import)
│   │   ├── _win32.py               # Windows-specific helpers (focus_window_by_exe, etc.)
│   │   ├── clipboard.py + .toml
│   │   ├── window.py    + .toml
│   │   ├── browser.py   + .toml
│   │   ├── system.py    + .toml
│   │   ├── mouse.py     + .toml
│   │   └── primitives.py + .toml
│   └── web/                        # embedded FastAPI management UI
│       ├── app.py                  # routes
│       ├── prompt.py               # Prompt Inspector routes (inspect/edit/save)
│       ├── server.py               # uvicorn daemon thread
│       ├── static/                 # htmx + tailwind bundles, app.css
│       ├── templates/              # Jinja2 templates (HTMX fragments)
│       │   └── _prompt_inspector.html  # Prompt Inspector panel template
│   └── event_bus.py                # in-process pub/sub for SSE consumers
│
├── src/voice_sprite/                 # sprite companion process (separate from daemon)
│   ├── __init__.py
│   ├── __main__.py               # entry point: SSE client thread + pyglet event loop
│   ├── charsheet.py              # TOML parser + PNG bounds validator
│   ├── chat_log.py               # ring-buffered HUD entries with hold+fade lifecycle
│   ├── chat_log_renderer.py      # pyglet label pool rendering fading chat entries
│   ├── config.py                 # sprite-side config loader
│   ├── cursor_tracker.py         # CursorDock: snaps window to cursor's monitor work area
│   ├── dpi.py                    # per-monitor DPI queries via shcore.dll
│   ├── event_client.py           # httpx-sse consumer with auto-reconnect
│   ├── llm_summary_client.py     # HTTP client for LM Studio one-shot summarization
│   ├── plan_outcome_handler.py   # handle_plan_outcome: parses plan_outcome SSE event → ChatLog entry
│   ├── speech_bubble.py          # fading label overlay
│   ├── sprite_renderer.py        # frame selection + animation timing
│   ├── state_machine.py          # 11-state FSM + heartbeat timeout
│   ├── summarizer.py             # hybrid rule-table + LLM-fallback HUD text generator
│   ├── summary_rules.py          # per-verb summary rules and chain detectors
│   ├── win32_flags.py            # WS_EX_LAYERED | WS_EX_TRANSPARENT | etc.
│   └── window.py                 # pyglet Window subclass
│
├── tests/
│   ├── unit/                       # one file per src module, mocked hardware
│   ├── integration/                # canned-WAV end-to-end, real model
│   ├── soak/                       # overnight stability runs
│   └── fixtures/                   # audio WAVs + sidecar TOML fixtures
│
├── scripts/                        # one-off utilities (device listing, config patching, smoke tests)
├── assets/sprite/                  # sprite character sheet assets
│   ├── README.md                  # art regeneration prompt template
│   └── charsheet.toml             # state→row mapping for charsheet.png
├── assets/sounds/                  # miss.wav (played) + start/stop.wav (unused per ADR 0014)
└── outputs/                        # rolling debug WAVs (gitignored)
```

## Invariants

- **Every tool module has a sibling `.toml`** with the same basename. The discovery pairing test enforces this.
- **Every module under `src/voice_commander/` has a test file** at `tests/unit/test_<module>.py` (subsystems excluded from coverage are listed in `pyproject.toml` → `[tool.coverage.run] omit`).
- **Every new ADR bumps the next sequence number.** Do not renumber existing ADRs.
- **`config.toml` is the single source of truth.** No `config.local.toml` overlay. Per-machine overrides go via `VC_LLM_*` env vars (for `[llm]` fields) or direct edits to the tracked file.
