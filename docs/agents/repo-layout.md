# Repo Layout

One-line description per directory and key file. Lazy-referenced from `CLAUDE.md`.

```
voice-commander/
├── pyproject.toml                  # uv-managed dependencies + tool config
├── uv.lock                         # committed lockfile
├── config.toml                     # tracked runtime defaults
├── config.local.toml               # machine-local overrides (gitignored)
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
│   ├── dispatcher.py               # runs matched tool, reports to feedback sink
│   ├── feedback.py                 # Windows / Null / Capturing feedback sinks
│   ├── registry.py                 # @tool decorator + auto-discovery
│   ├── tool_metadata.py            # sidecar TOML read/write + per-tool file locking
│   ├── config.py                   # config loader + deep-merge + validation
│   ├── single_instance.py          # one-daemon guard
│   ├── tools/                      # @tool groups (auto-discovered at import)
│   │   ├── _win32.py               # Windows-specific helpers (focus_window_by_exe, etc.)
│   │   ├── clipboard.py + .toml
│   │   ├── window.py    + .toml
│   │   ├── browser.py   + .toml
│   │   ├── system.py    + .toml
│   │   └── mouse.py     + .toml
│   └── web/                        # embedded FastAPI management UI
│       ├── app.py                  # routes
│       ├── server.py               # uvicorn daemon thread
│       ├── static/                 # htmx + tailwind bundles, app.css
│       └── templates/              # Jinja2 templates (HTMX fragments)
│
├── tests/
│   ├── unit/                       # one file per src module, mocked hardware
│   ├── integration/                # canned-WAV end-to-end, real model
│   ├── soak/                       # overnight stability runs
│   └── fixtures/                   # audio WAVs + sidecar TOML fixtures
│
├── scripts/                        # one-off utilities (device listing, config patching)
├── assets/sounds/                  # miss.wav (played) + start/stop.wav (unused per ADR 0014)
└── outputs/                        # rolling debug WAVs (gitignored)
```

## Invariants

- **Every tool module has a sibling `.toml`** with the same basename. The discovery pairing test enforces this.
- **Every module under `src/voice_commander/` has a test file** at `tests/unit/test_<module>.py` (subsystems excluded from coverage are listed in `pyproject.toml` → `[tool.coverage.run] omit`).
- **Every new ADR bumps the next sequence number.** Do not renumber existing ADRs.
- **`config.local.toml` never enters git.** It is for per-machine values only.
