# Primitives-Only MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce the LLM- and voice-visible tool surface to 11 primitives (7 action + 4 perception) plus `no_match`, replace VerbRouter rules with primitive-only rules + a `tail_coerce` hook, and delete every legacy speak / mute-key / RemoteTranscriber / HUD-summarizer / disabled-tool branch.

**Architecture:** Surgical deletion across `tools/`, `daemon.py`, `config.py`, `verb_router.py`, `transcriber.py`, `voice_sprite/`, plus graph-store wipe and doc updates. Every utterance routes through `VerbRouter` by default and through `LLMRouter` only while Merlin mode is active. No structural rewrite — just narrow each subsystem to the primitives-only surface.

**Tech Stack:** Python 3.11+, FastAPI/HTMX (untouched), `pyautogui`, `pywin32`, `rapidfuzz` (parameter resolution only), `winsound`, `pyglet`, faster-whisper, LM Studio over HTTP. No new dependencies.

---

## File Structure

Files modified by this plan (no new files outside docs/decisions):

| Path | Responsibility |
|---|---|
| `src/voice_commander/tools/primitives.py` | Strip to 7 action primitives + `no_match`. Delete `close`, `close_window`, `minimize`, `maximize`, `last`, `summon_commander`, `mute`, `done`, `ask_user`, helpers `_show_window`, `_close_with_verify`, `_set_mute_callback`, `_mute_callback`, `_COMMANDER_*`, `_CLOSE_VERIFY_*`. |
| `src/voice_commander/tools/primitives.toml` | Delete sidecar entries for every removed verb. |
| `src/voice_commander/tools/perception.py` | Strip to `read_clipboard`, `get_active_window_title`, `get_cursor_pos`. Delete `get_focused_window`, `list_windows`, `get_clipboard`, `list_processes`, `_get_process_name`. |
| `src/voice_commander/tools/perception.toml` | Delete sidecar entries for the removed perception tools. |
| `src/voice_commander/tools/_system.py` | **Deleted.** |
| `src/voice_commander/tools/_system.toml` | **Deleted.** |
| `src/voice_commander/daemon.py` | Drop speak-mode field/method, mute-key parameter, mute-callback wiring, RemoteTranscriber branch. Keep Merlin path. |
| `src/voice_commander/__main__.py` | Drop `mute_key=` argument when calling `run()`. Drop `cfg.transcription.backend == "remote"` branch in `_log_environment`. |
| `src/voice_commander/config.py` | Delete `HotkeyConfig.mute_key`, `TranscriptionConfig.backend / remote_endpoint_url / remote_timeout_ms`, the entire `SpeakConfig` dataclass + `[speak]` plumbing. Trim `_USER_EDITABLE_SECTIONS["transcription"]` to drop the remote keys. |
| `src/voice_commander/transcriber.py` | Delete `RemoteTranscriber`, `_to_wav_bytes`, `_parse_verbose_json`, `_empty_result`, and the `httpx`/`io`/`soundfile` imports they require. Keep the `TranscriberProtocol`. |
| `src/voice_commander/verb_router.py` | Add `tail_coerce` field on `VerbRule`; replace `build_default_rules()` with primitive-only rule set. |
| `src/voice_commander/validator.py` | Drop the `[rule_c2] speak.fuzzy_threshold` config rule. |
| `src/voice_commander/prompt_template.txt` | Audit — already references only primitives; no edits unless new dead names appear. |
| `config.toml` | Delete `[hotkey].mute_key`, `[transcription].backend / remote_endpoint_url / remote_timeout_ms`, the `[speak]` block. |
| `commands.json`, `workflows.json` | Already empty graph stores (`{"schema_version": 1, "graphs": {}}`) — verify only. |
| `commands.default.json`, `workflows.default.json` | Already-empty `commands.default.json` verified; `workflows.default.json` still seeds `search_web` — wipe to empty graphs. |
| `src/voice_sprite/__main__.py` | Drop summarizer wiring; render raw `tool_fired.name` and `"no match"` for `plan_outcome.status == "miss"` directly into `ChatLog`. |
| `src/voice_sprite/summarizer.py` | **Deleted.** |
| `src/voice_sprite/summary_rules.py` | **Deleted.** |
| `src/voice_sprite/plan_outcome_handler.py` | Replace summarizer-based handler with a thin `plan_outcome` → `"no match"` chat-log push (only on `status="miss"`). Keep `handle_ask_user` removed (no producers remain). |
| `start.ps1` | No code change needed (no `mute_key` references). Verify only. |
| `README.md` | Drop Right-Ctrl mute hotkey + `mute_key` config row. Note Scroll Lock as the sole hotkey. |
| `CLAUDE.md` | Update "Current state" paragraph to reflect 11-primitive catalog + Merlin-deferred LLM path. |
| `docs/architecture.md` | Shrink tool list table; delete RemoteTranscriber subsection; drop `mute_key` from `run()` signature; drop disabled-perception references. |
| `docs/agents/technical-decisions.md` | Append row for ADR 0075. Mark ADRs 0025, 0072, 0073 superseded inline. |
| `docs/decisions/0075-primitives-only-mvp.md` | **New.** Records this design. |
| `tests/unit/test_tools_primitives.py` | Delete tests for `close`, `close_window`, `last`, `summon_commander`, `mute`. Keep tests for `wait`, `press`, `type`, `focus`, `open`, `click`, `scroll`, `no_match`. Update `test_imports_expose_expected_symbols` to the new symbol list. |
| `tests/unit/test_perception.py` | Delete tests for `get_focused_window`, `list_windows`, `get_clipboard`, `list_processes`. Keep tests for `read_clipboard`, `get_active_window_title`, `get_cursor_pos`. |
| `tests/unit/test_tools_system.py` | **Deleted.** |
| `tests/unit/test_summarizer.py` | **Deleted.** |
| `tests/unit/test_summary_rules.py` | **Deleted.** |
| `tests/unit/test_sprite_plan_outcome_handler.py` | Rewrite for the trimmed handler (plan_outcome → "no match" only). |
| `tests/unit/test_streaming_daemon.py` | Delete every `test_*speak*` and `test_*mute*` case; drop the `speak_fuzzy_threshold` kwarg from helper builders. |
| `tests/unit/test_verb_router.py` | Replace with the new primitive-only rule coverage (click/scroll/wait/press/type/focus/open + tail_coerce). |
| `tests/unit/test_config.py` | Drop `mute_key`, `[speak]`, `transcription.backend / remote_*` cases. |
| `tests/unit/test_transcriber.py` | Drop every `RemoteTranscriber` test. |
| `tests/integration/test_speak_end_to_end.py` | **Deleted.** |
| `tests/integration/test_session_lifecycle.py` | Delete `test_speak_mode_lifecycle`, `test_scroll_lock_from_speak_mode_*`. |
| `tests/integration/test_hotkey_toggles_session.py` | Delete `test_speak_toggle_*`; drop `mute_key` arg from helpers. |
| `tests/integration/test_daemon_factory.py` | Drop `mute_key` / RemoteTranscriber assertions. |
| `tests/integration/test_merlin_router_integration.py` | Audit — keeps Merlin happy path; delete any speak-mode coupling. |
| `scripts/set-transcription-backend.py` | **Deleted** (only flips the about-to-be-removed `transcription.backend` field). |
| `scripts/benchmark-transcription.py`, `scripts/latency-test.py` | These are local-only scratch scripts (untracked). No edits required by this plan. |

---

## Phase 1 — Tool surface shrink

### Task 1.1: Trim `tools/primitives.py` to 7 action primitives + `no_match`

**Files:**
- Modify: `src/voice_commander/tools/primitives.py`

- [ ] **Step 1: Delete legacy verb functions and their helpers**

Open `src/voice_commander/tools/primitives.py`. Delete these top-level definitions (and the `# ---` banner comments that introduce them):
- `_set_mute_callback`, `_mute_callback`
- `_COMMANDER_CWD`, `_COMMANDER_CMD`
- `_CLOSE_VERIFY_TIMEOUT_MS`, `_CLOSE_VERIFY_POLL_INTERVAL_MS`
- `close`, `close_window`, `minimize`, `maximize`, `_show_window`, `_close_with_verify`
- `last`
- `summon_commander`
- `mute`
- `done`
- `ask_user`

Drop now-unused imports: `Callable`, `Path`. Keep `cast`, `Any`, `re`, `os`, `time`, `logging`, `pyautogui`, `..resolver`, `..registry.tool`, and the trimmed `_win32` import (`FocusWindowError`, `_do_focus`, `_verify_foreground`).

After this step, the only `@tool`-decorated symbols in the file must be: `focus`, `type_text` (registered as `type`), `open_target` (registered as `open`), `press`, `wait`, `click`, `scroll`, `no_match`.

- [ ] **Step 2: Update the module docstring**

Replace the existing docstring with one that lists the surviving primitives (no mention of `close`, `close_window`, `mute`, `done`, `ask_user`, `summon_commander`, `last`):

```python
"""LLM-only primitive verbs — the 7-tool catalog the LLM/VerbRouter dispatch.

This module is the sole source of LLM-visible action primitives. Every verb
here is ``llm_only = true`` with ``phrases = []``. Two verbs shadow Python
builtins — ``type`` and ``open``. Their Python symbols are ``type_text`` and
``open_target``; the registry exposes them under the short LLM-visible names
via ``@tool(name=...)``.

Surviving verbs: ``focus``, ``type``, ``open``, ``press``, ``wait``,
``click``, ``scroll``, plus the LLM escape hatch ``no_match``.
"""
```

- [ ] **Step 3: Run primitives unit tests to confirm import-clean**

Run: `uv run python -m pytest tests/unit/test_tools_primitives.py -x --no-header -q 2>&1 | head -40`

Expected: existing tests for the deleted verbs FAIL with `ImportError`. We will fix this in Task 5.1.

- [ ] **Step 4: Commit**

```bash
git add src/voice_commander/tools/primitives.py
git commit -m "feat(tools): strip primitives.py to 7 action verbs + no_match"
```

### Task 1.2: Trim `tools/primitives.toml`

**Files:**
- Modify: `src/voice_commander/tools/primitives.toml`

- [ ] **Step 1: Delete legacy sidecar blocks**

Open `src/voice_commander/tools/primitives.toml`. Delete these tables (entire `[tools.X]` block plus any `[tools.X.args.*]` / `[tools.X.returns.*]` children):
`[tools.close]`, `[tools.close_window]`, `[tools.minimize]`, `[tools.maximize]`, `[tools.last]`, `[tools.summon_commander]`, `[tools.mute]`, `[tools.done]`, `[tools.ask_user]`, and the explanatory `# --- Legacy semantic verbs (disabled — superseded by user commands) -----------` banner.

Keep: `[tools.focus]` (+ `args.target`, `returns.hwnd`), `[tools.type]` (+ `args.text`), `[tools.open]` (+ `args.target`, `returns.hwnd`), `[tools.press]` (+ `args.combo`), `[tools.wait]` (+ `args.ms`), `[tools.click]` (+ `args.button`), `[tools.scroll]` (+ `args.direction`, `args.amount`), `[tools.no_match]` (+ `args.reason`).

- [ ] **Step 2: Update the file header comment**

Replace the multi-line header comment with:

```toml
category = "primitives"

# Primitive verbs dispatch to the OS (keystrokes, window focus, app launch).
# Most are marked ``internal = true`` so the LLM never sees them directly —
# user-defined commands and workflows in commands.json / workflows.json are
# the primary surface the LLM routes against.
#
# Exceptions kept LLM-visible (internal=false):
#   - ``press``: raw keystroke escape hatch for bare key-name utterances
#     ("control T", "scroll lock", "enter") that no command covers.
#   - ``no_match``: the "nothing matched" escape hatch. No-op Python function.
```

- [ ] **Step 3: Run validator to confirm sig↔TOML alignment**

Run: `uv run voice-commander --validate 2>&1 | tail -5`

Expected: `OK`. If it fails, the failure points to a sig/TOML mismatch — fix the matching block.

- [ ] **Step 4: Commit**

```bash
git add src/voice_commander/tools/primitives.toml
git commit -m "feat(tools): drop legacy primitive entries from primitives.toml"
```

### Task 1.3: Trim `tools/perception.py` to 3 perception primitives

**Files:**
- Modify: `src/voice_commander/tools/perception.py`

- [ ] **Step 1: Delete agentic-loop perception verbs**

Open `src/voice_commander/tools/perception.py`. Delete `_get_process_name`, `get_focused_window`, `list_windows`, `get_clipboard`, `list_processes`. Keep `read_clipboard`, `get_active_window_title`, `get_cursor_pos`.

- [ ] **Step 2: Update the module docstring**

Replace the docstring with:

```python
"""Perception tools — observation primitives for graph branch nodes.

Three side-effect-free verbs that let GraphRuntime branch nodes observe the
desktop without focusing windows or sending keystrokes. All three are
``llm_only = true`` with ``phrases = []``.
"""
```

- [ ] **Step 3: Run perception unit tests to confirm import-clean for survivors**

Run: `uv run python -m pytest tests/unit/test_perception.py::test_read_clipboard_returns_text -x --no-header -q 2>&1 | tail -10`

Expected: any tests still importing the deleted symbols FAIL with `ImportError` (this is the signal Task 5.2 will clean up).

- [ ] **Step 4: Commit**

```bash
git add src/voice_commander/tools/perception.py
git commit -m "feat(tools): strip perception.py to 3 observation primitives"
```

### Task 1.4: Trim `tools/perception.toml`

**Files:**
- Modify: `src/voice_commander/tools/perception.toml`

- [ ] **Step 1: Delete legacy sidecar blocks**

Open `src/voice_commander/tools/perception.toml`. Delete `[tools.get_focused_window]`, `[tools.list_windows]`, `[tools.get_clipboard]`, `[tools.list_processes]`.

Keep `[tools.read_clipboard]` (+ `returns.text`), `[tools.get_active_window_title]` (+ `returns.title`), `[tools.get_cursor_pos]` (+ `returns.x`, `returns.y`).

- [ ] **Step 2: Update the file header comment**

Replace the existing header comment with:

```toml
category = "perception"

# Perception tools are side-effect-free observers used as branch inputs in
# user-authored graphs. The LLM router rarely calls them in normal mode; they
# come into play when a graph node consumes their return value.
```

- [ ] **Step 3: Run validator to confirm**

Run: `uv run voice-commander --validate 2>&1 | tail -5`

Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add src/voice_commander/tools/perception.toml
git commit -m "feat(tools): drop legacy perception entries from perception.toml"
```

### Task 1.5: Delete `_system` tool pair

**Files:**
- Delete: `src/voice_commander/tools/_system.py`
- Delete: `src/voice_commander/tools/_system.toml`

- [ ] **Step 1: Remove the files**

```bash
git rm src/voice_commander/tools/_system.py src/voice_commander/tools/_system.toml
```

- [ ] **Step 2: Verify registry no longer discovers `speak`**

Run: `uv run python -c "from voice_commander.registry import discover; from voice_commander.tool_metadata import ToolMetadataStore; from pathlib import Path; r = discover('voice_commander.tools', store=ToolMetadataStore(Path('src/voice_commander/tools'))); print('speak in registry:', r.by_name('speak') is not None)"`

Expected: `speak in registry: False`.

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(tools): remove _system.py + _system.toml (drops speak verb)"
```

---

## Phase 2 — Daemon strip

### Task 2.1: Drop speak-mode plumbing from `StreamingDaemon`

**Files:**
- Modify: `src/voice_commander/daemon.py`

- [ ] **Step 1: Delete speak-related imports and fields**

Open `src/voice_commander/daemon.py`. Make these edits:

1. Remove the `import rapidfuzz.fuzz` line at the top of the file.
2. Remove `from .tools import primitives as tool_primitives` (no longer needed once the mute-callback wiring is gone in step 4).
3. In the `Transcriber` import block at the top, change

   ```python
   from .transcriber import (
       RemoteTranscriber,
       Transcriber,
       TranscriberProtocol,
       TranscriptionResult,
   )
   ```
   to
   ```python
   from .transcriber import (
       Transcriber,
       TranscriptionResult,
   )
   ```
   (`TranscriberProtocol` is no longer needed because `Transcriber` is the only impl — remove it from the import. Keep `TranscriptionResult` and `Transcriber`.)

4. Update the `StreamingDaemon.__init__` signature: change the `transcriber: TranscriberProtocol` annotation to `transcriber: Transcriber`. Remove the `speak_fuzzy_threshold: int = 95` parameter (and its argument doc line if present). Remove `self._speak_fuzzy_threshold = speak_fuzzy_threshold` and `self._speak_mode: bool = False`.

5. In the docstring for `__init__`, drop the bullet `_session_active / _speak_mode — boolean state flags ...` and replace with `_session_active — boolean state flag (mutated only on the pynput hotkey-listener thread, also reset by :meth:`shutdown` during teardown).`

- [ ] **Step 2: Delete `on_speak_toggle` method entirely**

Remove the entire `def on_speak_toggle(self) -> None:` method (including its docstring) from `StreamingDaemon`.

- [ ] **Step 3: Strip speak-mode branches from `on_scroll_lock`**

Replace the body of `on_scroll_lock` with this version (preserving the existing recorder-None guard at the top and the publish/log lines at the bottom):

```python
def on_scroll_lock(self) -> None:
    """Toggle the voice session on/off.

    Thread context: called exclusively on the **pynput hotkey-listener thread**.
    Must not block — delegates all heavy work to other threads via queues.
    pynput serialises key callbacks so concurrent invocations cannot happen.

    State transitions:

    * **Open → close**: calls ``recorder.close_session()``, drains ``_utt_q``,
      sets ``_session_active = False``, publishes ``session_stopped``.
    * **Closed → open**: calls ``recorder.open_session()`` (spawns VAD worker),
      sets ``_session_active = True``, publishes ``session_started``.

    Guard: no-op (with a warning log) if ``_recorder`` is ``None`` — i.e. the
    daemon was constructed but the recorder has not yet been wired in.
    """
    if self._recorder is None:
        logger.warning("on_scroll_lock called but recorder is not yet initialised; ignoring")
        return
    if self._session_active:
        self._recorder.close_session()
        self._drain_utt_q()
        self._session_active = False
        self._merlin_mode = False
        self._feedback.on_recording_stop()
        self._publish("session_stopped")
        logger.info("Session closed")
    else:
        try:
            self._recorder.open_session()
            self._session_active = True
            self._merlin_mode = False
            self._feedback.on_recording_start()
            self._publish("session_started")
            logger.info("Session opened")
        except Exception as e:
            self._session_active = False
            self._merlin_mode = False
            self._feedback.on_error("recorder.open_session", e)
```

- [ ] **Step 4: Strip speak-mode gate from `_process_utterance`**

In `_process_utterance`, delete the entire "Speak-mode gate" block — i.e. the `if self._speak_mode:` clause and its body (lines covering the `text_norm` parsing, `rapidfuzz.fuzz.ratio` call, `speak_entry` lookup, fallback warning, and the trailing `else: logger.debug(...) ; return`). Also delete the docstring bullet `3. **speak-mode gate** — ...` so the gate-chain enumeration stays accurate.

- [ ] **Step 5: Drop `mute_key` from `run()`**

Change the signature of `run` from `def run(self, hotkey_key: str, mute_key: str = "") -> None:` to `def run(self, hotkey_key: str) -> None:`. Remove the `mute_key:` doc paragraph. Replace the bindings construction:

```python
bindings: dict[str, Callable[[], None]] = {hotkey_key: self.on_scroll_lock}
if mute_key:
    bindings[mute_key] = self.on_speak_toggle
self._hotkey = HotkeyController(bindings)
```
with:

```python
self._hotkey = HotkeyController({hotkey_key: self.on_scroll_lock})
```

- [ ] **Step 6: Drop `_speak_mode` resets in `shutdown()`**

In `shutdown()`, delete the line `self._speak_mode = False` after `self._session_active = False`.

- [ ] **Step 7: Drop the RemoteTranscriber branch and speak/mute-callback wiring in `build_streaming_daemon`**

In `build_streaming_daemon`:

1. Replace the transcription-backend block

   ```python
   transcriber: TranscriberProtocol
   if cfg.transcription.backend == "remote":
       transcriber = RemoteTranscriber(...)
       logger.info("Transcription backend: remote @ %s ...", ...)
   else:
       transcriber = Transcriber(...)
       logger.info("Transcription backend: local ...")
   ```
   with:
   ```python
   transcriber = Transcriber(
       model_size=cfg.transcription.model_size,
       device=cfg.transcription.device,
       compute_type=cfg.transcription.compute_type,
   )
   logger.info(
       "Transcription backend: local (model=%s device=%s)",
       cfg.transcription.model_size,
       cfg.transcription.device,
   )
   ```

2. In the `StreamingDaemon(...)` constructor call, drop the `speak_fuzzy_threshold=cfg.speak.fuzzy_threshold,` keyword argument.

3. Delete the trailing block

   ```python
   # Wire the mute() primitive to the daemon's scroll-lock handler so a
   # voice-driven "mute" utterance ends the session exactly like pressing
   # the Scroll Lock hotkey.
   tool_primitives._set_mute_callback(daemon.on_scroll_lock)
   ```

- [ ] **Step 8: Run daemon-wiring unit test**

Run: `uv run python -m pytest tests/unit/test_daemon_wiring.py -x --no-header -q 2>&1 | tail -20`

Expected: passes (or fails only with assertions about `mute_key` / `RemoteTranscriber` / `_speak_mode` — those are fixed in Phase 5).

- [ ] **Step 9: Commit**

```bash
git add src/voice_commander/daemon.py
git commit -m "feat(daemon): drop speak-mode, mute-key, RemoteTranscriber branches"
```

### Task 2.2: Drop `mute_key` from CLI entry point

**Files:**
- Modify: `src/voice_commander/__main__.py`

- [ ] **Step 1: Edit the daemon launch line**

Change:
```python
build_streaming_daemon(cfg).run(cfg.hotkey.key, mute_key=cfg.hotkey.mute_key)
```
to:
```python
build_streaming_daemon(cfg).run(cfg.hotkey.key)
```

- [ ] **Step 2: Drop the remote-backend short-circuit in `_log_environment`**

Delete the entire

```python
if cfg.transcription.backend == "remote":
    logger.info(
        "transcription backend=remote endpoint=%s — skipping local-Whisper diagnostics",
        cfg.transcription.remote_endpoint_url,
    )
    return
```

block from `_log_environment`. The function now always probes ctranslate2 / faster_whisper.

- [ ] **Step 3: Commit**

```bash
git add src/voice_commander/__main__.py
git commit -m "feat(cli): drop mute_key + remote-backend logging from __main__"
```

### Task 2.3: Trim `Config` and config writer

**Files:**
- Modify: `src/voice_commander/config.py`

- [ ] **Step 1: Delete `mute_key` from `HotkeyConfig`**

Replace the `HotkeyConfig` dataclass with:

```python
@dataclass(frozen=True)
class HotkeyConfig:
    key: str = "scroll_lock"
```

- [ ] **Step 2: Delete remote-backend fields from `TranscriptionConfig`**

Replace the dataclass with:

```python
@dataclass(frozen=True)
class TranscriptionConfig:
    model_size: str = "small.en"
    device: str = "cuda"
    compute_type: str = "float16"
    min_confidence: float = 0.30
```

(Remove `backend`, `remote_endpoint_url`, `remote_timeout_ms`, and the entire `__post_init__` validator.)

- [ ] **Step 3: Delete the `SpeakConfig` dataclass and its `Config` field**

Remove the entire `SpeakConfig` dataclass definition. In `Config`, delete the `speak: SpeakConfig = field(default_factory=SpeakConfig)` field.

In `Config.load()`, delete the `speak=_section(SpeakConfig, raw.get("speak", {}))` keyword argument from the constructor call.

- [ ] **Step 4: Trim `_USER_EDITABLE_SECTIONS`**

Replace the `transcription` set in `_USER_EDITABLE_SECTIONS` with:

```python
"transcription": {
    "model_size",
    "device",
    "compute_type",
    "min_confidence",
},
```

- [ ] **Step 5: Run config unit test**

Run: `uv run python -m pytest tests/unit/test_config.py -x --no-header -q 2>&1 | tail -20`

Expected: speak/mute_key/backend tests fail (cleanup in Phase 5). All other tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/voice_commander/config.py
git commit -m "feat(config): drop mute_key, remote-backend fields, [speak] block"
```

### Task 2.4: Drop `RemoteTranscriber` from `transcriber.py`

**Files:**
- Modify: `src/voice_commander/transcriber.py`

- [ ] **Step 1: Delete `RemoteTranscriber` and helpers**

Open `src/voice_commander/transcriber.py`. Delete:
- the `RemoteTranscriber` class (entire body),
- `_to_wav_bytes`,
- `_parse_verbose_json`,
- `_empty_result`.

- [ ] **Step 2: Drop unused imports**

Remove these lines from the imports section: `import io`, `import httpx`, `import soundfile`. Keep `numpy as np`, `numpy.typing as npt`, `math`, `logging`, `Path`, `dataclass`.

- [ ] **Step 3: Simplify the `TranscriberProtocol`**

Since `Transcriber` is now the only implementation, drop the `TranscriberProtocol` declaration and the `from typing import Protocol` import. (Daemon imports already adjusted to `Transcriber` in Task 2.1 Step 1.) Audit `tests/integration/test_streaming_pipeline.py` for any `TranscriberProtocol` reference and replace with `Transcriber`.

Run: `uv run python -c "import voice_commander.transcriber as t; print(hasattr(t, 'RemoteTranscriber'), hasattr(t, 'TranscriberProtocol'))"`

Expected: `False False`.

- [ ] **Step 4: Run transcriber tests**

Run: `uv run python -m pytest tests/unit/test_transcriber.py -x --no-header -q 2>&1 | tail -10`

Expected: tests for the local backend pass; remote-backend tests fail with `ImportError` (cleanup in Phase 5).

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/transcriber.py
git commit -m "feat(transcriber): drop RemoteTranscriber + TranscriberProtocol"
```

### Task 2.5: Wipe top-level `config.toml`

**Files:**
- Modify: `config.toml`

- [ ] **Step 1: Edit `config.toml`**

Open `config.toml` at the repo root. Delete:
- The `mute_key = "ctrl_r"` line under `[hotkey]`.
- The `backend = "local" ...` comment line, the `remote_endpoint_url = ...` line, and the `remote_timeout_ms = 5000` line under `[transcription]`.
- Any `[speak]` block (currently absent in the live file but verify after the edit).

The `[hotkey]` section ends up as:
```toml
[hotkey]
key = "scroll_lock"
```

The `[transcription]` section ends up as:
```toml
[transcription]
model_size = "small.en"
device = "cuda"
compute_type = "float16"
min_confidence = 0.3
```

- [ ] **Step 2: Confirm config loads**

Run: `uv run voice-commander --validate 2>&1 | tail -5`

Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add config.toml
git commit -m "chore(config): drop mute_key, remote-backend, [speak] from config.toml"
```

### Task 2.6: Remove the now-stale `set-transcription-backend.py` script

**Files:**
- Delete: `scripts/set-transcription-backend.py`

- [ ] **Step 1: Remove file**

```bash
git rm scripts/set-transcription-backend.py
```

- [ ] **Step 2: Drop start.ps1 references (if any)**

```bash
grep -n "set-transcription-backend" start.ps1 || echo "no references — start.ps1 already clean"
```

If the grep returns lines, delete each occurrence by hand and re-run the grep until it reports clean.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "chore(scripts): drop set-transcription-backend (backend toggle removed)"
```

### Task 2.7: Drop `[rule_c2]` from the config validator

**Files:**
- Modify: `src/voice_commander/validator.py`

- [ ] **Step 1: Delete the speak rule**

In `validate_config`, remove this block:

```python
# Rule C2: speak.fuzzy_threshold must be in [0, 100].
if not (0 <= cfg.speak.fuzzy_threshold <= 100):
    errors.append(
        f"[rule_c2] speak.fuzzy_threshold={cfg.speak.fuzzy_threshold} is outside "
        f"the valid range [0, 100]"
    )
```

- [ ] **Step 2: Run validator unit tests**

Run: `uv run python -m pytest tests/unit/test_validator.py -x --no-header -q 2>&1 | tail -10`

Expected: passes (any speak rule_c2 cases fail — fix in Phase 5).

- [ ] **Step 3: Commit**

```bash
git add src/voice_commander/validator.py
git commit -m "feat(validator): drop rule_c2 (speak.fuzzy_threshold gone)"
```

---

## Phase 3 — VerbRouter rewrite

### Task 3.1: Add `tail_coerce` to `VerbRule`

**Files:**
- Modify: `src/voice_commander/verb_router.py`

- [ ] **Step 1: Write the failing test for `tail_coerce`**

Open `tests/unit/test_verb_router.py`. Replace the entire file with:

```python
"""Unit tests for the primitives-only VerbRouter rule set."""

import pytest
from voice_commander.plan import Plan, ToolCall
from voice_commander.verb_router import VerbRouter, build_default_rules


def _router() -> VerbRouter:
    return VerbRouter(build_default_rules())


# ---------------------------------------------------------------------------
# click
# ---------------------------------------------------------------------------

def test_click_routes_to_click():
    plan = _router().route("click")
    assert plan is not None
    assert plan.steps == (ToolCall(name="click", kwargs={}),)


def test_click_tolerates_trailing_period():
    plan = _router().route("Click.")
    assert plan is not None
    assert plan.steps == (ToolCall(name="click", kwargs={}),)


# ---------------------------------------------------------------------------
# scroll
# ---------------------------------------------------------------------------

def test_scroll_default_down():
    plan = _router().route("scroll")
    assert plan is not None
    assert plan.steps == (ToolCall(name="scroll", kwargs={"direction": "down"}),)


def test_scroll_up_subcommand():
    plan = _router().route("scroll up")
    assert plan is not None
    assert plan.steps == (ToolCall(name="scroll", kwargs={"direction": "up"}),)


def test_scroll_down_subcommand():
    plan = _router().route("scroll down")
    assert plan is not None
    assert plan.steps == (ToolCall(name="scroll", kwargs={"direction": "down"}),)


# ---------------------------------------------------------------------------
# focus / open / type / press
# ---------------------------------------------------------------------------

def test_focus_with_tail():
    plan = _router().route("focus chrome")
    assert plan is not None
    assert plan.steps == (ToolCall(name="focus", kwargs={"target": "chrome"}),)


def test_open_with_tail():
    plan = _router().route("open notepad")
    assert plan is not None
    assert plan.steps == (ToolCall(name="open", kwargs={"target": "notepad"}),)


def test_type_with_tail():
    plan = _router().route("type hello world")
    assert plan is not None
    assert plan.steps == (ToolCall(name="type", kwargs={"text": "hello world"}),)


def test_press_with_tail():
    plan = _router().route("press control t")
    assert plan is not None
    assert plan.steps == (ToolCall(name="press", kwargs={"combo": "control t"}),)


def test_focus_without_tail_misses():
    assert _router().route("focus") is None


# ---------------------------------------------------------------------------
# wait — uses tail_coerce
# ---------------------------------------------------------------------------

def test_wait_numeric_tail_coerced_to_int():
    plan = _router().route("wait 500")
    assert plan is not None
    assert plan.steps == (ToolCall(name="wait", kwargs={"ms": 500}),)


def test_wait_unit_suffix_ms_stripped():
    plan = _router().route("wait 500 ms")
    assert plan is not None
    assert plan.steps == (ToolCall(name="wait", kwargs={"ms": 500}),)


def test_wait_unit_suffix_milliseconds_stripped():
    plan = _router().route("wait 500 milliseconds")
    assert plan is not None
    assert plan.steps == (ToolCall(name="wait", kwargs={"ms": 500}),)


def test_wait_without_tail_misses():
    assert _router().route("wait") is None


# ---------------------------------------------------------------------------
# fall-through
# ---------------------------------------------------------------------------

def test_unknown_verb_returns_none():
    assert _router().route("xyz") is None


def test_empty_returns_none():
    assert _router().route("") is None
    assert _router().route("   ") is None


def test_legacy_copy_no_longer_routes():
    """Semantic verbs are deleted — user must author them as graphs."""
    assert _router().route("copy") is None


def test_legacy_close_no_longer_routes():
    assert _router().route("close") is None


def test_legacy_minimize_no_longer_routes():
    assert _router().route("minimize") is None


def test_legacy_new_tab_no_longer_routes():
    assert _router().route("new tab") is None
```

- [ ] **Step 2: Run the new test file — confirm it fails**

Run: `uv run python -m pytest tests/unit/test_verb_router.py -x --no-header -q 2>&1 | tail -20`

Expected: FAIL — current `build_default_rules()` still returns the legacy copy/cut/etc. set, and `tail_coerce` does not exist.

- [ ] **Step 3: Implement `tail_coerce` in `VerbRule` + `route()`**

Replace the body of `src/voice_commander/verb_router.py` with:

```python
"""Deterministic first-word verb router for normal-mode voice commands."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .plan import Plan, ToolCall


@dataclass(frozen=True)
class RouteTarget:
    tool: str
    kwargs: dict[str, object]


@dataclass(frozen=True)
class SubcommandRule:
    aliases: tuple[str, ...]
    target: RouteTarget


@dataclass(frozen=True)
class VerbRule:
    name: str
    aliases: tuple[str, ...]
    default_target: RouteTarget | None = None
    subcommands: tuple[SubcommandRule, ...] = ()
    raw_tail_tool: str | None = None
    raw_tail_arg: str | None = None
    tail_coerce: Callable[[str], object] | None = None


class VerbRouter:
    def __init__(self, rules: tuple[VerbRule, ...]) -> None:
        self._rules = {rule.name: rule for rule in rules}
        self._alias_map: dict[str, str] = {}
        for rule in rules:
            for alias in rule.aliases:
                self._alias_map[alias] = rule.name

    def route(self, transcript: str) -> Plan | None:
        text = transcript.strip().rstrip(".,!?")
        if not text:
            return None
        head, _, tail = text.partition(" ")
        head = head.strip().lower()
        tail = tail.strip().rstrip(".,!?")

        verb_name = self._alias_map.get(head)
        if verb_name is None:
            return None
        verb = self._rules[verb_name]

        if not tail and verb.default_target is not None:
            return self._plan_for(verb.default_target, verb.name, tail)

        if tail:
            for sub in verb.subcommands:
                if tail.lower() in sub.aliases:
                    return self._plan_for(sub.target, verb.name, tail)

        if tail and verb.raw_tail_tool and verb.raw_tail_arg:
            arg_value: object = tail
            if verb.tail_coerce is not None:
                try:
                    arg_value = verb.tail_coerce(tail)
                except (ValueError, TypeError):
                    return None
            return Plan(
                steps=(ToolCall(name=verb.raw_tail_tool, kwargs={verb.raw_tail_arg: arg_value}),),
                raw_response={"router": "verb", "verb": verb.name, "tail": tail},
            )

        return None

    def _plan_for(self, target: RouteTarget, verb_name: str, tail: str) -> Plan:
        return Plan(
            steps=(ToolCall(name=target.tool, kwargs=dict(target.kwargs)),),
            raw_response={"router": "verb", "verb": verb_name, "tail": tail},
        )


def _coerce_wait_ms(tail: str) -> int:
    """Permissive int parser: '500' / '500 ms' / '500 milliseconds' → 500."""
    head_token = tail.split()[0]
    return int(head_token)


def build_default_rules() -> tuple[VerbRule, ...]:
    return (
        VerbRule("click", ("click",), default_target=RouteTarget("click", {})),
        VerbRule(
            "scroll",
            ("scroll",),
            default_target=RouteTarget("scroll", {"direction": "down"}),
            subcommands=(
                SubcommandRule(("up",), RouteTarget("scroll", {"direction": "up"})),
                SubcommandRule(("down",), RouteTarget("scroll", {"direction": "down"})),
            ),
        ),
        VerbRule("focus", ("focus",), raw_tail_tool="focus", raw_tail_arg="target"),
        VerbRule("open", ("open",), raw_tail_tool="open", raw_tail_arg="target"),
        VerbRule("type", ("type",), raw_tail_tool="type", raw_tail_arg="text"),
        VerbRule("press", ("press",), raw_tail_tool="press", raw_tail_arg="combo"),
        VerbRule(
            "wait",
            ("wait",),
            raw_tail_tool="wait",
            raw_tail_arg="ms",
            tail_coerce=_coerce_wait_ms,
        ),
    )
```

- [ ] **Step 4: Run the rewritten verb-router tests**

Run: `uv run python -m pytest tests/unit/test_verb_router.py -x --no-header -q 2>&1 | tail -10`

Expected: PASS (all 16 cases).

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/verb_router.py tests/unit/test_verb_router.py
git commit -m "feat(verb-router): primitives-only rules + tail_coerce hook"
```

---

## Phase 4 — Graph / workflow wipe

### Task 4.1: Verify `commands.json` and `workflows.json` are empty

**Files:**
- Modify (verify): `commands.json`, `workflows.json`

- [ ] **Step 1: Inspect both files**

Run:
```bash
cat commands.json
cat workflows.json
```

Expected output for both:
```json
{
  "schema_version": 1,
  "graphs": {}
}
```

If either contains graphs, decide manually whether to migrate them via the Builder UI before deleting. For the primitives-only MVP, the spec mandates `[]` (empty) — wipe and accept the breakage.

- [ ] **Step 2: If non-empty, wipe**

If `commands.json` or `workflows.json` had graphs, overwrite with the empty form:

```bash
printf '{\n  "schema_version": 1,\n  "graphs": {}\n}\n' > commands.json
printf '{\n  "schema_version": 1,\n  "graphs": {}\n}\n' > workflows.json
```

- [ ] **Step 3: Commit (only if step 2 ran)**

```bash
git add commands.json workflows.json
git commit -m "chore: wipe commands.json + workflows.json to empty graph store"
```

### Task 4.2: Wipe `workflows.default.json`

**Files:**
- Modify: `workflows.default.json`

- [ ] **Step 1: Replace with the empty graph store**

Overwrite `workflows.default.json` with:

```json
{
  "schema_version": 1,
  "graphs": {}
}
```

- [ ] **Step 2: Commit**

```bash
git add workflows.default.json
git commit -m "chore: drop search_web seed from workflows.default.json"
```

### Task 4.3: Verify `commands.default.json` is already empty

**Files:**
- Verify: `commands.default.json`

- [ ] **Step 1: Inspect**

Run: `cat commands.default.json`

Expected:
```json
{
  "schema_version": 1,
  "graphs": {}
}
```

If anything other than the empty form appears, overwrite identically to Task 4.2 and commit `chore: drop seed from commands.default.json`.

---

## Phase 5 — HUD summarizer strip + test cleanup

### Task 5.1: Trim `tests/unit/test_tools_primitives.py`

**Files:**
- Modify: `tests/unit/test_tools_primitives.py`

- [ ] **Step 1: Delete legacy-verb test cases**

Open `tests/unit/test_tools_primitives.py`. Delete every test for the removed verbs:
- `test_close_sends_ctrl_w`, `test_close_window_sends_alt_f4`, `test_close_self_verify_logs_when_fg_unchanged`
- `test_last_default_sends_alt_tab`, `test_last_tab_true_sends_ctrl_tab`, `test_last_default_verify_logs_when_fg_unchanged`, `test_last_signature`
- `test_summon_commander_spawns_powershell_in_repo`, `test_summon_commander_takes_no_args`
- `test_mute_invokes_injected_callback`, `test_mute_noop_when_callback_unset`

Update `test_imports_expose_expected_symbols` to use only the surviving symbols:

```python
def test_imports_expose_expected_symbols() -> None:
    from voice_commander.tools import primitives as _p

    for name in (
        "click",
        "focus",
        "no_match",
        "open_target",
        "press",
        "scroll",
        "type_text",
        "wait",
    ):
        assert hasattr(_p, name), f"primitives missing symbol {name!r}"
```

Also remove the import block at the top — drop `close`, `close_window`, `last`, `mute`, `summon_commander` from `from voice_commander.tools.primitives import (...)`.

- [ ] **Step 2: Run tests**

Run: `uv run python -m pytest tests/unit/test_tools_primitives.py -x --no-header -q 2>&1 | tail -10`

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_tools_primitives.py
git commit -m "test(primitives): drop tests for removed legacy verbs"
```

### Task 5.2: Trim `tests/unit/test_perception.py`

**Files:**
- Modify: `tests/unit/test_perception.py`

- [ ] **Step 1: Delete agentic-loop perception tests**

Open `tests/unit/test_perception.py`. Delete every test that targets the removed perception verbs (`get_focused_window`, `list_windows`, `get_clipboard`, `list_processes`). Keep the tests for `read_clipboard`, `get_active_window_title`, `get_cursor_pos`. Drop unused helpers (`_make_psutil`, `_make_win32process`, `_make_win32clipboard` if it covered only `get_clipboard`) — or keep them if they support surviving tests.

Update the `from voice_commander.tools.perception import (...)` import to only

```python
from voice_commander.tools.perception import (
    get_active_window_title,
    get_cursor_pos,
    read_clipboard,
)
```

- [ ] **Step 2: Run tests**

Run: `uv run python -m pytest tests/unit/test_perception.py -x --no-header -q 2>&1 | tail -10`

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_perception.py
git commit -m "test(perception): drop tests for removed agentic-loop verbs"
```

### Task 5.3: Delete `test_tools_system.py`

**Files:**
- Delete: `tests/unit/test_tools_system.py`

- [ ] **Step 1: Remove file**

```bash
git rm tests/unit/test_tools_system.py
```

- [ ] **Step 2: Commit**

```bash
git commit -m "test: drop test_tools_system (speak verb gone)"
```

### Task 5.4: Trim `tests/unit/test_streaming_daemon.py`

**Files:**
- Modify: `tests/unit/test_streaming_daemon.py`

- [ ] **Step 1: Delete every speak/mute test case**

Open `tests/unit/test_streaming_daemon.py`. Delete:
- `test_scroll_lock_closes_from_active_speak_mode`
- `test_speak_toggle_noop_when_inactive`
- `test_speak_toggle_enters_speak_mode`
- `test_speak_toggle_exits_speak_mode`
- `test_speak_toggle_stream_stays_open`
- `test_pipeline_speak_branch_fuzzy_match_calls_speak_tool`
- `test_pipeline_speak_branch_non_match_drops_silently`
- `test_pipeline_speak_branch_multi_word_drops_silently`
- `test_pipeline_speak_branch_skips_confidence_gate`

Drop the `speak_fuzzy_threshold` keyword from any `StreamingDaemon(...)` test fixture or builder helper. Drop `mute_key=` from any test that calls `daemon.run(...)`.

- [ ] **Step 2: Run tests**

Run: `uv run python -m pytest tests/unit/test_streaming_daemon.py -x --no-header -q 2>&1 | tail -10`

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_streaming_daemon.py
git commit -m "test(daemon): drop speak/mute streaming-daemon tests"
```

### Task 5.5: Trim `tests/unit/test_config.py`

**Files:**
- Modify: `tests/unit/test_config.py`

- [ ] **Step 1: Delete tests for removed fields**

Delete:
- `test_mute_key_defaults_to_ctrl_r`
- `test_mute_key_loads_from_toml`
- `test_mute_key_invalid_type_raises`
- `test_speak_section_defaults`
- `test_speak_section_overrides`
- `test_speak_fuzzy_threshold_invalid_type_raises`

Search for any test referencing `transcription.backend` / `remote_endpoint_url` / `remote_timeout_ms`. Delete each such case (e.g. tests covering `__post_init__`-driven `ValueError` on `backend="remote"` with empty endpoint).

- [ ] **Step 2: Run tests**

Run: `uv run python -m pytest tests/unit/test_config.py -x --no-header -q 2>&1 | tail -10`

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_config.py
git commit -m "test(config): drop mute_key, [speak], remote-backend tests"
```

### Task 5.6: Trim `tests/unit/test_transcriber.py`

**Files:**
- Modify: `tests/unit/test_transcriber.py`

- [ ] **Step 1: Delete every RemoteTranscriber test**

Open the file. Delete every test class / function whose name starts with `test_remote_transcriber*`, `test_remote_*`, or that imports `RemoteTranscriber`. Drop the `from voice_commander.transcriber import RemoteTranscriber` line.

- [ ] **Step 2: Run tests**

Run: `uv run python -m pytest tests/unit/test_transcriber.py -x --no-header -q 2>&1 | tail -10`

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_transcriber.py
git commit -m "test(transcriber): drop RemoteTranscriber tests"
```

### Task 5.7: Delete `test_speak_end_to_end.py`

**Files:**
- Delete: `tests/integration/test_speak_end_to_end.py`

- [ ] **Step 1: Remove file**

```bash
git rm tests/integration/test_speak_end_to_end.py
```

- [ ] **Step 2: Commit**

```bash
git commit -m "test: drop test_speak_end_to_end (speak-mode removed)"
```

### Task 5.8: Trim `tests/integration/test_session_lifecycle.py`

**Files:**
- Modify: `tests/integration/test_session_lifecycle.py`

- [ ] **Step 1: Delete speak-mode test cases**

Delete:
- `test_speak_mode_lifecycle`
- `test_scroll_lock_from_speak_mode_synths_right_ctrl`
- `test_scroll_lock_from_speak_mode_warns_if_tool_not_found`
- `test_scroll_lock_from_speak_mode_logs_exception_if_tool_raises`

- [ ] **Step 2: Run tests**

Run: `uv run python -m pytest tests/integration/test_session_lifecycle.py -x --no-header -q 2>&1 | tail -10`

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_session_lifecycle.py
git commit -m "test(session): drop speak-mode lifecycle integration cases"
```

### Task 5.9: Trim `tests/integration/test_hotkey_toggles_session.py`

**Files:**
- Modify: `tests/integration/test_hotkey_toggles_session.py`

- [ ] **Step 1: Delete speak-toggle tests**

Delete:
- `test_speak_toggle_does_not_touch_recorder`
- `test_speak_toggle_noop_when_session_inactive`

Search for any helper builder calling `daemon.run(..., mute_key=...)` and drop the `mute_key=` argument.

- [ ] **Step 2: Run tests**

Run: `uv run python -m pytest tests/integration/test_hotkey_toggles_session.py -x --no-header -q 2>&1 | tail -10`

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_hotkey_toggles_session.py
git commit -m "test(hotkey): drop speak-toggle integration cases"
```

### Task 5.10: Trim `tests/integration/test_daemon_factory.py`

**Files:**
- Modify: `tests/integration/test_daemon_factory.py`

- [ ] **Step 1: Delete remote-backend / mute_key assertions**

Open the file. Drop any test or assertion that:
- references `RemoteTranscriber`,
- asserts behaviour for `cfg.transcription.backend == "remote"`,
- references `mute_key` or `cfg.hotkey.mute_key`,
- references `cfg.speak`.

If a test's only purpose was one of the above, delete it entirely.

- [ ] **Step 2: Run tests**

Run: `uv run python -m pytest tests/integration/test_daemon_factory.py -x --no-header -q 2>&1 | tail -10`

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_daemon_factory.py
git commit -m "test(daemon-factory): drop mute_key + remote-backend assertions"
```

### Task 5.11: Trim Merlin integration test

**Files:**
- Modify: `tests/integration/test_merlin_router_integration.py`

- [ ] **Step 1: Audit and trim**

Open the file. Search for any reference to `_speak_mode`, `mute_key`, `SpeakConfig`, or `tool_primitives._set_mute_callback`. Delete each line. The Merlin happy-path test stays.

- [ ] **Step 2: Run tests**

Run: `uv run python -m pytest tests/integration/test_merlin_router_integration.py -x --no-header -q 2>&1 | tail -10`

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_merlin_router_integration.py
git commit -m "test(merlin): drop speak/mute coupling"
```

### Task 5.12: Delete sprite summarizer + summary_rules

**Files:**
- Delete: `src/voice_sprite/summarizer.py`
- Delete: `src/voice_sprite/summary_rules.py`
- Delete: `tests/unit/test_summarizer.py`
- Delete: `tests/unit/test_summary_rules.py`

- [ ] **Step 1: Remove the four files**

```bash
git rm src/voice_sprite/summarizer.py src/voice_sprite/summary_rules.py tests/unit/test_summarizer.py tests/unit/test_summary_rules.py
```

- [ ] **Step 2: Commit**

```bash
git commit -m "feat(sprite): drop Summarizer + summary rules (raw tool name on HUD)"
```

### Task 5.13: Rewrite `plan_outcome_handler.py`

**Files:**
- Modify: `src/voice_sprite/plan_outcome_handler.py`

- [ ] **Step 1: Replace the file**

Overwrite `src/voice_sprite/plan_outcome_handler.py` with:

```python
"""plan_outcome and tool_fired SSE event handlers — primitives-only HUD.

The HUD now mirrors the live tool stream: each ``tool_fired`` event pushes the
raw tool name to the chat log; ``plan_outcome`` with ``status="miss"`` pushes
``"no match"``. Errors render as ``"<tool_name> failed"``. Successful runs are
already represented by the per-step ``tool_fired`` lines, so we suppress the
trailing ``ok`` outcome to avoid double-rendering.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from voice_commander.plan import PlanOutcome

from .chat_log import ChatLog, ChatLogEntry

logger = logging.getLogger(__name__)


def handle_plan_outcome(
    data: dict[str, Any],
    chat_log: ChatLog,
    now_provider: Callable[[], float] = time.monotonic,
) -> None:
    """Append a HUD line for a `plan_outcome` SSE payload.

    * ``status="miss"`` → ``"no match"``.
    * ``status="error"`` → ``"<failed_tool> failed"`` (or ``"command failed"``
      when no failed-step index is set).
    * ``status="ok"`` → no append (the per-step ``tool_fired`` events have
      already populated the HUD).
    """
    try:
        outcome = PlanOutcome.from_event_dict(data)
    except (KeyError, ValueError, TypeError, AttributeError):
        logger.exception("Failed to parse PlanOutcome — skipping HUD append")
        return

    if outcome.status == "ok":
        return

    if outcome.status == "miss":
        text = "no match"
    else:
        idx = outcome.failed_step_index
        if idx is not None and 0 <= idx < len(outcome.steps):
            text = f"{outcome.steps[idx].name} failed"
        else:
            text = "command failed"

    chat_log.append(
        ChatLogEntry(
            text=text,
            status=outcome.status,
            born_at_s=now_provider(),
        )
    )


def handle_tool_fired(
    data: dict[str, Any],
    chat_log: ChatLog,
    now_provider: Callable[[], float] = time.monotonic,
) -> None:
    """Append the raw tool name on every `tool_fired` SSE event."""
    name = data.get("name")
    if not name:
        return
    chat_log.append(
        ChatLogEntry(
            text=str(name),
            status="ok",
            born_at_s=now_provider(),
        )
    )
```

(Note: the `handle_ask_user` helper is removed — `ask_user` is gone.)

- [ ] **Step 2: Commit**

```bash
git add src/voice_sprite/plan_outcome_handler.py
git commit -m "feat(sprite): rewrite plan_outcome_handler for primitives-only HUD"
```

### Task 5.14: Wire the new handlers into the sprite event loop

**Files:**
- Modify: `src/voice_sprite/__main__.py`

- [ ] **Step 1: Drop summarizer wiring**

Open `src/voice_sprite/__main__.py`. Delete:
- `from .summarizer import Summarizer`
- `from .summary_rules import CHAIN_DETECTORS, RULES`
- `summarizer = Summarizer(rules=RULES, chain_detectors=CHAIN_DETECTORS)`

Replace `from .plan_outcome_handler import handle_ask_user, handle_plan_outcome` with `from .plan_outcome_handler import handle_plan_outcome, handle_tool_fired`.

- [ ] **Step 2: Update the SSE event handler**

Replace the body of `def on_event(...)` with:

```python
def on_event(event_type: str, data: dict[str, Any]) -> None:
    result = sm.on_event(event_type, data)
    if result is not None:
        renderer.set_state(result)
        logger.info("State → %s", result.value)
    window.set_muted(sm.muted)
    renderer.set_muted(sm.muted)
    if event_type == "tool_fired":
        handle_tool_fired(data, chat_log)
        if "name" in data:
            bubble.show(data["name"])
    elif event_type == "plan_outcome":
        handle_plan_outcome(data, chat_log)
```

(The previous `if event_type == "ask_user":` branch is removed entirely. The `bubble.show(...)` call moves inside the `tool_fired` branch.)

- [ ] **Step 3: Run sprite tests**

Run: `uv run python -m pytest tests/unit/test_sprite_main.py -x --no-header -q 2>&1 | tail -10`

Expected: PASS (any test that imports `handle_ask_user` or `Summarizer` fails — fix in Task 5.15).

- [ ] **Step 4: Commit**

```bash
git add src/voice_sprite/__main__.py
git commit -m "feat(sprite): consume tool_fired directly into HUD chat log"
```

### Task 5.15: Rewrite `test_sprite_plan_outcome_handler.py`

**Files:**
- Modify: `tests/unit/test_sprite_plan_outcome_handler.py`

- [ ] **Step 1: Replace test file**

Overwrite `tests/unit/test_sprite_plan_outcome_handler.py` with:

```python
"""Unit tests for the trimmed sprite plan_outcome / tool_fired handlers."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from voice_sprite.chat_log import ChatLog, ChatLogEntry
from voice_sprite.plan_outcome_handler import handle_plan_outcome, handle_tool_fired


@pytest.fixture()
def chat_log() -> ChatLog:
    return ChatLog(max_lines=5, hold_ms=4000, fade_ms=3000)


def _make_outcome(status: str, *, steps=(), failed_step_index=None) -> dict:
    return {
        "transcript": "anything",
        "steps": steps,
        "status": status,
        "failed_step_index": failed_step_index,
        "error_msg": None,
        "duration_ms": 12,
    }


def test_plan_outcome_miss_appends_no_match(chat_log: ChatLog) -> None:
    handle_plan_outcome(_make_outcome("miss"), chat_log, now_provider=lambda: 1.0)
    assert len(chat_log.entries) == 1
    assert chat_log.entries[0].text == "no match"
    assert chat_log.entries[0].status == "miss"


def test_plan_outcome_ok_appends_nothing(chat_log: ChatLog) -> None:
    handle_plan_outcome(_make_outcome("ok"), chat_log, now_provider=lambda: 1.0)
    assert len(chat_log.entries) == 0


def test_plan_outcome_error_with_index(chat_log: ChatLog) -> None:
    handle_plan_outcome(
        _make_outcome("error", steps=[{"name": "press", "kwargs": {"combo": "ctrl+c"}}], failed_step_index=0),
        chat_log,
        now_provider=lambda: 1.0,
    )
    assert chat_log.entries[0].text == "press failed"


def test_plan_outcome_error_no_index(chat_log: ChatLog) -> None:
    handle_plan_outcome(_make_outcome("error"), chat_log, now_provider=lambda: 1.0)
    assert chat_log.entries[0].text == "command failed"


def test_tool_fired_appends_raw_name(chat_log: ChatLog) -> None:
    handle_tool_fired({"name": "click"}, chat_log, now_provider=lambda: 1.0)
    assert chat_log.entries[0].text == "click"
    assert chat_log.entries[0].status == "ok"


def test_tool_fired_no_name_is_noop(chat_log: ChatLog) -> None:
    handle_tool_fired({}, chat_log, now_provider=lambda: 1.0)
    assert len(chat_log.entries) == 0
```

(If `ChatLog` does not expose an `entries` attribute, swap to whatever public iteration helper is available — e.g. `list(chat_log)` or `chat_log._entries`. Verify by reading `src/voice_sprite/chat_log.py`.)

- [ ] **Step 2: Verify the chat-log accessor name and adjust test**

Run: `grep -n "self\._entries\|self\.entries\|def entries\|def __iter__" src/voice_sprite/chat_log.py | head -10`

If the attribute differs, update the test to use the actual accessor.

- [ ] **Step 3: Run tests**

Run: `uv run python -m pytest tests/unit/test_sprite_plan_outcome_handler.py -x --no-header -q 2>&1 | tail -10`

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_sprite_plan_outcome_handler.py
git commit -m "test(sprite): rewrite plan_outcome_handler tests for trimmed handler"
```

### Task 5.16: Drop `test_ask_user_handler.py`

**Files:**
- Delete: `tests/unit/test_ask_user_handler.py`

- [ ] **Step 1: Confirm the file targets only the deleted handler**

Run: `head -30 tests/unit/test_ask_user_handler.py`

If every test imports `handle_ask_user`, delete the file. Otherwise, surgically delete only those tests.

- [ ] **Step 2: Remove or trim the file**

```bash
git rm tests/unit/test_ask_user_handler.py
```

- [ ] **Step 3: Commit**

```bash
git commit -m "test: drop test_ask_user_handler (ask_user verb removed)"
```

---

## Phase 6 — Full verification

### Task 6.1: Run the full test suite

**Files:** none (verification only)

- [ ] **Step 1: Run all unit tests**

Run: `uv run python -m pytest tests/unit -x --no-header -q 2>&1 | tail -30`

Expected: PASS. If any fail, the failure points to a stray reference to `RemoteTranscriber`, `mute_key`, `_speak_mode`, or a deleted verb. Fix the root cause (the production code is the source of truth at this point) and rerun.

- [ ] **Step 2: Run all integration tests**

Run: `uv run python -m pytest tests/integration -x --no-header -q 2>&1 | tail -30`

Expected: PASS.

- [ ] **Step 3: Run the validator CLI**

Run: `uv run voice-commander --validate 2>&1 | tail -5`

Expected: `OK`.

- [ ] **Step 4: Audit `prompt_template.txt` for references to deleted tools**

Run: `grep -niE "close|minimize|maximize|last|summon|mute|done|ask_user|list_windows|list_processes|get_focused_window|get_clipboard" src/voice_commander/prompt_template.txt || echo "clean"`

Expected: `clean`. If any match appears, hand-edit the template to drop the line; commit as `feat(prompt): drop references to removed tools`.

- [ ] **Step 5: Audit code for any straggling references to deleted symbols**

Run:
```bash
grep -REn "RemoteTranscriber|TranscriberProtocol|_speak_mode|on_speak_toggle|_mute_callback|tool_primitives\._set_mute_callback|SpeakConfig|cfg\.speak|cfg\.transcription\.backend|cfg\.transcription\.remote_endpoint_url|cfg\.transcription\.remote_timeout_ms|cfg\.hotkey\.mute_key" src tests scripts 2>/dev/null
```

Expected: empty output. If anything appears, surgically delete it.

- [ ] **Step 6: Manual smoke matrix (HUMAN-IN-THE-LOOP gate)**

Start the daemon: `.\start.ps1 -NoMenu` (or interactive equivalent).

Walk this matrix and tick each row only after observation:

| Utterance | Expected behaviour |
|---|---|
| "click" | Mouse left-click fires at cursor |
| "scroll up" | Window scrolls up |
| "scroll down" | Window scrolls down |
| "press control t" | Ctrl+T sent to focused app |
| "type hello world" | "hello world" typed into focused app |
| "open notepad" | Notepad launches |
| "focus chrome" | Chrome window comes to foreground |
| "wait 500" | Daemon log shows ~500 ms pause; no error |
| "merlin" | Daemon log: `Merlin mode entered`; sprite enters `llm_thinking` on next utterance |
| "merlin" again | Daemon log: `Merlin mode exited`; verb router takes over |
| "Click." | Same as "click" (punctuation tolerance) |
| "copy" | Miss chime; HUD shows `no match` |

- [ ] **Step 7: Builder UI smoke**

In a browser, open `http://127.0.0.1:8765/page/builder`. Confirm:
- The palette lists exactly 11 primitives (7 action: `focus`, `type`, `open`, `press`, `wait`, `click`, `scroll`; 4 perception: `read_clipboard`, `get_active_window_title`, `get_cursor_pos`, `ocr_region`). `no_match` may or may not render depending on the builder filter — either is fine.
- No legacy verbs (`close`, `close_window`, `minimize`, `maximize`, `last`, `summon_commander`, `mute`, `done`, `ask_user`, `speak`, `get_focused_window`, `list_windows`, `get_clipboard`, `list_processes`) appear.
- Drag any primitive into a fresh graph, save, hot-reload registers it.
- The Prompt Inspector panel still loads `prompt_template.txt` cleanly.

If anything fails, capture the failure and fix before completing the phase.

- [ ] **Step 8: Commit any straggler fixes**

If steps 1–7 produced fixes, commit each one with a `fix(...)` prefix. If clean, no commit needed.

---

## Phase 7 — Documentation

### Task 7.1: Author ADR 0075

**Files:**
- Create: `docs/decisions/0075-primitives-only-mvp.md`

- [ ] **Step 1: Write the ADR**

Create `docs/decisions/0075-primitives-only-mvp.md` with:

```markdown
# ADR 0075 — Primitives-only MVP

**Status:** Accepted
**Date:** 2026-05-08
**Supersedes:** ADR 0025 (mute hotkey), ADR 0072 (speak/dictation toggle), ADR 0073 (remote transcription backend) — and reduces ADR 0043 (nine-verb primitive catalog) to seven action verbs + four perception verbs.

## Context

The catalog and codebase carried a long tail of features that pre-dated the primitives-only MVP direction:
- 9 disabled-but-still-importable legacy verbs (`close`, `close_window`, `minimize`, `maximize`, `last`, `summon_commander`, `mute`, `done`, `ask_user`)
- 4 disabled perception tools (`get_focused_window`, `list_windows`, `get_clipboard`, `list_processes`)
- The speak / dictation toggle subsystem (Right-Ctrl mute key, `[speak]` config block, `_system.speak()` tool)
- Remote `whisper.cpp` transcription backend (unused)
- HUD `Summarizer` rule table (Merlin-deferred → effectively dead path)
- VerbRouter rules covering `copy`/`cut`/`paste`/`undo`/`redo`/`save`/`refresh`/`minimize`/`maximize`/`close`/`new` (semantic shortcuts that the user authors as graphs)

Symptom that surfaced the cleanup: utterance "Click." → MISS, because VerbRouter had no `click` rule.

## Decision

Reduce the LLM- and voice-visible tool surface to:

| Action (7) | Perception (4) |
|---|---|
| `click(button="left")` | `get_active_window_title()` |
| `focus(target)` | `get_cursor_pos()` |
| `open(target)` | `read_clipboard()` |
| `press(combo)` | `ocr_region(x, y, w, h)` |
| `scroll(direction, amount=3)` | |
| `type(text)` | |
| `wait(ms)` | |

Plus `no_match(reason)` — LLM-only escape hatch, hidden from VerbRouter.

VerbRouter rules cover only direct primitive invocation (`click`, `scroll`, `focus X`, `open X`, `type X`, `press X`, `wait N`). Anything semantic (`copy`, `minimize`, `new tab`) is authored by the user as a graph in the Builder UI, dispatching to `press(combo=...)`.

`tail_coerce` on `VerbRule` lets the `wait` rule accept `"500"`, `"500 ms"`, `"500 milliseconds"` and coerce to `int`.

Speak-mode, the Right-Ctrl mute hotkey, RemoteTranscriber, and the HUD summarizer's rule table are all deleted. The HUD now renders raw `tool_fired.name` values directly into the chat log; `plan_outcome` with `status="miss"` becomes the literal string `"no match"`.

## Consequences

- User commands referencing deleted tools fail loud at startup; user re-authors them in the Builder UI.
- Right-Ctrl muscle memory is broken — Scroll Lock is now the sole hotkey.
- The HUD loses pretty rule-driven phrasing (`copied`, `pasted`, `searched <browser> for "..."`); it gains transparency by rendering the raw tool name the dispatcher fired.
- Merlin-mode LLM routing remains intact for open-ended utterances; the small-MoE tool list shrinks accordingly, raising tool-call reliability.

## Alternatives considered

- Migrating user-authored graphs that depended on deleted tools — explicitly rejected ("boil the ocean"; user re-authors).
- Keeping the rule-table summarizer — rejected as dead weight given the absence of an LLM fallback.

## References

- Spec: `docs/superpowers/specs/2026-05-08-primitives-only-mvp-design.md`
- Plan: `docs/superpowers/plans/2026-05-08-primitives-only-mvp.md`
```

- [ ] **Step 2: Commit**

```bash
git add docs/decisions/0075-primitives-only-mvp.md
git commit -m "docs(adr): file ADR 0075 — primitives-only MVP"
```

### Task 7.2: Append the ADR row to `docs/agents/technical-decisions.md`

**Files:**
- Modify: `docs/agents/technical-decisions.md`

- [ ] **Step 1: Append the ADR-0075 row**

Insert this line at the bottom of the first decisions table (the one that ends with the `Process supervision` row, before the `## LLM Router` heading):

```markdown
| Primitives-only MVP | 7 action + 4 perception verbs; VerbRouter routes only primitive invocations; speak/RemoteTranscriber/Summarizer rule-table all removed | Minimal orthogonal catalog; user composes higher-level commands in the Builder; small-MoE tool count drops sharply | [0075](../decisions/0075-primitives-only-mvp.md) |
```

Add `*(superseded by ADR 0075)*` to the right-most cell of:
- the row whose ADR link is `[0025]` (Session model / mute hotkey),
- the row in the LLM Router table whose ADR link is `[0072]` (if present — search the file),
- the row whose ADR link is `[0073]` (Transcription backend toggle).

(If a row is in a sub-section table, mark it there.)

- [ ] **Step 2: Commit**

```bash
git add docs/agents/technical-decisions.md
git commit -m "docs(decisions): record ADR 0075 + supersede 0025/0072/0073"
```

### Task 7.3: Update `CLAUDE.md` "Current state" paragraph

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Rewrite the "Current state" paragraph**

In `CLAUDE.md`, replace the `**Current state.**` paragraph (in the "What we're building" section) with:

```markdown
**Current state.** VerbRouter is the daily routing path: every transcript runs through `VerbRouter.route()`, which matches the first word against a primitives-only rule set (`click`, `scroll`, `focus X`, `open X`, `type X`, `press X`, `wait N`) and emits a one-step plan. Saying "Merlin" mid-session flips into LLM mode; the next utterance routes through `LLMRouter.route()` against LM Studio with `tool_choice="required"`. Saying "Merlin" again reverts. There is no offline fuzzy-match fallback in normal mode — verb misses chime once. `rapidfuzz` survives only inside `resolver.resolve_window` / `resolve_app` for grounding `focus(target)` / `open(target)`. The system prompt is an external template (`prompt_template.txt`) with `{default_browser}` placeholder; editable and hot-reloadable via the web UI's Prompt Inspector panel (ADR 0056). The tool catalogue is exactly 11 primitives — 7 action (`focus`, `type`, `open`, `press`, `wait`, `click`, `scroll`) plus 4 perception (`get_active_window_title`, `get_cursor_pos`, `read_clipboard`, `ocr_region`) — plus the LLM-only `no_match(reason)` escape hatch (ADR 0075). An in-process `EventBus` broadcasts daemon state changes (session, tool_fired, miss, heartbeat, plan_outcome) via an SSE `/events` endpoint. A separate `voice_sprite` process consumes these events and renders an animated pixel-art companion (pyglet, click-through overlay) that mirrors daemon state. The HUD overlay renders raw `tool_fired.name` strings into a fading chat log (no rule-table summarizer); `plan_outcome` with `status="miss"` renders `"no match"`. `CursorDock` snaps the sprite window to the cursor's current monitor work area at 30 Hz, keeping the companion visible across multi-monitor desktops.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): refresh current-state paragraph for primitives-only MVP"
```

### Task 7.4: Update `docs/architecture.md`

**Files:**
- Modify: `docs/architecture.md`

- [ ] **Step 1: Drop `mute_key` from the documented `run()` signature**

Find the line `def run(self, hotkey_key: str, mute_key: str = "") -> None:` and replace with `def run(self, hotkey_key: str) -> None:`.

- [ ] **Step 2: Delete the `RemoteTranscriber` subsection**

Find section `### 4.3 \`Transcriber\` / \`RemoteTranscriber\``. Rename to `### 4.3 \`Transcriber\``. Delete the `class RemoteTranscriber:` block and the surrounding paragraph that describes the `cfg.transcription.backend` toggle. Keep the `Transcriber` description intact.

- [ ] **Step 3: Shrink the tool-list table**

Find the catalog table (search for `## Tools` or `### Tool catalog` — adjust if your local copy differs). Reduce it to the 11 primitives + `no_match`. Delete rows for `close`, `close_window`, `minimize`, `maximize`, `last`, `summon_commander`, `mute`, `done`, `ask_user`, `get_focused_window`, `list_windows`, `get_clipboard`, `list_processes`, `speak`.

- [ ] **Step 4: Drop agentic-loop perception references**

Find any paragraph mentioning the `agentic-loop` LLM path consuming `get_focused_window` / `list_windows` / `get_clipboard` / `list_processes`. Delete those paragraphs — perception now feeds graph branch nodes only.

- [ ] **Step 5: Commit**

```bash
git add docs/architecture.md
git commit -m "docs(arch): trim tool catalog, drop RemoteTranscriber + mute_key"
```

### Task 7.5: Update `README.md`

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Drop the mute hotkey advertisement**

Delete:
- The bullet on line ~29: `**Mute hotkey for dictation coexistence.** ...`.
- The "Optional mute" paragraph on line ~167: `**Optional mute:** press **Right Ctrl** ...`.
- The `[hotkey]` config row that documents `mute_key` (line ~178).

Replace any references to "Scroll Lock or Right Ctrl" with "Scroll Lock". Add a one-line note: `**Hotkey:** Scroll Lock toggles a voice session. Press once to start; press again to end.`

- [ ] **Step 2: Drop transcription-backend documentation**

If the README documents the `[transcription].backend` toggle or RemoteTranscriber, delete those rows / paragraphs. Search:

```bash
grep -nE "backend|RemoteTranscriber|whisper\.cpp" README.md
```

Delete every match line that documents the now-dead toggle.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(readme): drop mute hotkey + remote-backend documentation"
```

---

## Acceptance criteria checklist

- [ ] `uv run python -m pytest` is green.
- [ ] `uv run voice-commander --validate` prints `OK`.
- [ ] Daemon starts; web UI loads at `http://127.0.0.1:8765/page/builder`; sprite + HUD animate in response to a session start/stop.
- [ ] Voice smoke matrix (Phase 6 Task 6.1 Step 6) passes for every row.
- [ ] Builder palette shows 11 primitives (action + perception); a freshly dragged primitive saves and hot-reloads.
- [ ] `grep -REn "RemoteTranscriber|TranscriberProtocol|_speak_mode|on_speak_toggle|_mute_callback|SpeakConfig|cfg\\.speak|cfg\\.transcription\\.backend|cfg\\.hotkey\\.mute_key" src tests scripts` returns no results.
- [ ] ADR 0075 filed; technical-decisions table lists ADR 0075; supersedes annotations attached to ADRs 0025, 0072, 0073.
- [ ] `CLAUDE.md`, `docs/architecture.md`, `README.md` all reflect the primitives-only world.
