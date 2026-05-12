# Contributing to Voice Commander

Welcome! Voice Commander is a Windows-only, local-first voice command launcher — speak a command like "open Spotify" or "close window" and the daemon fires it. No cloud, no memorised shortcuts, no third-party speech service. Contributions are welcome whether you are adding a new primitive, fixing a bug, improving the Builder UI, or writing docs. Read this file first, then head to `CLAUDE.md` for the full architectural picture.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Windows 11 | Native-only: WASAPI audio, `winrt` OCR, `winsound`, `pynput` hotkeys |
| Python 3.11+ | 3.12 also tested |
| [`uv`](https://docs.astral.sh/uv/) | Fast Python package manager — install once, used everywhere |
| Node.js 20+ & [`pnpm`](https://pnpm.io/) | Required for the Builder React SPA |
| CUDA 12.x GPU (optional) | `faster-whisper` runs on CPU but is 3-5× slower without it |

---

## Setup

```powershell
git clone https://github.com/thecodeartificerX/voice-commander.git
cd voice-commander
uv sync
cd web/builder-ui
pnpm install
pnpm build
cd ..\..
Copy-Item config.toml.example config.toml
# Edit config.toml to set your audio device name
# Run: uv run python -c "import sounddevice; print(sounddevice.query_devices())"
# to list device names available on your system.
```

The Builder UI build outputs to `src/voice_commander/web/static/builder/`. You only need to rebuild it when you change files under `web/builder-ui/`. Without the build, `/page/builder` shows a friendly stub page.

---

## Running the daemon

```powershell
.\start.ps1
```

Press **Scroll Lock** to open a voice session, speak, and press Scroll Lock again to end it. The web UI is at `http://127.0.0.1:8765`. Logs are written to `voice-commander.log`.

---

## Running tests

```powershell
uv run pytest tests/
```

All tests are offline — no live audio device, GPU, or network required. If you add a test that needs real hardware, mark it `@pytest.mark.hardware` and document the skip condition. See [`docs/testing-strategy.md`](docs/testing-strategy.md) for the four-layer test pyramid.

---

## Project conventions

### Read these first

- **`CLAUDE.md`** — single source of truth for architecture, current state, and agent rules. Read before touching code.
- **[`docs/architecture.md`](docs/architecture.md)** — subsystem contracts, type stubs, thread topology, and data-flow diagrams.
- **[`docs/agents/technical-decisions.md`](docs/agents/technical-decisions.md)** — one-line summary of every locked technical decision.

### ADRs (Architecture Decision Records)

Every architectural choice — a new library, a routing change, a new wire protocol — gets an ADR in [`docs/decisions/`](docs/decisions/) at the time of the decision, not retroactively. File it as `docs/decisions/NNNN-short-title.md` (increment from the latest) and add a summary row to `docs/agents/technical-decisions.md`. A PR that introduces an architectural change without an ADR will be asked to add one before merge.

### Commit style

This project uses [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(router): add synonym fuzzy-match threshold config
fix(audio): handle PortAudio buffer underrun on resume
perf(transcriber): reuse model across sessions
refactor(dispatcher): extract run_plan into its own module
docs(adr): 0083 — remove legacy step-based workflow format
chore(deps): bump faster-whisper to 1.1.0
```

Scope is the subsystem name (`router`, `audio`, `builder`, `dispatcher`, `daemon`, etc.). Keep subject lines under 72 characters.

### Code style

- Formatter: `ruff format` (configured in `pyproject.toml`)
- Linter: `ruff check`
- Type hints required on all public function signatures
- No hard-coded paths — never commit `C:\Users\yourname\...`
- No personal data — check your diffs before pushing

---

## Filing bugs

Use the [Bug Report issue template](.github/ISSUE_TEMPLATE/bug_report.yml). Before filing:

1. Check existing issues — it may already be tracked.
2. Reproduce after a clean daemon restart.
3. Collect the relevant lines from `voice-commander.log`.
4. Note your OS build, Python version, GPU model, and the git SHA (`git rev-parse --short HEAD`).

A report without reproduction steps will be closed as `needs-info`.

---

## Submitting pull requests

1. **Fork** the repo and create a branch from `main`:
   ```powershell
   git checkout -b feat/your-feature-name
   ```
2. **One feature or fix per PR.** If you are fixing two unrelated things, open two PRs.
3. **Write tests.** New behaviour without tests will not be merged.
4. **File an ADR** if your change makes an architectural decision. See above.
5. **Run the full suite locally** before pushing:
   ```powershell
   uv run pytest tests/
   ```
6. **Open the PR against `main`** and fill out the pull request template — every checkbox matters.
7. Link the relevant issue with `Closes #N` in the PR description.

A maintainer will review within a few days. If a PR sits for more than two weeks with no response, ping in the linked issue.

---

## License

By contributing you agree that your changes will be made available under the [MIT License](LICENSE) that covers this project.

---

## Code of conduct

Please read and follow the [Code of Conduct](CODE_OF_CONDUCT.md). Be direct, be kind, be technical.
