# Testing Strategy

**Project:** Voice Commander
**Last updated:** 2026-04-19
**Status:** Authoritative — update this document whenever test structure changes.

---

## Contents

1. [Test Pyramid Overview](#1-test-pyramid-overview)
2. [Subsystem Unit Test Map](#2-subsystem-unit-test-map)
3. [Phase Human-Validation Checklists](#3-phase-human-validation-checklists)
4. [Adding a New Tool Test](#4-adding-a-new-tool-test)
5. [Running Tests](#5-running-tests)

---

## 1. Test Pyramid Overview

Voice Commander uses a four-layer pyramid. Each layer has a distinct scope, speed profile, and trigger.

```
          ┌──────────────────────────┐
          │     SOAK (24 h run)      │  Layer 4 — Phase 5 gate only
          ├──────────────────────────┤
          │  Hardware-in-the-Loop    │  Layer 3 — human-driven, per-phase gate
          │  (manual checklists)     │
          ├──────────────────────────┤
          │  Integration             │  Layer 2 — canned WAVs, no mic/hotkey
          │  (WAV → tool call)       │
          ├──────────────────────────┤
          │  Unit                    │  Layer 1 — fast, no hardware, CI-safe
          │  (per-subsystem, mocked) │
          └──────────────────────────┘
```

### Layer 1 — Unit tests

- **Location:** `tests/unit/`
- **Markers:** *(no marker — runs by default)*
- **Speed:** milliseconds per test; whole suite under 30 s.
- **Principle:** every subsystem is independently testable. Hardware dependencies (`pynput`, `sounddevice`, `faster-whisper`, `winsound`) are mocked or replaced with fakes. No GPU required.
- **Triggered by:** every `uv run pytest` invocation; CI on every push.

### Layer 2 — Integration tests (canned WAVs)

- **Location:** `tests/integration/`
- **Markers:** `@pytest.mark.integration`
- **Speed:** seconds; model must be preloaded.
- **Principle:** end-to-end pipeline from WAV file to tool function call, bypassing `HotkeyController` and `Recorder`. Each MVP command has a fixture WAV recorded once and checked in. Tests assert the correct tool function was called and no exceptions were raised.
- **Fixtures:** `tests/fixtures/audio/phrase_coverage/<tool>_<n>.wav` (see §4 for naming convention).
- **Triggered by:** `uv run pytest` (includes integration); skip with `-m "not integration"` for fast local iteration.

### Layer 3 — Hardware-in-the-loop (manual)

- **Location:** Per-phase checklists in §3 below.
- **Markers:** `@pytest.mark.hardware` (automated sub-steps only; manual steps are checkbox lists).
- **Principle:** a human sits at the machine with a microphone, runs `uv run voice-commander`, and follows the checklist step by step. Validates real audio capture, CUDA transcription, audio chimes, and clipboard side effects that cannot be simulated.
- **Triggered by:** phase gate — human signs off before the next phase begins.

### Layer 4 — Soak test

- **Location:** `tests/soak/` (Phase 5 deliverable).
- **Markers:** `@pytest.mark.soak`
- **Principle:** run the daemon for 24 continuous hours with a scripted phrase generator piping canned WAVs through the worker queue every 30 s. Assert: RSS memory growth ≤ 100 MB, no unhandled exceptions logged, log file well-formed throughout.
- **Triggered by:** Phase 5 gate only. Not part of the standard `pytest` run.

---

## 2. Subsystem Unit Test Map

| Subsystem | Unit test file | What it covers | Test fixtures / fakes used |
|---|---|---|---|
| `HotkeyController` | `tests/unit/test_hotkey.py` | `KEY_ALIASES` resolution, unknown key raises `ValueError`, toggle callback invoked on release; `hardware` marker for real Scroll Lock tap | `pynput.keyboard.Controller` (real, hardware-marked); otherwise pure unit with lambda callbacks |
| `Recorder` | `tests/unit/test_recorder.py` | WAV written to fixed path `recorded.wav` with correct sample rate / channels / bit depth, second record/stop cycle overwrites the same file (only one WAV in output dir), `stop()` before `start()` raises `RuntimeError` | `sounddevice` stream callback injected with synthetic PCM frames (`numpy.zeros`); `tmp_path` fixture for output dir |
| `Transcriber` | `tests/unit/test_transcriber.py` | `TranscriptionResult` fields populated, `language == "en"`, `confidence` clamped to `[0, 1]`, empty audio returns low confidence; `hardware` marker for real CUDA transcription | Fixture WAVs: `tests/fixtures/audio/hello_world.wav`, `tests/fixtures/audio/copy.wav`, `tests/fixtures/audio/silence.wav`; model mocked in non-hardware tests |
| `ToolRegistry` | `tests/unit/test_registry.py` | `@tool` decorator registers entry, `by_name()` / `all()` / `flat_phrases()` return correct data, duplicate name raises `DuplicateToolError`, phrases normalized (lowercase, stripped punctuation, collapsed whitespace) | In-memory registry reset via `reset_global_registry()` in `setup_function()`; no external deps |
| `Resolver` | `tests/unit/test_resolver.py` | Utterance dispatched to `LLMRouter` returns correct tool and plan, LLM miss returns `tool=None`, error from LLM falls back gracefully, response JSON parsed correctly | Stub `LLMRouter` returning canned JSON responses; no real HTTP calls |
| `Dispatcher` | `tests/unit/test_dispatcher.py` | Match above threshold → `on_match` + tool function called, match below threshold → `on_miss` called, tool function raises → `on_error` called + exception swallowed | `CapturingFeedbackSink` records all calls; tool functions are bare `MagicMock()` instances |
| `FeedbackSink` | `tests/unit/test_feedback.py` | `NullFeedbackSink` methods callable without error, `CapturingFeedbackSink` records calls in order. `WindowsFeedbackSink` is pure `winsound` + logger side-effects — covered via the `CapturingFeedbackSink` pattern in `Dispatcher` tests and through the Phase-3 GATE (human hears the chimes). | No hardware-marked `FeedbackSink` test needed after ADR 0013 (toasts removed) |
| `Config` | `tests/unit/test_config.py` | Round-trip: write TOML → `Config.load()` → values match, defaults applied when keys absent, invalid types raise `ConfigError`, `Config` is frozen (mutation raises `FrozenInstanceError`) | `tmp_path` fixture for temp `config.toml`; no external deps |
| `Daemon` | `tests/unit/test_daemon.py` | `Daemon.shutdown()` sets shutdown event and worker drains; all subsystem constructors called with values from `Config`; worker thread restarted once on death then exits | All subsystems mocked with `MagicMock`; `threading.Event` used to control shutdown timing |

---

## 3. Phase Human-Validation Checklists

These checklists are the acceptance criteria that a human must verify before unlocking the next phase. Check each box only after personally confirming the behaviour on real hardware.

---

### Phase 0 — Scaffolding and Docs

**Gate task:** `VC-P0-GATE`

- [ ] `uv sync --all-groups` exits 0 with no errors.
- [ ] `uv run pytest` exits 0 (only `Config` unit tests exist at this point).
- [ ] All 11 ADRs present in `docs/decisions/` and each follows the standard template (Context, Decision, Consequences, Alternatives).
- [ ] Reference docs present in `docs/references/` for each live dependency (faster-whisper, sounddevice, pynput, pyautogui, pystray, uv, winsound); spot-check at least two for accuracy. `windows_toasts.md` was removed in ADR 0013. `rapidfuzz.md` was removed when the Matcher was replaced by the LLM-based Resolver (ADR 0039).
- [ ] `docs/index.md` present and reviewed — table of contents is accurate.
- [ ] `docs/architecture.md` present and reviewed — diagrams and subsystem descriptions match spec §2.
- [ ] `docs/gotchas.md` present and reviewed — covers Scroll Lock LED, CUDA DLL paths, PortAudio device drift, pynput callback threading, and the historical §10 on WinRT-eager-import corrupting CUDA init (kept as a cautionary case study).
- [ ] `docs/libraries.md` present and reviewed — long-form rationale for every dependency.
- [ ] `docs/testing-strategy.md` (this file) present and reviewed — phase checklists accurate, pytest commands correct.
- [ ] `CLAUDE.md` present at project root and reviewed.
- [ ] `README.md` present and reviewed.
- [ ] `config.toml` present; all keys from spec §5 present with correct defaults.
- [ ] `src/voice_commander/config.py` implements `Config.load()` matching spec §5; `Config` is a frozen dataclass tree.
- [ ] Directory layout matches spec §2.4 exactly (run `ls -R` and cross-check).
- [ ] Human marks Phase 0 complete in Kaizen OS (`VC-P0-GATE` subquest → done).

---

### Phase 1 — Hotkey and Audio Capture

**Gate task:** `VC-P1-GATE`

Run the Phase 1 daemon:

```bash
uv run voice-commander
```

- [ ] Daemon prints `Phase1Daemon running. Press Ctrl+C to exit.`
- [ ] Press Scroll Lock. **Start chime** (rising tone) plays immediately.
- [ ] Speak: "hello voice commander, this is a test" (approximately 3 s).
- [ ] Press Scroll Lock. **Stop chime** (lower tone) plays.
- [ ] A WAV file appears at `outputs/recorded.wav` within 1 s of stopping.
- [ ] Open the WAV in any audio player — speech is clearly audible, sounds like 16 kHz mono (not stereo, not distorted).
- [ ] Repeat the record-and-stop cycle 3 more times. Verify that `outputs/` still contains exactly one file (`recorded.wav`) — each recording overwrites the previous one.
- [ ] Ctrl+C exits the daemon cleanly (no traceback, process terminates within 3 s).
- [ ] `uv run pytest -m "not hardware"` exits 0 — `HotkeyController`, `Recorder`, and `FeedbackSink` unit tests all pass.
- [ ] Human marks Phase 1 complete in Kaizen OS (`VC-P1-GATE` subquest → done).

---

### Phase 2 — Transcription

**Gate task:** `VC-P2-GATE`

Run the Phase 2 daemon:

```bash
uv run voice-commander
```

- [ ] Daemon logs `Loading faster-whisper model=small.en device=cuda ...` then `Model loaded` within 30 s (first-run download to `~/.cache/huggingface` is acceptable once).
- [ ] Press Scroll Lock. Start chime plays.
- [ ] Say "hello world, testing the transcriber" clearly.
- [ ] Press Scroll Lock. Stop chime plays.
- [ ] Within approximately 1 s of the stop chime, `voice-commander.log` shows a line like `transcript (conf=0.XX): hello world testing the transcriber`.
- [ ] Repeat with 3 varied utterances (e.g., "what is my name", "open a new tab", "take a screenshot") — transcripts are accurate and appear in the log.
- [ ] Measure latency between stop-chime and transcript log line for each utterance — all are ≤ 1.5 s.
- [ ] `uv run pytest -m "not hardware"` exits 0 — `Transcriber` unit tests pass (with mocked model).
- [ ] `uv run pytest -m hardware` exits 0 — real CUDA transcription tests pass.
- [ ] Human marks Phase 2 complete in Kaizen OS (`VC-P2-GATE` subquest → done).

---

### Phase 3 — Router, Registry, and First Tool

**Gate task:** `VC-P3-GATE`

Preparation: open Notepad, type and select the text "hello world".

Run the Phase 3 daemon:

```bash
uv run voice-commander
```

- [ ] Daemon reaches `Model loaded` without errors.
- [ ] Press Scroll Lock. Say "copy". Press Scroll Lock.
- [ ] Within approximately 1.5 s of the stop press:
  - [ ] Stop chime plays.
  - [ ] Log line appears: `MATCH copy <- 'copy'` (LLM resolved the tool).
  - [ ] Paste elsewhere (Ctrl+V in a text editor) — the text "hello world" appears.
- [ ] Press Scroll Lock. Say "xyzzy nonsense". Press Scroll Lock.
  - [ ] Miss beep plays.
  - [ ] Log line appears: `MISS 'xyzzy nonsense'` (LLM returned no tool).
- [ ] Ctrl+C exits the daemon cleanly.
- [ ] `uv run pytest -m "not hardware"` exits 0 — `ToolRegistry`, `Resolver`, `Dispatcher`, `FeedbackSink` unit tests all pass.
- [ ] Integration test `tests/integration/test_copy_e2e.py` passes: WAV fixture → `copy` tool called.
- [ ] Human marks Phase 3 complete in Kaizen OS (`VC-P3-GATE` subquest → done).

---

### Phase 4 — Full MVP Toolset

**Gate task:** `VC-P4-GATE`

- [ ] `uv run pytest -m "not hardware" -v` exits 0 — all unit and integration tests pass.
- [ ] `uv run pytest -m hardware -v` exits 0 — all hardware-marked tests pass.
- [ ] Live run-through (`uv run voice-commander`) — say each of the 14 MVP commands in sequence:
  - [ ] `copy` — selected text copied to clipboard.
  - [ ] `paste` — clipboard content pasted at cursor.
  - [ ] `cut` — selected text cut to clipboard.
  - [ ] `select all` — all content in focused control selected.
  - [ ] `undo` — last action reversed.
  - [ ] `redo` — undone action reapplied.
  - [ ] `new tab` — new browser/terminal tab opened.
  - [ ] `close tab` — current tab closed.
  - [ ] `switch tab` (or `next tab`) — focus moves to next tab.
  - [ ] `zoom in` — page or view zoomed in.
  - [ ] `zoom out` — page or view zoomed out.
  - [ ] `scroll down` — view scrolls down.
  - [ ] `scroll up` — view scrolls up.
  - [ ] `take screenshot` — screenshot saved or clipboard populated.
- [ ] For each command above, the `voice-commander.log` line `MATCH {tool} <- '{phrase}'` appeared (LLM resolved the tool) and the corresponding side-effect fired (e.g. clipboard change).
- [ ] Say three nonsense utterances — each produces a miss beep and a `MISS` log line.
- [ ] Log file contains no `on_error` entries from that session.
- [ ] Miss rate on the 20-utterance check (14 commands + 6 natural variations) is ≤ 5 % (at most 1 miss).
- [ ] Human marks Phase 4 complete in Kaizen OS (`VC-P4-GATE` subquest → done).

---

### Phase 5 — Hardening

**Gate task:** `VC-P5-GATE`

- [ ] `uv run pytest -m "not hardware" --cov=voice_commander` — all tests pass; coverage report shows ≥ 80 % for `voice_commander` package.
- [ ] `uv run pytest -m hardware -v` — all hardware tests pass.
- [ ] System tray icon visible in the Windows system tray after `uv run voice-commander`.
  - [ ] Icon colour changes: grey (idle) → red (recording) → yellow (thinking) → grey (idle).
  - [ ] Right-click menu has at minimum: "Open output folder", "Quit".
  - [ ] "Quit" exits the daemon cleanly.
  - [ ] "Open output folder" opens the `outputs/` directory in Windows Explorer.
- [ ] Second-instance launch exits immediately with a human-readable lock message (e.g., `Voice Commander is already running.`).
- [ ] Verify log rotation at 5 MB: grow `voice-commander.log` artificially or wait for natural growth; confirm rotated file appears (`voice-commander.log.1`) and main log is trimmed.
- [ ] `config.toml` tweaks take effect after restart:
  - [ ] Change `[llm] model` to a non-existent model name → daemon logs a connection/model error and misses gracefully (no crash).
  - [ ] Change `[hotkey] key` to `"pause"` → Scroll Lock no longer triggers; Pause key does.
  - [ ] (Removed — `toast_enabled` no longer exists; see ADR 0013.)
- [ ] Soak test passes:
  - [ ] Run `uv run pytest -m soak` (or the soak harness directly) for 24 h.
  - [ ] RSS memory at end − RSS at start ≤ 100 MB.
  - [ ] Log file contains no `on_error` or unhandled exception lines.
  - [ ] Log file is well-formed JSON lines throughout (no truncated entries).
- [ ] Human marks Phase 5 complete in Kaizen OS (`VC-P5-GATE` subquest → done).
- [ ] **MVP SHIP.**

---

## 4. Adding a New Tool Test

Follow this procedure every time a new `@tool`-decorated function is added to `src/voice_commander/tools/`.

### 4.1 Fixture WAV naming convention

Fixture WAVs for phrase coverage live at:

```
tests/fixtures/audio/phrase_coverage/<tool_name>_<n>.wav
```

- `<tool_name>` — the function name as registered in `@tool(phrases=[...])`, e.g. `copy`, `new_tab`.
- `<n>` — 1-based index, one file per representative phrase variation, e.g. `copy_1.wav`, `copy_2.wav`.

Record WAVs at **16 kHz, mono, 16-bit PCM** — the exact format Whisper expects. Use the Phase-1 daemon's `outputs/` directory as a convenient recorder, then copy and rename the files.

Minimum: one WAV per registered phrase. Recommended: also include one natural-language variation per phrase (e.g., `"please copy that"` alongside `"copy"`).

### 4.2 Unit test template

Create `tests/unit/test_tools_<module>.py` (one file per tool module, e.g., `test_tools_clipboard.py`):

```python
"""Unit tests for voice_commander.tools.<module>."""
from unittest.mock import patch

import pytest

import voice_commander.tools.<module> as <module>


# ---------------------------------------------------------------------------
# <ToolName> tests
# ---------------------------------------------------------------------------

def test_<tool_name>_sends_expected_keys():
    """<tool_name>() sends the expected pyautogui hotkey combination."""
    with patch("voice_commander.tools.<module>.pyautogui.hotkey") as mock_hk:
        <module>.<tool_name>()
    mock_hk.assert_called_once_with("<key1>", "<key2>")


def test_<tool_name>_is_registered():
    """@tool decorator registers <tool_name> with expected phrases."""
    from voice_commander.registry import get_global_registry
    entry = get_global_registry().by_name("<tool_name>")
    assert entry is not None, "<tool_name> not registered"
    assert "<primary phrase>" in entry.phrases
```

### 4.3 Integration test template

Add to (or create) `tests/integration/test_<module>_e2e.py`:

```python
"""Integration tests for <module> tools: WAV → tool call, no mic/hotkey."""
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from voice_commander.transcriber import Transcriber
from voice_commander.registry import discover
from voice_commander.resolver import Resolver
from voice_commander.dispatcher import Dispatcher
from voice_commander.feedback import CapturingFeedbackSink

FIXTURES = Path("tests/fixtures/audio/phrase_coverage")


@pytest.fixture(scope="module")
def pipeline():
    """Build a complete pipeline from real registry + real model."""
    registry = discover()
    transcriber = Transcriber(model_size="small.en", device="cuda", compute_type="float16")
    transcriber.load()
    feedback = CapturingFeedbackSink()
    resolver = Resolver(registry=registry)
    dispatcher = Dispatcher(feedback=feedback)
    return transcriber, resolver, dispatcher, feedback


@pytest.mark.integration
def test_<tool_name>_wav_triggers_tool(pipeline):
    """<tool_name>_1.wav transcribes and dispatches to <tool_name> tool."""
    transcriber, resolver, dispatcher, feedback = pipeline
    wav = FIXTURES / "<tool_name>_1.wav"
    assert wav.exists(), f"Fixture WAV not found: {wav}"

    with patch("voice_commander.tools.<module>.<tool_name>") as mock_fn:
        result = transcriber.transcribe(wav)
        match = resolver.resolve(result.text)
        dispatcher.dispatch(result.text, match)

    assert match.tool is not None, f"No match for transcript: {result.text!r}"
    assert match.tool.name == "<tool_name>"
    mock_fn.assert_called_once()
```

### 4.4 Checklist for a new tool

- [ ] `@tool(phrases=[...])` decorator added to the function in `src/voice_commander/tools/<module>.py`.
- [ ] Phrases are natural English; at least two variations registered.
- [ ] Unit test added following the template above.
- [ ] Fixture WAVs recorded and saved to `tests/fixtures/audio/phrase_coverage/`.
- [ ] Integration test added following the template above.
- [ ] `uv run pytest tests/unit/test_tools_<module>.py -v` passes.
- [ ] `uv run pytest tests/integration/test_<module>_e2e.py -v` passes.
- [ ] Phrase added to the MVP phrase list in `docs/architecture.md`.

---

## 5. Running Tests

### Full suite (unit + integration)

```bash
uv run pytest
```

Runs all tests except `hardware` and `soak` markers. This is the standard CI command.

### Fast suite (unit only, no hardware)

```bash
uv run pytest -m "not hardware"
```

Skips any test marked `@pytest.mark.hardware`. Use this during rapid iteration when you do not have the GPU / mic available or want sub-30 s feedback.

### With coverage report

```bash
uv run pytest --cov=voice_commander
```

Generates a terminal coverage report for the `voice_commander` package. Phase 5 gate requires ≥ 80 % coverage. To also produce an HTML report:

```bash
uv run pytest --cov=voice_commander --cov-report=html
```

Output lands in `htmlcov/index.html`.

### Hardware tests only

```bash
uv run pytest -m hardware
```

Requires: real GPU with CUDA, microphone present. Run at phase gates.

### Integration tests only

```bash
uv run pytest -m integration
```

Requires: CUDA (real model loaded). Fixture WAVs must be present in `tests/fixtures/audio/phrase_coverage/`.

### Soak test

```bash
uv run pytest -m soak
```

Runs for 24 h. Only run at the Phase 5 gate. Do not run in CI.

### Verbose output with short traceback

```bash
uv run pytest -v --tb=short
```

### Run a single test file

```bash
uv run pytest tests/unit/test_resolver.py -v
```

### pytest.ini markers (declared in `pyproject.toml`)

```toml
[tool.pytest.ini_options]
markers = [
    "hardware: requires real GPU, microphone, and display",
    "integration: end-to-end pipeline using canned WAV fixtures",
    "soak: 24-hour endurance run; do not run in CI",
]
```

These must be declared to avoid `PytestUnknownMarkWarning`.
