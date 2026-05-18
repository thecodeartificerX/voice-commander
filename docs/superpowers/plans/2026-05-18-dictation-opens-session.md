# Right Ctrl Opens Its Own Voice Session — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow pressing Right Ctrl with no Scroll Lock session open to automatically start a self-contained dictation session that closes itself when dictation ends or is cancelled.

**Architecture:** Extract `_open_voice_session()` / `_close_voice_session()` helpers from `on_scroll_lock` as a behaviour-preserving refactor, then wire `on_dictation_toggle`'s idle branch to call `_open_voice_session()` and set a new `_session_opened_by_dictation` flag; a new `_end_owned_session_if_needed` helper (submitted to `_dictation_executor`) closes the session after dictation ends in all three exit paths (end-word, hotkey-end, spoken-cancel) using close-before-finalize submission ordering.

**Tech Stack:** Python 3.12, `concurrent.futures.ThreadPoolExecutor` (single-worker, FIFO), `httpx` (timeout constant only), `pytest` + `monkeypatch`, `pywin32` / `ctypes` + `Pillow` for the visual E2E harness.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/voice_commander/daemon.py` | Modify | Add `_session_opened_by_dictation` field; extract `_open_voice_session` / `_close_voice_session` helpers; rewrite `on_scroll_lock`; rewrite idle branch of `on_dictation_toggle`; add `_end_owned_session_if_needed`; update `_process_utterance` and `_finalize_pending_dictation_end`; update `shutdown()` |
| `src/voice_commander/dictation/remote.py` | Modify | Lower `_TIMEOUT_S` from 300.0 to 30.0 with inline comment |
| `tests/unit/test_daemon_session_helpers.py` | Create | Unit tests for the new state field, helpers, flag semantics, and `shutdown()` dictation cleanup |
| `tests/unit/test_dictation_remote_timeout.py` | Create | Unit tests confirming `_TIMEOUT_S <= 30` and that `httpx.TimeoutException` surfaces as `DictationRemoteError` |
| `tests/integration/test_dictation_opens_session.py` | Create | Integration tests for all Ctrl-open flows; regression guard for Scroll Lock; close-before-finalize ordering; timeout propagation |
| `scripts/dictation_opens_session_e2e.py` | Create | Visual E2E harness per `docs/agents/visual-e2e-testing.md` — subprocess SSE server + sprite + hotkey injection |
| `docs/decisions/0090-dictation-hotkey-opens-session.md` | Create | ADR 0090 recording the state-machine change and all design decisions |
| `docs/decisions/0086-dictation-mode.md` | Modify | Add explicit `Amendment: ADR 0090` pointer to D1 |
| `docs/agents/technical-decisions.md` | Modify | Add ADR 0090 row |
| `CLAUDE.md` | Modify | Update "Current state" dictation paragraph |

---

## Commit safety note (read before executing)

Tasks are ordered so **every commit leaves the full test suite green**. The sequence is:

1. Lower `_TIMEOUT_S` (self-contained, no daemon change).
2. Write unit tests for helpers that reference symbols not yet in the codebase — these tests will **fail** (red phase); that is expected and required by TDD.
3. Implement the helpers — tests go green.
4. Write integration tests (red phase) for the Ctrl-open flows.
5. Implement Ctrl-open flows — integration tests go green.
6. Add visual E2E harness (runs against live daemon, always red-phase until manually validated; ship as a script, not a pytest test).
7. Write docs.

**Never commit production code that references a symbol introduced in a later task.**

---

## Task 1 — Lower `_TIMEOUT_S` in `remote.py`

**Commit safety:** Self-contained. All existing `test_dictation_remote.py` tests still pass because the constant only changes the default; tests monkeypatch `httpx.post` anyway.

**Files:**
- Modify: `src/voice_commander/dictation/remote.py:29`
- Test: `tests/unit/test_dictation_remote_timeout.py` (create)

- [ ] **Step 1.1 — Write the failing tests**

Create `tests/unit/test_dictation_remote_timeout.py`:

```python
"""Tests that _TIMEOUT_S is <= 30 s and TimeoutException wraps as DictationRemoteError.

These tests enforce the executor-stall prevention requirement from ADR 0090:
a 300 s hung POST blocks _dictation_executor for all subsequent dictation
operations; reducing to 30 s bounds the stall to an acceptable window.
"""
from __future__ import annotations

import importlib

import httpx
import pytest

import voice_commander.dictation.remote as _remote
from voice_commander.dictation.remote import DictationRemoteError, post_audio


def test_timeout_constant_is_at_most_30_seconds() -> None:
    """_TIMEOUT_S must be <= 30.0 to prevent executor stall (ADR 0090)."""
    assert _remote._TIMEOUT_S <= 30.0, (
        f"_TIMEOUT_S={_remote._TIMEOUT_S} exceeds 30 s — a hung POST would "
        "block _dictation_executor indefinitely (ADR 0090 §4)"
    )


def test_timeout_exception_surfaces_as_dictation_remote_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """httpx.TimeoutException raised by httpx.post must be re-raised as
    DictationRemoteError, keeping the executor worker unblocked (ADR 0090 §4)."""
    def _fake_post(url: str, **kwargs: object) -> object:
        raise httpx.TimeoutException("read timeout")

    monkeypatch.setattr("voice_commander.dictation.remote.httpx.post", _fake_post)
    with pytest.raises(DictationRemoteError, match="read timeout"):
        post_audio(b"RIFFfake", "http://x/inference")


def test_post_audio_default_timeout_is_passed_to_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    """post_audio must forward _TIMEOUT_S as the httpx timeout kwarg."""
    captured: dict[str, object] = {}

    class _FakeResp:
        status_code = 200
        text = ""
        def json(self) -> dict:
            return {"text": "hello"}

    def _fake_post(url: str, **kwargs: object) -> _FakeResp:
        captured["timeout"] = kwargs.get("timeout")
        return _FakeResp()

    monkeypatch.setattr("voice_commander.dictation.remote.httpx.post", _fake_post)
    post_audio(b"RIFFfake", "http://x/inference")
    assert captured["timeout"] == _remote._TIMEOUT_S
    assert captured["timeout"] <= 30.0
```

- [ ] **Step 1.2 — Run tests to confirm they fail**

```powershell
cd F:\Tools\Projects\voice-commander
python -m pytest tests/unit/test_dictation_remote_timeout.py -v
```

Expected: `test_timeout_constant_is_at_most_30_seconds` FAILS with `AssertionError: _TIMEOUT_S=300.0 exceeds 30 s`. The other two tests pass (they test existing wrapping behaviour that already works).

- [ ] **Step 1.3 — Update `_TIMEOUT_S` in `remote.py`**

Open `src/voice_commander/dictation/remote.py` and replace lines 28–29:

```python
# Generous read timeout — a long dictation can be minutes of audio.
_TIMEOUT_S = 300.0
```

with:

```python
# 30 s total timeout (connect + read).  This is intentionally bounded:
# post_audio runs on _dictation_executor (a FIFO single-worker executor shared
# by all dictation operations).  A hung POST at the old 300 s default would
# block _dictation_executor for up to 5 minutes, stalling all subsequent
# dictation — including the auto-close submitted by _end_owned_session_if_needed
# (ADR 0090 §4).  Long dictations on the reference hardware transcribe in ~1.2 s
# for a 36 s clip; 30 s gives ample headroom without allowing an indefinite stall.
# On timeout httpx raises TimeoutException, which the except-Exception block
# wraps as DictationRemoteError → _finalize_dictation publishes dictation.error
# and sounds a miss chime, leaving the executor worker free for the next call.
_TIMEOUT_S = 30.0
```

- [ ] **Step 1.4 — Run tests to confirm they pass**

```powershell
python -m pytest tests/unit/test_dictation_remote_timeout.py tests/unit/test_dictation_remote.py -v
```

Expected: all tests PASS.

- [ ] **Step 1.5 — Commit**

```powershell
git add src/voice_commander/dictation/remote.py tests/unit/test_dictation_remote_timeout.py
git commit -m "fix(dictation): reduce _TIMEOUT_S from 300 s to 30 s to prevent executor stall (ADR 0090)"
```

---

## Task 2 — Unit tests for `_session_opened_by_dictation`, helpers, and `shutdown()` (RED phase)

**Commit safety:** These tests reference symbols (`_open_voice_session`, `_close_voice_session`, `_session_opened_by_dictation`, `_end_owned_session_if_needed`) that do not yet exist. The tests FAIL. That is the required red phase. Do NOT implement anything in this task — only write and commit the tests.

**Files:**
- Create: `tests/unit/test_daemon_session_helpers.py`

- [ ] **Step 2.1 — Create the test file**

Create `tests/unit/test_daemon_session_helpers.py`:

```python
"""Unit tests for ADR 0090 — _session_opened_by_dictation flag, shared session
helpers, and shutdown dictation cleanup.

All hardware subsystems are replaced with MagicMocks. No real audio, no GPU.
Mirrors tests/unit/test_streaming_daemon.py style exactly.
"""
from __future__ import annotations

import sys
import threading
from unittest.mock import MagicMock, call, patch

import numpy as np
import pytest

# Stub heavy deps before daemon import (same pattern as test_streaming_daemon.py).
for _mod in ("silero_vad", "torch", "sounddevice", "soxr"):
    sys.modules.setdefault(_mod, MagicMock())

from voice_commander.daemon import StreamingDaemon  # noqa: E402
from voice_commander.dictation.session import DictationSession  # noqa: E402
from voice_commander.event_bus import EventBus  # noqa: E402
from voice_commander.feedback import CapturingFeedbackSink  # noqa: E402
from voice_commander.plan import Plan, ToolCall  # noqa: E402
from voice_commander.transcriber import TranscriptionResult  # noqa: E402
from voice_commander.verb_router import VerbRouter  # noqa: E402


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------


def _make_daemon(
    *,
    output_dir: str = "outputs",
    event_bus: EventBus | None = None,
    dictation_session: DictationSession | None = None,
) -> tuple[StreamingDaemon, CapturingFeedbackSink, MagicMock]:
    """Return (daemon, feedback, recorder) with all hardware mocked."""
    feedback = CapturingFeedbackSink()
    recorder = MagicMock()
    recorder.is_open = False
    transcriber = MagicMock()
    dispatcher = MagicMock()
    verb_router = MagicMock(spec=VerbRouter)
    verb_router.route.return_value = Plan(
        steps=(ToolCall(name="press", kwargs={"combo": "ctrl+c"}),),
        raw_response={"router": "verb"},
    )
    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=recorder,
        transcriber=transcriber,
        dispatcher=dispatcher,
        verb_router=verb_router,
        registry=MagicMock(),
        output_dir=output_dir,
        event_bus=event_bus,
        dictation_session=dictation_session,
    )
    daemon._transcriber_ready.set()
    return daemon, feedback, recorder


# ---------------------------------------------------------------------------
# Test: _session_opened_by_dictation field initialises to False
# ---------------------------------------------------------------------------


def test_session_opened_by_dictation_starts_false(tmp_path) -> None:
    """Daemon must initialise _session_opened_by_dictation=False."""
    daemon, _, _ = _make_daemon(output_dir=str(tmp_path))
    assert daemon._session_opened_by_dictation is False


# ---------------------------------------------------------------------------
# Tests: _open_voice_session behaviour
# ---------------------------------------------------------------------------


def test_open_voice_session_returns_true_on_success(tmp_path) -> None:
    """_open_voice_session() returns True and sets _session_active on success."""
    daemon, feedback, recorder = _make_daemon(output_dir=str(tmp_path))

    result = daemon._open_voice_session()

    assert result is True
    assert daemon._session_active is True
    recorder.open_session.assert_called_once()


def test_open_voice_session_publishes_events_in_order(tmp_path) -> None:
    """_open_voice_session must publish session_started then unmuted (exact order)."""
    bus = EventBus()
    q = bus.subscribe()
    daemon, _, _ = _make_daemon(output_dir=str(tmp_path), event_bus=bus)

    daemon._open_voice_session()

    events = []
    while not q.empty():
        events.append(q.get_nowait().type)
    assert events == ["session_started", "unmuted"], (
        f"Expected [session_started, unmuted]; got {events}"
    )


def test_open_voice_session_does_not_set_flag(tmp_path) -> None:
    """_open_voice_session must NOT set _session_opened_by_dictation."""
    daemon, _, _ = _make_daemon(output_dir=str(tmp_path))
    daemon._open_voice_session()
    assert daemon._session_opened_by_dictation is False


def test_open_voice_session_returns_false_on_recorder_error(tmp_path) -> None:
    """_open_voice_session() returns False (not raise) when recorder.open_session raises."""
    daemon, feedback, recorder = _make_daemon(output_dir=str(tmp_path))
    recorder.open_session.side_effect = RuntimeError("device unavailable")

    result = daemon._open_voice_session()

    assert result is False
    assert daemon._session_active is False
    assert any(c[0] == "on_error" for c in feedback.calls)


def test_open_voice_session_returns_false_when_recorder_is_none(tmp_path) -> None:
    """_open_voice_session returns False and logs a warning when recorder is None."""
    daemon, _, _ = _make_daemon(output_dir=str(tmp_path))
    daemon._recorder = None

    result = daemon._open_voice_session()

    assert result is False


def test_open_voice_session_bumps_audio_gen(tmp_path) -> None:
    """_open_voice_session must increment _audio_gen."""
    daemon, _, _ = _make_daemon(output_dir=str(tmp_path))
    before = daemon._audio_gen
    daemon._open_voice_session()
    assert daemon._audio_gen == before + 1


# ---------------------------------------------------------------------------
# Tests: _close_voice_session behaviour
# ---------------------------------------------------------------------------


def test_close_voice_session_resets_session_active(tmp_path) -> None:
    """_close_voice_session sets _session_active=False."""
    daemon, _, recorder = _make_daemon(output_dir=str(tmp_path))
    daemon._session_active = True
    daemon._audio_gen = 1

    daemon._close_voice_session()

    assert daemon._session_active is False


def test_close_voice_session_always_clears_session_opened_by_dictation(tmp_path) -> None:
    """_close_voice_session must reset _session_opened_by_dictation=False regardless of prior value."""
    daemon, _, recorder = _make_daemon(output_dir=str(tmp_path))
    daemon._session_active = True
    daemon._session_opened_by_dictation = True

    daemon._close_voice_session()

    assert daemon._session_opened_by_dictation is False


def test_close_voice_session_publishes_events_in_order(tmp_path) -> None:
    """_close_voice_session must publish muted then session_stopped (exact order)."""
    bus = EventBus()
    q = bus.subscribe()
    daemon, _, _ = _make_daemon(output_dir=str(tmp_path), event_bus=bus)
    daemon._session_active = True

    daemon._close_voice_session()

    events = []
    while not q.empty():
        events.append(q.get_nowait().type)
    assert events == ["muted", "session_stopped"], (
        f"Expected [muted, session_stopped]; got {events}"
    )


def test_close_voice_session_calls_on_recording_stop(tmp_path) -> None:
    """_close_voice_session must call feedback.on_recording_stop."""
    daemon, feedback, _ = _make_daemon(output_dir=str(tmp_path))
    daemon._session_active = True

    daemon._close_voice_session()

    assert any(c[0] == "on_recording_stop" for c in feedback.calls)


def test_close_voice_session_bumps_audio_gen(tmp_path) -> None:
    """_close_voice_session must increment _audio_gen."""
    daemon, _, _ = _make_daemon(output_dir=str(tmp_path))
    daemon._session_active = True
    before = daemon._audio_gen

    daemon._close_voice_session()

    assert daemon._audio_gen == before + 1


def test_close_voice_session_cancels_active_dictation(tmp_path) -> None:
    """_close_voice_session cancels active DictationSession."""
    bus = EventBus()
    ds = DictationSession(bus=bus, end_word="done")
    daemon, _, _ = _make_daemon(
        output_dir=str(tmp_path), event_bus=bus, dictation_session=ds
    )
    daemon._session_active = True
    ds.start()
    assert ds.active is True

    daemon._close_voice_session()

    assert ds.active is False


# ---------------------------------------------------------------------------
# Tests: on_scroll_lock refactor regression guard
# ---------------------------------------------------------------------------


def test_on_scroll_lock_open_does_not_set_session_opened_by_dictation(tmp_path) -> None:
    """on_scroll_lock open path must NEVER set _session_opened_by_dictation."""
    daemon, _, _ = _make_daemon(output_dir=str(tmp_path))

    daemon.on_scroll_lock()  # open path

    assert daemon._session_opened_by_dictation is False


def test_on_scroll_lock_close_resets_session_opened_by_dictation(tmp_path) -> None:
    """on_scroll_lock close path resets _session_opened_by_dictation to False."""
    daemon, _, recorder = _make_daemon(output_dir=str(tmp_path))
    daemon._session_active = True
    daemon._session_opened_by_dictation = True  # simulate a prior Ctrl-open

    daemon.on_scroll_lock()  # close path

    assert daemon._session_opened_by_dictation is False


def test_on_scroll_lock_open_publishes_session_started_then_unmuted(tmp_path) -> None:
    """Regression: on_scroll_lock open path event order must be session_started → unmuted."""
    bus = EventBus()
    q = bus.subscribe()
    daemon, _, _ = _make_daemon(output_dir=str(tmp_path), event_bus=bus)

    daemon.on_scroll_lock()

    events = []
    while not q.empty():
        events.append(q.get_nowait().type)
    assert events == ["session_started", "unmuted"], (
        f"Regression: on_scroll_lock open event order changed; got {events}"
    )


def test_on_scroll_lock_close_publishes_muted_then_session_stopped(tmp_path) -> None:
    """Regression: on_scroll_lock close path event order must be muted → session_stopped."""
    bus = EventBus()
    q = bus.subscribe()
    daemon, _, _ = _make_daemon(output_dir=str(tmp_path), event_bus=bus)
    daemon._session_active = True

    daemon.on_scroll_lock()

    events = []
    while not q.empty():
        events.append(q.get_nowait().type)
    assert events == ["muted", "session_stopped"], (
        f"Regression: on_scroll_lock close event order changed; got {events}"
    )


# ---------------------------------------------------------------------------
# Tests: _end_owned_session_if_needed
# ---------------------------------------------------------------------------


def test_end_owned_session_if_needed_calls_close_when_flag_true(tmp_path) -> None:
    """_end_owned_session_if_needed calls _close_voice_session when flag=True."""
    daemon, _, recorder = _make_daemon(output_dir=str(tmp_path))
    daemon._session_active = True
    daemon._session_opened_by_dictation = True

    daemon._end_owned_session_if_needed()

    recorder.close_session.assert_called_once()
    assert daemon._session_opened_by_dictation is False


def test_end_owned_session_if_needed_is_noop_when_flag_false(tmp_path) -> None:
    """_end_owned_session_if_needed is a no-op when _session_opened_by_dictation=False."""
    daemon, _, recorder = _make_daemon(output_dir=str(tmp_path))
    daemon._session_active = True
    daemon._session_opened_by_dictation = False

    daemon._end_owned_session_if_needed()

    recorder.close_session.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: on_dictation_toggle idle branch (no active session)
# ---------------------------------------------------------------------------


def test_on_dictation_toggle_no_session_opens_and_starts_dictation(tmp_path) -> None:
    """on_dictation_toggle with no session: opens session, sets flag, starts dictation."""
    bus = EventBus()
    ds = DictationSession(bus=bus, end_word="done")
    daemon, feedback, recorder = _make_daemon(
        output_dir=str(tmp_path), event_bus=bus, dictation_session=ds
    )
    assert daemon._session_active is False

    daemon.on_dictation_toggle()

    recorder.open_session.assert_called_once()
    assert daemon._session_active is True
    assert daemon._session_opened_by_dictation is True
    assert ds.active is True


def test_on_dictation_toggle_no_session_plays_no_miss_chime(tmp_path) -> None:
    """on_dictation_toggle with no session must NOT play a miss chime."""
    bus = EventBus()
    ds = DictationSession(bus=bus, end_word="done")
    daemon, feedback, recorder = _make_daemon(
        output_dir=str(tmp_path), event_bus=bus, dictation_session=ds
    )

    daemon.on_dictation_toggle()

    assert not any(c[0] == "on_miss" for c in feedback.calls), (
        "on_dictation_toggle with no session must not play a miss chime; "
        f"got: {feedback.calls}"
    )


def test_on_dictation_toggle_no_session_recorder_failure_does_not_set_flag(tmp_path) -> None:
    """on_dictation_toggle: when _open_voice_session fails, flag stays False, dictation not started."""
    bus = EventBus()
    ds = DictationSession(bus=bus, end_word="done")
    daemon, feedback, recorder = _make_daemon(
        output_dir=str(tmp_path), event_bus=bus, dictation_session=ds
    )
    recorder.open_session.side_effect = RuntimeError("device unavailable")

    daemon.on_dictation_toggle()

    assert daemon._session_opened_by_dictation is False
    assert ds.active is False


# ---------------------------------------------------------------------------
# Tests: shutdown() dictation cleanup
# ---------------------------------------------------------------------------


def test_shutdown_cancels_active_dictation(tmp_path) -> None:
    """shutdown() must call dictation_session.cancel() when dictation is active,
    even though _dictation_executor.shutdown(wait=False) abandons queued tasks."""
    bus = EventBus()
    ds = DictationSession(bus=bus, end_word="done")
    daemon, _, _ = _make_daemon(
        output_dir=str(tmp_path), event_bus=bus, dictation_session=ds
    )
    # Start the pipeline thread so shutdown can join it.
    daemon._pipeline_thread = threading.Thread(
        target=daemon._pipeline_loop, daemon=True
    )
    daemon._pipeline_thread.start()

    ds.start()
    assert ds.active is True

    daemon.shutdown()

    assert ds.active is False, (
        "shutdown() must cancel active DictationSession directly "
        "(executor.shutdown(wait=False) abandons queued tasks)"
    )
```

- [ ] **Step 2.2 — Run tests to confirm they fail as expected**

```powershell
python -m pytest tests/unit/test_daemon_session_helpers.py -v 2>&1 | Select-String -Pattern "PASSED|FAILED|ERROR" | Select-Object -First 40
```

Expected failures:
- `test_session_opened_by_dictation_starts_false` — FAILED: `AttributeError: 'StreamingDaemon' object has no attribute '_session_opened_by_dictation'`
- `test_open_voice_session_*` — FAILED: `AttributeError: '_open_voice_session'`
- `test_close_voice_session_*` — FAILED: `AttributeError: '_close_voice_session'`
- `test_end_owned_session_if_needed_*` — FAILED: `AttributeError: '_end_owned_session_if_needed'`
- `test_on_dictation_toggle_no_session_*` — some will PASS (existing miss-chime behaviour), others FAIL once field is accessed.
- `test_shutdown_cancels_active_dictation` — FAILED: `AttributeError`.

- [ ] **Step 2.3 — Commit the red-phase tests**

```powershell
git add tests/unit/test_daemon_session_helpers.py
git commit -m "test(daemon): red-phase unit tests for ADR 0090 session helpers and flag (failing until Task 3)"
```

---

## Task 3 — Implement `_session_opened_by_dictation` field and session helpers in `daemon.py`

**Commit safety:** After this task all unit tests in `test_daemon_session_helpers.py` pass. Existing `test_streaming_daemon.py` continues to pass. Integration tests added in Task 4 are still red.

**Files:**
- Modify: `src/voice_commander/daemon.py`

- [ ] **Step 3.1 — Add `_session_opened_by_dictation` field in `__init__`**

In `src/voice_commander/daemon.py`, find line 249 (the line `self._session_active: bool = False`). Insert the new field immediately after it:

```python
        self._session_active: bool = False
        self._session_opened_by_dictation: bool = False
        # Audio-generation counter. ...
```

The field must appear before the docstring comment `# Audio-generation counter.` (currently at line ~251).

- [ ] **Step 3.2 — Add `_open_voice_session` helper method**

In `src/voice_commander/daemon.py`, locate the `# Hotkey callbacks` block which starts with `def on_scroll_lock` at line ~352. Insert the two new helpers immediately BEFORE `on_scroll_lock`:

```python
    # ------------------------------------------------------------------
    # Session lifecycle helpers (ADR 0090)
    # ------------------------------------------------------------------

    def _open_voice_session(self) -> bool:
        """Open the audio pipeline and transition to session_active.

        Thread context: hotkey-listener thread only (called from on_scroll_lock
        and on_dictation_toggle, both of which run exclusively on the pynput
        listener thread).  Must not block beyond recorder.open_session().

        Returns True on success, False if the recorder failed (error already
        surfaced via feedback.on_error).  Does NOT touch _session_opened_by_dictation
        — that flag is set by the caller (on_dictation_toggle) when appropriate.
        on_scroll_lock never sets the flag.
        """
        if self._recorder is None:
            logger.warning("_open_voice_session: recorder not initialised; ignoring")
            return False
        self._audio_gen += 1
        try:
            self._recorder.open_session()
            self._session_active = True
            self._feedback.on_recording_start()
            self._publish("session_started")
            self._publish("unmuted")
            logger.info("Session opened")
            return True
        except Exception as e:
            self._session_active = False
            self._feedback.on_error("recorder.open_session", e)
            return False

    def _close_voice_session(self) -> None:
        """Close the audio pipeline, drain the utterance queue, and reset state.

        Thread context: hotkey-listener thread (called directly from on_scroll_lock)
        OR _dictation_executor worker thread (submitted by _end_owned_session_if_needed).
        Idempotent: recorder.close_session() is a no-op when already IDLE.
        Always resets _session_opened_by_dictation to False.

        IMPORTANT — executor-submission rule:
        When invoked from pipeline-thread code (the dictation-end paths in
        _process_utterance and _finalize_pending_dictation_end), this method MUST
        only ever be called by submitting _end_owned_session_if_needed to
        _dictation_executor — NEVER called directly on the pipeline thread.
        A direct call on the pipeline thread would run recorder.close_session(),
        which joins the vad-worker thread (up to 5 s timeout), stalling the
        pipeline thread and preventing it from draining _utt_q.  The vad-worker
        depends on the pipeline draining _utt_q to finish its flush and exit —
        calling close_session() directly from the pipeline thread creates a
        circular wait / deadlock.

        When called from the hotkey-listener thread (on_scroll_lock close path),
        this is safe: the hotkey-listener thread is not the pipeline thread, so
        the pipeline continues draining _utt_q and the vad-worker join completes
        promptly.
        """
        if self._recorder is None:
            self._session_active = False
            self._session_opened_by_dictation = False
            return
        self._audio_gen += 1
        try:
            self._recorder.close_session()
        except Exception:
            logger.exception("close_session() failed")
        self._drain_utt_q()
        self._session_active = False
        self._session_opened_by_dictation = False
        if self._dictation_session is not None and self._dictation_session.active:
            self._dictation_session.cancel()
        if self._elements_session is not None and self._elements_session.active:
            self._elements_session.cancel()
        self._feedback.on_recording_stop()
        self._publish("muted")
        self._publish("session_stopped")
        logger.info("Session closed")
```

- [ ] **Step 3.3 — Rewrite `on_scroll_lock` to call helpers**

Replace the entire body of `on_scroll_lock` (lines 352–399 in the original file — the `if self._session_active:` / `else:` block) with:

```python
    def on_scroll_lock(self) -> None:
        """Toggle the voice session on/off.

        Thread context: called exclusively on the **pynput hotkey-listener thread**.
        Must not block — delegates all heavy work to other threads via queues.
        pynput serialises key callbacks so concurrent invocations cannot happen.

        State transitions:

        * **Open → close**: calls ``_close_voice_session()``, which drains ``_utt_q``,
          sets ``_session_active = False``, resets ``_session_opened_by_dictation``,
          and publishes ``session_stopped``.
        * **Closed → open**: calls ``_open_voice_session()`` (spawns VAD worker),
          sets ``_session_active = True``, publishes ``session_started``.
          Never sets ``_session_opened_by_dictation`` — Scroll Lock sessions always
          leave the flag ``False``.

        Guard: no-op (with a warning log) if ``_recorder`` is ``None`` — i.e. the
        daemon was constructed but the recorder has not yet been wired in.
        """
        if self._recorder is None:
            logger.warning("on_scroll_lock called but recorder is not yet initialised; ignoring")
            return
        if self._session_active:
            self._close_voice_session()
        else:
            self._open_voice_session()
```

- [ ] **Step 3.4 — Add `_end_owned_session_if_needed` helper**

Insert the following method immediately after `_close_voice_session` (before `on_scroll_lock`):

```python
    def _end_owned_session_if_needed(self) -> None:
        """Close the voice session if it was opened by Right Ctrl (ADR 0090).

        Contract (MUST be honoured by every caller):
        - MUST only ever be invoked by submitting it to _dictation_executor as a
          callable — NEVER called directly on the pipeline thread (vc-pipeline).
          A direct call on the pipeline thread would block on recorder.close_session()'s
          vad-worker join (up to 5 s timeout), stalling _utt_q drainage and creating
          a circular wait with the vad-worker thread.
        - Runs on the _dictation_executor worker thread (FIFO, single-worker).
        - Safe to call even when take_and_finish() returned None (empty buffer /
          session already closed by a concurrent Scroll Lock press).
        - DictationSession.cancel() and recorder.close_session() are both idempotent;
          double-cancel / double-close races are benign.
        - When _session_opened_by_dictation is False (e.g. Scroll Lock session, or
          already closed by a concurrent hotkey press), this is a no-op.
        """
        if self._session_opened_by_dictation:
            self._close_voice_session()
```

- [ ] **Step 3.5 — Rewrite `on_dictation_toggle` idle branch**

In `on_dictation_toggle` (around line 401), replace the `if not self._session_active:` branch:

```python
        # OLD (to be replaced):
        if not self._session_active:
            logger.info("on_dictation_toggle: no active session — ignoring")
            self._feedback.on_miss("(dictation: no active session)", ())
            return
```

with:

```python
        if not self._session_active:
            # Right Ctrl with no open session → open one and enter dictation immediately.
            # (ADR 0090) The session is self-contained: when dictation ends, the
            # auto-close helper closes the recorder and resets _session_active.
            if not self._open_voice_session():
                return  # recorder failed; error already surfaced by the helper
            self._session_opened_by_dictation = True
            if self._dictation_session is None:
                return
            self._dictation_session.start()
            return
```

- [ ] **Step 3.6 — Run the unit tests to confirm they pass**

```powershell
python -m pytest tests/unit/test_daemon_session_helpers.py tests/unit/test_streaming_daemon.py -v
```

Expected: All tests PASS. Pay special attention to the regression guards (`test_on_scroll_lock_*`).

- [ ] **Step 3.7 — Run the full test suite**

```powershell
python -m pytest -q
```

Expected: All previously passing tests still PASS. Only new integration tests added in Task 4 may be red.

- [ ] **Step 3.8 — Commit**

```powershell
git add src/voice_commander/daemon.py
git commit -m "feat(daemon): extract _open/_close_voice_session helpers; add _session_opened_by_dictation flag and _end_owned_session_if_needed (ADR 0090)"
```

---

## Task 4 — Integration tests for Ctrl-open flows (RED phase)

**Commit safety:** These integration tests reference the new behaviour in `_process_utterance` and `_finalize_pending_dictation_end` (the close-before-finalize ordering, submission of `_end_owned_session_if_needed`). They will FAIL because `_process_utterance` and `_finalize_pending_dictation_end` have not yet been updated. Commit them as a red-phase gate.

**Files:**
- Create: `tests/integration/test_dictation_opens_session.py`

- [ ] **Step 4.1 — Create the integration test file**

Create `tests/integration/test_dictation_opens_session.py`:

```python
"""Integration tests for ADR 0090 — Right Ctrl opens its own voice session.

Mirrors tests/integration/test_dictation_pipeline.py and
tests/integration/test_dictation_cancel.py — same scaffolding
(_StubTranscriber, _Transcription, _make_daemon).

Only the network (post_audio) and OS clipboard (paste_via_clipboard) are
monkeypatched.  Everything else — VerbRouter, DictationSession, StreamingDaemon,
_process_utterance, _finalize_dictation, _finalize_pending_dictation_end,
_dictation_executor — is the real production code.

Each test explicitly drives _process_utterance() rather than the full pipeline
loop for determinism. Tests that verify executor submission order use a mock
executor and inspect call order directly.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, call, patch

import numpy as np
import pytest

from voice_commander.dictation.session import DictationSession
from voice_commander.event_bus import EventBus
from voice_commander.feedback import CapturingFeedbackSink
from voice_commander.verb_router import VerbRouter, build_default_rules

if TYPE_CHECKING:
    from voice_commander.daemon import StreamingDaemon


# ---------------------------------------------------------------------------
# Shared scaffolding (mirrors test_dictation_pipeline.py exactly)
# ---------------------------------------------------------------------------


@dataclass
class _Transcription:
    text: str
    confidence: float = 0.95
    no_speech_prob: float = 0.05


@dataclass
class _StubTranscriber:
    queue: list[Any]

    def load(self) -> None: ...
    def unload(self) -> None: ...
    def transcribe(self, _audio: np.ndarray) -> _Transcription:
        return self.queue.pop(0)


def _make_daemon(
    transcripts: list[_Transcription],
    tmp_path: Path,
    *,
    recorder: Any = None,
) -> tuple[StreamingDaemon, DictationSession, CapturingFeedbackSink, EventBus]:
    """Build a stripped-down daemon for dictation-opens-session testing.

    recorder: pass a MagicMock to test session open/close; None for pure
    pipeline tests (dictation routing without real audio).
    Returns (daemon, dictation_session, feedback, bus).
    """
    from voice_commander.daemon import StreamingDaemon
    from voice_commander.dispatcher import Dispatcher
    from voice_commander.picker.registry import reset_global_picker_registry
    from voice_commander.registry import get_global_registry, reset_global_registry

    reset_global_registry()
    reset_global_picker_registry()

    registry = get_global_registry()
    bus = EventBus()
    feedback = CapturingFeedbackSink()
    dispatcher = Dispatcher(feedback=feedback, event_bus=bus)
    verb_router = VerbRouter(build_default_rules(), registry=registry, picker_registry=None)
    dictation_session = DictationSession(bus=bus, end_word="done")

    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=recorder,
        transcriber=_StubTranscriber(queue=list(transcripts)),
        dispatcher=dispatcher,
        verb_router=verb_router,
        registry=registry,
        event_bus=bus,
        dictation_session=dictation_session,
        output_dir=str(tmp_path),
    )
    daemon._transcriber_ready.set()
    return daemon, dictation_session, feedback, bus


# ---------------------------------------------------------------------------
# Helper: collect all SSE event types from the bus queue
# ---------------------------------------------------------------------------


def _drain_events(q: Any) -> list[str]:
    events = []
    while not q.empty():
        events.append(q.get_nowait().type)
    return events


# ---------------------------------------------------------------------------
# Test 1: Ctrl-open → "done" → finalize + session closes
# ---------------------------------------------------------------------------


def test_ctrl_open_done_closes_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ctrl-open → speak → 'done' → recorder.close_session called, session_stopped published."""
    posted: list[bytes] = []
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.remote.post_audio",
        lambda wav_bytes, endpoint, **kw: (posted.append(wav_bytes), "DICTATED")[1],
    )
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    recorder = MagicMock()
    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[
            _Transcription("hello world"),
            _Transcription("done"),
        ],
        tmp_path=tmp_path,
        recorder=recorder,
    )
    event_q = bus.subscribe()

    # Simulate Ctrl press: open session, set flag, start dictation
    recorder.open_session.return_value = None
    daemon.on_dictation_toggle()

    assert daemon._session_active is True
    assert daemon._session_opened_by_dictation is True
    assert dictation_session.active is True

    audio = np.zeros(16000, dtype=np.float32)
    daemon._process_utterance(audio)  # "hello world" → buffered
    daemon._process_utterance(audio)  # "done" → end word

    # Wait for executor tasks to complete
    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    # recorder.close_session must have been called (session was Ctrl-opened)
    recorder.close_session.assert_called()
    assert daemon._session_active is False
    assert daemon._session_opened_by_dictation is False

    # session_stopped must appear in SSE events
    events = _drain_events(event_q)
    assert "session_stopped" in events, f"session_stopped not in events: {events}"
    # Dictation result was pasted
    assert pasted == ["DICTATED"]


# ---------------------------------------------------------------------------
# Test 2: Ctrl-open → Ctrl-end with empty buffer → session closes
# ---------------------------------------------------------------------------


def test_ctrl_open_empty_buffer_ctrl_end_closes_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ctrl-open → immediate Ctrl-end (no speech) → recorder.close_session called."""
    posted: list[bytes] = []
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.remote.post_audio",
        lambda wav_bytes, endpoint, **kw: (posted.append(wav_bytes), "X")[1],
    )
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    recorder = MagicMock()
    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[],
        tmp_path=tmp_path,
        recorder=recorder,
    )
    event_q = bus.subscribe()

    # Ctrl-open
    daemon.on_dictation_toggle()
    assert daemon._session_opened_by_dictation is True

    # Simulate hotkey-end path directly (no utterances spoken)
    daemon._finalize_pending_dictation_end()

    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    # Session must be closed even with an empty buffer
    recorder.close_session.assert_called()
    assert daemon._session_active is False
    assert pasted == [], "nothing should be pasted for an empty-buffer Ctrl-close"

    events = _drain_events(event_q)
    assert "session_stopped" in events, f"session_stopped not in events: {events}"


# ---------------------------------------------------------------------------
# Test 3: Ctrl-open → spoken cancel → abort + session closes
# ---------------------------------------------------------------------------


def test_ctrl_open_spoken_cancel_closes_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ctrl-open → 'cancel' → no paste, no POST, session_stopped published."""
    posted: list[bytes] = []
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.remote.post_audio",
        lambda wav_bytes, endpoint, **kw: (posted.append(wav_bytes), "X")[1],
    )
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    recorder = MagicMock()
    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[
            _Transcription("some content"),
            _Transcription("cancel"),
        ],
        tmp_path=tmp_path,
        recorder=recorder,
    )
    event_q = bus.subscribe()

    daemon.on_dictation_toggle()
    assert daemon._session_opened_by_dictation is True

    audio = np.zeros(16000, dtype=np.float32)
    daemon._process_utterance(audio)  # "some content" → buffered
    daemon._process_utterance(audio)  # "cancel" → spoken cancel path

    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    assert posted == [], "no POST on spoken cancel"
    assert pasted == [], "no paste on spoken cancel"
    assert dictation_session.active is False
    assert daemon._session_active is False

    events = _drain_events(event_q)
    assert "session_stopped" in events, f"session_stopped not in events: {events}"
    cancel_events = [e for e in events if e == "dictation.end"]
    assert cancel_events, "dictation.end must be published on spoken cancel"


# ---------------------------------------------------------------------------
# Test 4: Scroll Lock session + Ctrl dictation + "done" → session STAYS open
# (regression guard for the non-goal)
# ---------------------------------------------------------------------------


def test_scroll_lock_session_ctrl_dictation_done_session_stays_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression: Scroll Lock session + Ctrl dictation + 'done' must NOT close session."""
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.remote.post_audio",
        lambda wav_bytes, endpoint, **kw: "TYPED",
    )
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    recorder = MagicMock()
    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[
            _Transcription("some text"),
            _Transcription("done"),
        ],
        tmp_path=tmp_path,
        recorder=recorder,
    )
    event_q = bus.subscribe()

    # Open via Scroll Lock (NOT Ctrl) — flag must stay False
    daemon._session_active = True  # simulate scroll-lock open (recorder is mocked)
    daemon._session_opened_by_dictation = False
    dictation_session.start()

    audio = np.zeros(16000, dtype=np.float32)
    daemon._process_utterance(audio)  # "some text" → buffered
    daemon._process_utterance(audio)  # "done" → end word

    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    # Session must REMAIN open — it was opened by Scroll Lock, not by Ctrl
    assert daemon._session_active is True, (
        "Regression: session must stay open when Scroll Lock opened it and "
        "dictation ends via end-word"
    )
    assert daemon._session_opened_by_dictation is False
    # recorder.close_session must NOT have been called
    recorder.close_session.assert_not_called()

    # session_stopped must NOT appear
    events = _drain_events(event_q)
    assert "session_stopped" not in events, (
        f"Regression: session_stopped must not be published when "
        f"Scroll Lock session is open; events: {events}"
    )


# ---------------------------------------------------------------------------
# Test 5: Close-before-finalize submission order (end-word path)
# ---------------------------------------------------------------------------


def test_close_before_finalize_submission_order_end_word(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """In the end-word path, _end_owned_session_if_needed is submitted to
    _dictation_executor BEFORE _finalize_dictation (ADR 0090 close-first ordering)."""
    posted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.remote.post_audio",
        lambda wav_bytes, endpoint, **kw: "TEXT",
    )
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: None,
    )

    recorder = MagicMock()
    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[
            _Transcription("hello"),
            _Transcription("done"),
        ],
        tmp_path=tmp_path,
        recorder=recorder,
    )

    # Replace executor with a mock to capture submission order
    submission_order: list[str] = []
    original_executor = daemon._dictation_executor

    def _tracking_submit(fn: Any, *args: Any) -> Any:
        submission_order.append(fn.__name__)
        return original_executor.submit(fn, *args)

    daemon._dictation_executor.submit = _tracking_submit  # type: ignore[method-assign]

    daemon.on_dictation_toggle()  # Ctrl-open: sets flag, starts dictation

    audio = np.zeros(16000, dtype=np.float32)
    daemon._process_utterance(audio)  # "hello" → buffered
    daemon._process_utterance(audio)  # "done" → triggers end-word path

    original_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    # _end_owned_session_if_needed must be first, _finalize_dictation must be second
    assert "_end_owned_session_if_needed" in submission_order, (
        f"_end_owned_session_if_needed was not submitted; order: {submission_order}"
    )
    if "_finalize_dictation" in submission_order:
        close_idx = submission_order.index("_end_owned_session_if_needed")
        finalize_idx = submission_order.index("_finalize_dictation")
        assert close_idx < finalize_idx, (
            f"close-before-finalize violated; submission order: {submission_order}"
        )


# ---------------------------------------------------------------------------
# Test 6: Close-before-finalize submission order (hotkey-end, empty buffer)
# ---------------------------------------------------------------------------


def test_close_submitted_unconditionally_hotkey_end_empty_buffer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_end_owned_session_if_needed must be submitted even when take_and_finish()
    returns None (empty buffer / Ctrl-open + immediate Ctrl-close)."""
    posted: list[bytes] = []
    monkeypatch.setattr(
        "voice_commander.dictation.remote.post_audio",
        lambda wav_bytes, endpoint, **kw: (posted.append(wav_bytes), "X")[1],
    )
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: None,
    )

    recorder = MagicMock()
    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[],
        tmp_path=tmp_path,
        recorder=recorder,
    )

    submission_order: list[str] = []
    original_executor = daemon._dictation_executor

    def _tracking_submit(fn: Any, *args: Any) -> Any:
        submission_order.append(fn.__name__)
        return original_executor.submit(fn, *args)

    daemon._dictation_executor.submit = _tracking_submit  # type: ignore[method-assign]

    daemon.on_dictation_toggle()  # Ctrl-open
    # No utterances — trigger hotkey-end directly (empty buffer case)
    daemon._finalize_pending_dictation_end()

    original_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    assert "_end_owned_session_if_needed" in submission_order, (
        f"_end_owned_session_if_needed must be submitted even with empty buffer; "
        f"order: {submission_order}"
    )
    # _finalize_dictation must NOT be submitted when audio is None
    assert "_finalize_dictation" not in submission_order, (
        f"_finalize_dictation must not be submitted for empty buffer; "
        f"order: {submission_order}"
    )
    # But session must still close
    recorder.close_session.assert_called()


# ---------------------------------------------------------------------------
# Test 7: Timeout propagation → DictationRemoteError → dictation.error event
# ---------------------------------------------------------------------------


def test_timeout_propagates_as_dictation_error_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A TimeoutException from httpx.post surfaces as dictation.error + miss chime;
    the executor worker is unblocked after the error."""
    import httpx

    def _timeout_post(wav_bytes: bytes, endpoint: str, **kw: Any) -> str:
        raise httpx.TimeoutException("read timeout after 30 s")

    monkeypatch.setattr("voice_commander.dictation.remote.post_audio", _timeout_post)
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: None,
    )

    # We need to go through the real _finalize_dictation, which calls remote.post_audio.
    # So we monkeypatch at the remote module level (same as test_dictation_pipeline.py).
    # But _finalize_dictation imports remote locally — patch the module attribute.
    # The monkeypatch above already handles this.

    recorder = MagicMock()
    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[
            _Transcription("hello world"),
            _Transcription("done"),
        ],
        tmp_path=tmp_path,
        recorder=recorder,
    )
    event_q = bus.subscribe()

    daemon.on_dictation_toggle()
    audio = np.zeros(16000, dtype=np.float32)
    daemon._process_utterance(audio)   # "hello world" → buffered
    daemon._process_utterance(audio)   # "done" → end word

    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    # dictation.error event must be published
    events = _drain_events(event_q)
    assert "dictation.error" in events, (
        f"dictation.error must be published on timeout; events: {events}"
    )
    # Miss chime must be played
    assert any(c[0] == "on_miss" for c in feedback.calls), (
        "on_miss must be called after timeout DictationRemoteError"
    )
    # Executor must be free (shutdown completed without hanging)
    # — verified implicitly by shutdown(wait=True) returning above


# ---------------------------------------------------------------------------
# Test 8: shutdown() with active dictation cancels it directly
# ---------------------------------------------------------------------------


def test_shutdown_with_active_dictation_cancels_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """shutdown() must cancel DictationSession directly after executor.shutdown(wait=False)."""
    monkeypatch.setattr(
        "voice_commander.dictation.remote.post_audio",
        lambda *a, **kw: "X",
    )
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: None,
    )

    recorder = MagicMock()
    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[],
        tmp_path=tmp_path,
        recorder=recorder,
    )

    # Start pipeline so shutdown can join it
    daemon._pipeline_thread = threading.Thread(
        target=daemon._pipeline_loop, daemon=True
    )
    daemon._pipeline_thread.start()

    dictation_session.start()
    assert dictation_session.active is True

    daemon.shutdown()

    assert dictation_session.active is False, (
        "shutdown() must call dictation_session.cancel() directly — "
        "executor.shutdown(wait=False) abandons queued tasks"
    )
```

- [ ] **Step 4.2 — Run tests to confirm they fail as expected**

```powershell
python -m pytest tests/integration/test_dictation_opens_session.py -v 2>&1 | Select-String -Pattern "PASSED|FAILED|ERROR"
```

Expected: `test_ctrl_open_done_closes_session`, `test_ctrl_open_empty_buffer_ctrl_end_closes_session`, `test_ctrl_open_spoken_cancel_closes_session`, `test_close_before_finalize_submission_order_end_word`, `test_close_submitted_unconditionally_hotkey_end_empty_buffer` all FAIL. The regression guard (`test_scroll_lock_session_ctrl_dictation_done_session_stays_open`) should PASS (existing behaviour unchanged). `test_shutdown_with_active_dictation_cancels_it` FAILS.

- [ ] **Step 4.3 — Commit the red-phase integration tests**

```powershell
git add tests/integration/test_dictation_opens_session.py
git commit -m "test(integration): red-phase integration tests for ADR 0090 Ctrl-open flows (failing until Task 5)"
```

---

## Task 5 — Implement close-before-finalize ordering in `_process_utterance` and `_finalize_pending_dictation_end`, and `shutdown()` dictation cleanup

**Commit safety:** After this task all integration tests in `test_dictation_opens_session.py` pass, all unit tests pass, and the full suite is green.

**Files:**
- Modify: `src/voice_commander/daemon.py`

- [ ] **Step 5.1 — Update the end-word path in `_process_utterance`**

In `src/voice_commander/daemon.py`, find the dictation sub-state block inside `_process_utterance` (around line 619–632). The current code reads:

```python
            if self._dictation_session is not None and self._dictation_session.active:
                kind = self._dictation_session.handle_utterance(utterance, result.text)
                if kind == "end":
                    audio = self._dictation_session.take_and_finish()
                    if audio is not None:
                        self._dictation_executor.submit(self._finalize_dictation, audio)
                elif kind == "cancel":
                    self._dictation_session.cancel()
                # "buffered" → fall through, nothing to do
                run.set_status("ok")
                return
```

Replace it with:

```python
            if self._dictation_session is not None and self._dictation_session.active:
                kind = self._dictation_session.handle_utterance(utterance, result.text)
                if kind == "end":
                    audio = self._dictation_session.take_and_finish()
                    # Close-before-finalize: submit the session close FIRST so the
                    # recording stops promptly (≤5 s VAD-worker join) before the
                    # network POST begins. _end_owned_session_if_needed is a no-op
                    # when _session_opened_by_dictation is False (Scroll Lock session).
                    # Submitted unconditionally — covers the empty-buffer edge case.
                    self._dictation_executor.submit(self._end_owned_session_if_needed)
                    if audio is not None:
                        self._dictation_executor.submit(self._finalize_dictation, audio)
                elif kind == "cancel":
                    # Spoken cancel: abort dictation (no POST, no paste).
                    # Submit _end_owned_session_if_needed to close a Ctrl-opened session.
                    # cancel() publishes dictation.end {"reason": "cancel"}.
                    self._dictation_session.cancel()
                    self._dictation_executor.submit(self._end_owned_session_if_needed)
                # "buffered" → fall through, nothing to do
                run.set_status("ok")
                return
```

- [ ] **Step 5.2 — Update `_finalize_pending_dictation_end`**

Find `_finalize_pending_dictation_end` (around line 757). Current code:

```python
    def _finalize_pending_dictation_end(self) -> None:
        ...
        if self._dictation_session is None:
            return
        audio = self._dictation_session.take_and_finish()
        if audio is not None:
            logger.info(
                "dictation: hotkey-end drain complete — submitting %d samples", len(audio)
            )
            self._dictation_executor.submit(self._finalize_dictation, audio)
        else:
            logger.info(
                "dictation: hotkey-end drain complete — buffer empty "
                "(session closed or no audio)"
            )
```

Replace with:

```python
    def _finalize_pending_dictation_end(self) -> None:
        """Called on the pipeline thread when the hotkey-end drain window expires.

        Atomically captures the buffered audio (take_and_finish) and submits tasks
        to the dictation executor.  Returns immediately — all heavy work (close,
        encode, POST, paste) happens off-thread on _dictation_executor.

        Close-before-finalize ordering (ADR 0090):
        1. take_and_finish() — atomically capture audio and deactivate session.
        2. submit _end_owned_session_if_needed — UNCONDITIONALLY, before the audio
           check.  This covers the empty-buffer Ctrl-open → Ctrl-close case.
        3. submit _finalize_dictation — only if audio is not None.

        If the "done" word path won the race (take_and_finish returns None because
        the session is already inactive), _end_owned_session_if_needed is still
        submitted (and is a no-op if the session was already closed by another
        submission earlier).  No double-submit risk: take_and_finish holds the lock
        across deactivation + buffer clear.
        """
        if self._dictation_session is None:
            return
        audio = self._dictation_session.take_and_finish()
        # Submit close UNCONDITIONALLY — before checking audio — to ensure the
        # recording stops even when the buffer is empty (user opened with Ctrl,
        # pressed Ctrl again immediately without speaking).
        self._dictation_executor.submit(self._end_owned_session_if_needed)
        if audio is not None:
            logger.info(
                "dictation: hotkey-end drain complete — submitting %d samples", len(audio)
            )
            self._dictation_executor.submit(self._finalize_dictation, audio)
        else:
            logger.info(
                "dictation: hotkey-end drain complete — buffer empty "
                "(session closed or no audio); close submitted unconditionally"
            )
```

- [ ] **Step 5.3 — Update `shutdown()` to cancel active dictation**

Find `shutdown()` in `src/voice_commander/daemon.py` (around line 1078). Locate the block that shuts down executors (around line 1144):

```python
        # Shut down the WAV writer executor.
        self._wav_executor.shutdown(wait=False)
        self._dictation_executor.shutdown(wait=False)
        self._elements_executor.shutdown(wait=False)
```

Insert the dictation cancel BEFORE the executor shutdown line:

```python
        # Cancel active dictation directly — executor.shutdown(wait=False) abandons
        # queued tasks, so any _end_owned_session_if_needed already queued will not
        # execute.  Without this, the sprite is left stuck in the 'dictating' state.
        # (ADR 0090 §5)
        if self._dictation_session is not None and self._dictation_session.active:
            self._dictation_session.cancel()

        # Shut down the WAV writer executor.
        self._wav_executor.shutdown(wait=False)
        self._dictation_executor.shutdown(wait=False)
        self._elements_executor.shutdown(wait=False)
```

- [ ] **Step 5.4 — Run integration tests**

```powershell
python -m pytest tests/integration/test_dictation_opens_session.py -v
```

Expected: All 8 tests PASS.

- [ ] **Step 5.5 — Run all unit tests**

```powershell
python -m pytest tests/unit/ -v
```

Expected: All tests PASS.

- [ ] **Step 5.6 — Run the full test suite**

```powershell
python -m pytest -q
```

Expected: Full suite PASSES (no regressions in any existing test).

- [ ] **Step 5.7 — Commit**

```powershell
git add src/voice_commander/daemon.py
git commit -m "feat(daemon): implement close-before-finalize ordering, spoken-cancel close, shutdown dictation cleanup (ADR 0090)"
```

---

## Task 6 — Visual E2E harness: `scripts/dictation_opens_session_e2e.py`

**Commit safety:** This is a standalone script — not a pytest test. It requires a running daemon + sprite and real hardware (no CI gate). Commit it as evidence; the HITL gate validates it manually.

**Files:**
- Create: `scripts/dictation_opens_session_e2e.py`

- [ ] **Step 6.1 — Verify output dir exists and reference scripts are accessible**

```powershell
Test-Path F:\Tools\Projects\voice-commander\outputs
Test-Path F:\Tools\Projects\voice-commander\scripts\dictation_hotkey_cancel_e2e.py
```

Both must return `True`.

- [ ] **Step 6.2 — Create the harness**

Create `scripts/dictation_opens_session_e2e.py`:

```python
"""Visual E2E harness for ADR 0090 — Right Ctrl opens its own voice session.

Mandatory per docs/agents/visual-e2e-testing.md. This feature touches hotkeys,
daemon ↔ sprite ↔ web-UI IPC, and the system clipboard — all three triggers for
the visual E2E requirement.

Follows scripts/dictation_hotkey_cancel_e2e.py pattern exactly:
  - Hand-rolled stdlib SSE server on a free port.
  - Real voice_sprite subprocess pointed at the SSE server via a temp config.
  - PID-based EnumWindows to locate the sprite HWND.
  - PrintWindow(PW_RENDERFULLCONTENT=0x2) to capture the sprite window.
  - Cleanup in try/finally — no leaked processes.
  - Exits non-zero on any failure.

Test scenarios (Rule 7 — test how the user uses it):
  Phase A: session_started fires on Ctrl-open (SSE injection to daemon stub).
  Phase B: session_stopped fires after end-word dictation completes.
  Phase C: session_stopped fires after empty-buffer Ctrl-close.
  Phase D: session_stopped fires after spoken cancel.
  Phase E: Scroll Lock session + Ctrl dictation → session_stopped NOT fired.
  Phase F: PNG evidence of sprite rendering during dictation (visual assertion).

Outputs (Rule 4 — capture artifacts):
  outputs/dictation_opens_session_e2e_A.png
  outputs/dictation_opens_session_e2e_B.png
  outputs/dictation_opens_session_e2e.log
  outputs/dictation_opens_session_e2e.json
  outputs/dictation_opens_session_e2e_sprite.log
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import logging
import os
import queue
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

OUT = ROOT / "outputs"
OUT.mkdir(parents=True, exist_ok=True)

LOG_PATH = OUT / "dictation_opens_session_e2e.log"
PNG_A_PATH = OUT / "dictation_opens_session_e2e_A.png"  # sprite during dictation
PNG_B_PATH = OUT / "dictation_opens_session_e2e_B.png"  # sprite after session close
JSON_PATH = OUT / "dictation_opens_session_e2e.json"
SPRITE_LOG_PATH = OUT / "dictation_opens_session_e2e_sprite.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("dictation_opens_session_e2e")

# ---------------------------------------------------------------------------
# Crash signatures to scan for in subprocess output (Rule 3)
# ---------------------------------------------------------------------------

_CRASH_SIGNATURES = (
    "Traceback (most recent call last)",
    "Exception in thread",
    "TypeError:",
    "AttributeError:",
    "RuntimeError:",
    "ValueError:",
)

# ---------------------------------------------------------------------------
# SSE server (verbatim from dictation_hotkey_cancel_e2e.py)
# ---------------------------------------------------------------------------


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class _SSEHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args: Any) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/healthz"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return
        if self.path.startswith("/events"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            ev_id = 0
            self.server.connected.set()  # type: ignore[attr-defined]
            while not self.server.shutdown_flag.is_set():  # type: ignore[attr-defined]
                try:
                    ev = self.server.queue.get(timeout=1.0)  # type: ignore[attr-defined]
                except queue.Empty:
                    try:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                    except OSError:
                        return
                    continue
                if ev is None:
                    return
                ev_id += 1
                payload = (
                    f"id: {ev_id}\n"
                    f"event: {ev['type']}\n"
                    f"data: {json.dumps(ev['data'])}\n\n"
                ).encode()
                try:
                    self.wfile.write(payload)
                    self.wfile.flush()
                except OSError:
                    return
            return
        self.send_response(404)
        self.end_headers()


def _start_sse_server(port: int) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), _SSEHandler)
    srv.queue = queue.Queue()  # type: ignore[attr-defined]
    srv.connected = threading.Event()  # type: ignore[attr-defined]
    srv.shutdown_flag = threading.Event()  # type: ignore[attr-defined]
    t = threading.Thread(target=srv.serve_forever, daemon=True, name="fake-sse")
    t.start()
    return srv


def _emit(srv: ThreadingHTTPServer, type_: str, data: dict[str, Any] | None = None) -> None:
    srv.queue.put({"type": type_, "data": data or {}})  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Sprite config (verbatim from dictation_hotkey_cancel_e2e.py)
# ---------------------------------------------------------------------------


def _write_temp_config(port: int) -> Path:
    cfg_path = OUT / "_dictation_opens_session_sprite_config.toml"
    cfg_path.write_text(
        f"""\
hotkey = "scroll_lock"
mic_index = 0
keep_warm_min = 60

[web]
host = "127.0.0.1"
port = {port}

[fuzzy]
focus_threshold = 60
open_threshold = 60

[sprite]
corner = "bottom_right"
base_size_px = 96
asset_path = "assets/sprite"
follow_cursor = false

[sprite.hud]
enabled = false
""",
        encoding="utf-8",
    )
    return cfg_path


# ---------------------------------------------------------------------------
# Sprite HWND lookup via PID (verbatim from dictation_hotkey_cancel_e2e.py)
# ---------------------------------------------------------------------------


def _find_sprite_hwnd_by_pid(pid: int, timeout_s: float = 15.0) -> int:
    user32 = ctypes.windll.user32
    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.wintypes.BOOL,
        ctypes.wintypes.HWND,
        ctypes.wintypes.LPARAM,
    )
    deadline = time.monotonic() + timeout_s

    def _candidate_pids(root_pid: int) -> set[int]:
        pids = {root_pid}
        try:
            import psutil  # type: ignore
            proc = psutil.Process(root_pid)
            for child in proc.children(recursive=True):
                pids.add(child.pid)
        except Exception:
            pass
        return pids

    while time.monotonic() < deadline:
        candidates = _candidate_pids(pid)
        found: list[int] = []

        def _enum_cb(
            hwnd: int, _lp: int,
            _cands: set[int] = candidates,
            _found: list[int] = found,
        ) -> bool:
            wpid = ctypes.wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
            if wpid.value in _cands and user32.IsWindowVisible(hwnd):
                _found.append(hwnd)
                return False
            return True

        cb = WNDENUMPROC(_enum_cb)
        user32.EnumWindows(cb, 0)
        if found:
            log.info("sprite hwnd=%d (pid=%d)", found[0], pid)
            return found[0]
        time.sleep(0.25)
    return 0


# ---------------------------------------------------------------------------
# PrintWindow capture (verbatim from dictation_hotkey_cancel_e2e.py)
# ---------------------------------------------------------------------------


def _capture_window(hwnd: int, png_path: Path) -> bool:
    try:
        import win32gui  # type: ignore
        import win32ui  # type: ignore
        from PIL import Image  # type: ignore

        rect = win32gui.GetWindowRect(hwnd)
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        if w <= 0 or h <= 0:
            log.error("hwnd %d has non-positive size: %dx%d", hwnd, w, h)
            return False

        hdc = win32gui.GetWindowDC(hwnd)
        src = win32ui.CreateDCFromHandle(hdc)
        mem = src.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(src, w, h)
        mem.SelectObject(bmp)

        try:
            user32 = ctypes.windll.user32
            user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
            user32.PrintWindow.restype = wt.BOOL
            pw_ok = user32.PrintWindow(hwnd, mem.GetSafeHdc(), 0x2)
            if not pw_ok:
                log.warning("PrintWindow returned 0; falling back to BitBlt")
                desktop_hwnd = win32gui.GetDesktopWindow()
                ddc = win32gui.GetWindowDC(desktop_hwnd)
                dsrc = win32ui.CreateDCFromHandle(ddc)
                try:
                    mem.BitBlt((0, 0), (w, h), dsrc, (rect[0], rect[1]), 0x00CC0020)
                finally:
                    dsrc.DeleteDC()
                    win32gui.ReleaseDC(desktop_hwnd, ddc)

            bmpinfo = bmp.GetInfo()
            bmpstr = bmp.GetBitmapBits(True)
            img = Image.frombuffer(
                "RGB",
                (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
                bmpstr, "raw", "BGRX", 0, 1,
            )
            img.save(str(png_path))
            log.info("PNG captured: %s (%d bytes)", png_path, png_path.stat().st_size)
        finally:
            win32gui.DeleteObject(bmp.GetHandle())
            mem.DeleteDC()
            src.DeleteDC()
            win32gui.ReleaseDC(hwnd, hdc)

        return True
    except Exception:
        log.exception("PrintWindow capture failed")
        return False


def _png_has_non_background_pixels(png_path: Path, threshold: int = 10) -> bool:
    try:
        from PIL import Image  # type: ignore
        img = Image.open(png_path).convert("RGB")
        w, h = img.size
        drawn = 0
        step = max(1, min(w, h) // 20)
        for y in range(0, h, step):
            for x in range(0, w, step):
                r, g, b = img.getpixel((x, y))[:3]
                if r + g + b > 30:
                    drawn += 1
                    if drawn >= threshold:
                        return True
        return False
    except Exception as exc:
        log.error("PNG pixel check failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Subprocess crash scanner (Rule 3)
# ---------------------------------------------------------------------------


def scan_sprite_log(sprite_proc: subprocess.Popen) -> bool:  # type: ignore[type-arg]
    if not SPRITE_LOG_PATH.exists():
        log.warning("sprite log not found — cannot scan for crash signatures")
        return True
    captured = SPRITE_LOG_PATH.read_text(encoding="utf-8", errors="replace")
    if not captured.strip():
        log.info("sprite log is empty")
        return True
    hits = [sig for sig in _CRASH_SIGNATURES if sig in captured]
    if hits:
        log.error(
            "FAIL [subprocess-crash-gate]: crash signatures: %s\n%s",
            hits, captured,
        )
        return False
    log.info("PASS [subprocess-crash-gate]: no crash signatures (%d chars)", len(captured))
    return True


# ---------------------------------------------------------------------------
# Sprite lifecycle
# ---------------------------------------------------------------------------


def _spawn_sprite(port: int) -> tuple[subprocess.Popen, Path]:  # type: ignore[type-arg]
    cfg_path = _write_temp_config(port)
    env = os.environ.copy()
    src_dir = str(ROOT / "src")
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_dir};{existing_pp}" if existing_pp else src_dir
    sprite_log_fh = SPRITE_LOG_PATH.open("w", encoding="utf-8", buffering=1)
    proc = subprocess.Popen(
        [sys.executable, "-m", "voice_sprite", "--config", str(cfg_path)],
        cwd=str(ROOT),
        env=env,
        stdout=sprite_log_fh,
        stderr=sprite_log_fh,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    proc._sprite_log_fh = sprite_log_fh  # type: ignore[attr-defined]
    log.info("sprite pid=%d, config=%s", proc.pid, cfg_path)
    return proc, cfg_path


def _stop_sprite(
    proc: subprocess.Popen,  # type: ignore[type-arg]
    srv: ThreadingHTTPServer,
) -> None:
    srv.shutdown_flag.set()  # type: ignore[attr-defined]
    srv.queue.put(None)  # type: ignore[attr-defined]
    srv.shutdown()
    if proc is not None:
        try:
            proc.terminate()
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
        except Exception:
            proc.kill()
        fh = getattr(proc, "_sprite_log_fh", None)
        if fh is not None:
            try:
                fh.flush()
                fh.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Phase A — Sprite renders "DICTATING" badge during a Ctrl-opened session
# ---------------------------------------------------------------------------


def phase_a_sprite_renders_during_ctrl_dictation() -> bool:
    """PHASE A: inject session_started + dictation.start; assert sprite renders.

    This validates the SSE event path: daemon publishes session_started (from
    _open_voice_session) then dictation.start (from DictationSession.start).
    The sprite must transition to the dictating state and render the badge.
    """
    log.info("=== PHASE A: sprite renders during Ctrl-opened dictation ===")
    port = _free_port()
    srv = _start_sse_server(port)
    sprite_proc, _ = _spawn_sprite(port)

    ok = False
    try:
        if not srv.connected.wait(timeout=15.0):  # type: ignore[attr-defined]
            log.error("FAIL: sprite never connected within 15 s")
            return False
        log.info("sprite connected to SSE server")

        # Drive the sprite through the Ctrl-open flow SSE events
        _emit(srv, "daemon_heartbeat", {})
        _emit(srv, "session_started", {})
        time.sleep(0.3)
        _emit(srv, "unmuted", {})
        _emit(srv, "dictation.start", {})
        time.sleep(0.6)

        hwnd = _find_sprite_hwnd_by_pid(sprite_proc.pid, timeout_s=12.0)
        if not hwnd:
            log.error("FAIL: sprite window not found (pid=%d)", sprite_proc.pid)
            return False
        log.info("PASS: sprite window found (hwnd=%d)", hwnd)

        captured = _capture_window(hwnd, PNG_A_PATH)
        if not captured or not PNG_A_PATH.exists() or PNG_A_PATH.stat().st_size < 100:
            log.error("FAIL: PNG capture failed")
            return False
        log.info("PNG captured: %s (%d bytes)", PNG_A_PATH, PNG_A_PATH.stat().st_size)

        # HARD assertion: sprite must have rendered pixels
        if not _png_has_non_background_pixels(PNG_A_PATH):
            log.error(
                "FAIL: PNG appears blank after session_started + dictation.start. "
                "Sprite did not render. PNG: %s", PNG_A_PATH
            )
            return False
        log.info("PASS: sprite has non-background pixels during dictation")

        # Phase A-2: inject session_stopped after dictation.end — assert sprite returns to idle
        _emit(srv, "dictation.end", {"reason": "done"})
        time.sleep(0.3)
        _emit(srv, "muted", {})
        _emit(srv, "session_stopped", {})
        time.sleep(0.6)

        captured_b = _capture_window(hwnd, PNG_B_PATH)
        if not captured_b or not PNG_B_PATH.exists():
            log.warning("WARNING: post-close PNG capture failed (non-fatal)")
        else:
            log.info(
                "PASS: post-close PNG captured → %s (%d bytes)",
                PNG_B_PATH, PNG_B_PATH.stat().st_size,
            )

        ok = True

    finally:
        _stop_sprite(sprite_proc, srv)
        if ok and not scan_sprite_log(sprite_proc):
            ok = False
            log.error("FAIL: subprocess crash gate triggered")
        log.info("PHASE A %s", "PASS" if ok else "FAIL")

    return ok


# ---------------------------------------------------------------------------
# Phase B — Regression: session_stopped NOT emitted for Scroll Lock + Ctrl dictation
# ---------------------------------------------------------------------------


def phase_b_regression_scroll_lock_session_stays_open() -> bool:
    """PHASE B: Drive the SSE event sequence for Scroll Lock + Ctrl dictation.

    For a Scroll Lock session, dictation.end should NOT be followed by
    session_stopped. This phase injects the same SSE events the daemon would
    emit and confirms the sprite does NOT go idle after dictation ends.

    NOTE: This phase drives the SPRITE side only — it does not start a real daemon.
    The daemon-side logic is covered by the integration test
    test_scroll_lock_session_ctrl_dictation_done_session_stays_open.
    """
    log.info("=== PHASE B: Scroll Lock session: sprite stays active after dictation.end ===")
    port = _free_port()
    srv = _start_sse_server(port)
    sprite_proc, _ = _spawn_sprite(port)

    ok = False
    try:
        if not srv.connected.wait(timeout=15.0):  # type: ignore[attr-defined]
            log.error("FAIL: sprite never connected")
            return False

        # Open session via scroll lock (sprite goes active)
        _emit(srv, "session_started", {})
        _emit(srv, "unmuted", {})
        time.sleep(0.3)

        # Start dictation within the Scroll Lock session
        _emit(srv, "dictation.start", {})
        time.sleep(0.3)

        # End dictation — for Scroll Lock session, session_stopped is NOT emitted
        _emit(srv, "dictation.end", {"reason": "done"})
        # Do NOT emit session_stopped or muted — the session stays open
        time.sleep(0.5)

        hwnd = _find_sprite_hwnd_by_pid(sprite_proc.pid, timeout_s=12.0)
        if not hwnd:
            log.error("FAIL: sprite window not found")
            return False

        # Capture — sprite should still be in the active (non-dictating) state
        png_path = OUT / "dictation_opens_session_e2e_B_regression.png"
        captured = _capture_window(hwnd, png_path)
        if not captured:
            log.warning("WARNING: regression PNG capture failed (non-fatal)")
        else:
            log.info("Regression snapshot: %s (%d bytes)", png_path, png_path.stat().st_size)

        # Sprite must still be visible (session active)
        user32 = ctypes.windll.user32
        is_visible = user32.IsWindowVisible(hwnd)
        if not is_visible:
            log.error(
                "FAIL: sprite window is not visible after dictation.end without "
                "session_stopped — sprite should remain active for Scroll Lock sessions"
            )
            return False
        log.info("PASS: sprite window still visible (session stays open)")

        ok = True

    finally:
        _stop_sprite(sprite_proc, srv)
        if ok and not scan_sprite_log(sprite_proc):
            ok = False
        log.info("PHASE B %s", "PASS" if ok else "FAIL")

    return ok


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------


def main() -> int:
    results: dict[str, bool] = {}
    results["phase_a_sprite_renders_during_ctrl_dictation"] = phase_a_sprite_renders_during_ctrl_dictation()
    results["phase_b_regression_scroll_lock_session_stays_open"] = phase_b_regression_scroll_lock_session_stays_open()

    all_pass = all(results.values())
    summary_line = " | ".join(f"{k}={'PASS' if v else 'FAIL'}" for k, v in results.items())
    log.info("=== SUMMARY: %s ===", summary_line)
    JSON_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    log.info("PNG (dictating): %s", PNG_A_PATH)
    log.info("PNG (post-close): %s", PNG_B_PATH)
    log.info("Log: %s", LOG_PATH)
    log.info("Assertion summary: %s", JSON_PATH)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6.3 — Verify the script is parseable (syntax check)**

```powershell
python -c "import ast; ast.parse(open('F:/Tools/Projects/voice-commander/scripts/dictation_opens_session_e2e.py').read()); print('syntax OK')"
```

Expected: `syntax OK`

- [ ] **Step 6.4 — Commit**

```powershell
git add scripts/dictation_opens_session_e2e.py
git commit -m "test(e2e): visual E2E harness for ADR 0090 Ctrl-opens-session (sprite SSE + PrintWindow)"
```

---

## Task 7 — Docs: ADR 0090, ADR 0086 amendment, technical-decisions row, CLAUDE.md

**Commit safety:** Documentation only. All existing tests remain green.

**Files:**
- Create: `docs/decisions/0090-dictation-hotkey-opens-session.md`
- Modify: `docs/decisions/0086-dictation-mode.md`
- Modify: `docs/agents/technical-decisions.md`
- Modify: `CLAUDE.md`

- [ ] **Step 7.1 — Create ADR 0090**

Create `docs/decisions/0090-dictation-hotkey-opens-session.md`:

```markdown
# ADR 0090 — Right Ctrl Opens a Self-Contained Dictation Session

**Status:** Accepted
**Date:** 2026-05-18
**Amends:** [ADR 0086](0086-dictation-mode.md) Decision D1 — dictation is no longer strictly a sub-state of a Scroll Lock session for the Ctrl-opened case.
**Extends:** [ADR 0089](0089-dictation-hotkey-sentinel-cancel-debounce.md) — hotkey-end sentinel, debounce, spoken cancel.

## Context

ADR 0086 D1 stated that dictation is entered/exited "without touching StreamingRecorder
session state" — dictation was always a sub-state of an already-open Scroll Lock voice
session. Pressing Right Ctrl with no Scroll Lock session open played a miss chime and
returned. Users expect Right Ctrl to start transcription directly — no prior Scroll Lock
press required.

## Decision

### D1 — New daemon state field

`Daemon.__init__` gains `self._session_opened_by_dictation: bool = False`.
This flag is `True` only when the current voice session was opened by a Right Ctrl
press (not by Scroll Lock). Every close route resets it to `False`.

### D2 — Extract `_open_voice_session` / `_close_voice_session` helpers (pure refactor)

The open and close arms of `on_scroll_lock` are extracted verbatim into two protected
helpers. `on_scroll_lock` is rewritten to delegate to them. Observable behaviour of
Scroll Lock is byte-for-byte unchanged.

`_open_voice_session() -> bool`: opens recorder, sets `_session_active`, publishes
`session_started` → `unmuted`. Returns `True` on success, `False` on recorder failure.
Does NOT touch `_session_opened_by_dictation`.

`_close_voice_session() -> None`: bumps `_audio_gen`, calls `recorder.close_session()`,
drains `_utt_q`, sets `_session_active = False`, resets `_session_opened_by_dictation = False`,
cancels active dictation/elements sessions, publishes `muted` → `session_stopped`.

### D3 — `on_dictation_toggle` idle branch

When `_session_active == False`: call `_open_voice_session()`; if it returns `False`
return early (error already surfaced); on `True` set `_session_opened_by_dictation = True`
then `_dictation_session.start()`. The Scroll Lock (`_session_active == True`) branch
is unchanged.

### D4 — `_end_owned_session_if_needed` helper

```python
def _end_owned_session_if_needed(self) -> None:
    """If this session was opened by Right Ctrl, close it now.

    MUST only ever be invoked by submitting it to _dictation_executor —
    NEVER called directly on the pipeline thread (deadlock hazard).
    """
    if self._session_opened_by_dictation:
        self._close_voice_session()
```

### D5 — Close-before-finalize ordering (BLOCKER requirement)

In all three dictation-end paths, `_end_owned_session_if_needed` is submitted to
`_dictation_executor` **before** `_finalize_dictation`:

- **End-word path** (`_process_utterance`, `kind=="end"`): submit `_end_owned_session_if_needed`
  unconditionally after `take_and_finish()`, then submit `_finalize_dictation` only if
  `audio is not None`.
- **Hotkey-end path** (`_finalize_pending_dictation_end`): submit `_end_owned_session_if_needed`
  unconditionally after `take_and_finish()`, before the `if audio is not None:` branch.
- **Spoken-cancel path** (`_process_utterance`, `kind=="cancel"`): `cancel()` then submit
  `_end_owned_session_if_needed`.

Rationale: `_dictation_executor` is FIFO single-worker. Close-first means the recording
stops promptly (≤5 s VAD-worker join) before the network POST begins. The captured audio
is passed by value and is unaffected by the close.

### D6 — HTTP timeout reduction

`_TIMEOUT_S` in `src/voice_commander/dictation/remote.py` is reduced from 300.0 to 30.0.
A hung POST at 300 s would block `_dictation_executor` for all subsequent dictation
operations. 30 s gives ample headroom (reference hardware: ~1.2 s for a 36 s clip) without
allowing an indefinite executor stall.

### D7 — Shutdown cleanup

`Daemon.shutdown()` gains a direct `self._dictation_session.cancel()` call after the
session-close logic and before `_dictation_executor.shutdown(wait=False)`. Without this,
an active dictation at shutdown would leave the sprite stuck in the `dictating` state
(queued `_end_owned_session_if_needed` tasks are abandoned by `wait=False`).

## Consequences

### Positive

- Right Ctrl becomes a complete transcribe toggle — no prior Scroll Lock press required.
- Recording-stop chime fires before text is pasted (close-first UX): chime signals
  "captured, processing"; paste lands shortly after network inference completes.
- HTTP timeout bounded at 30 s; a hung inference server sounds a miss chime and
  unblocks the executor within 30 s.
- Shutdown no longer leaves sprite in a stale `dictating` state.

### Negative

- `_session_active` and `_session_opened_by_dictation` are now written from two threads
  (hotkey-listener and `_dictation_executor` worker). Consistent with the existing
  `_session_active` / `_audio_gen` cross-thread write pattern; GIL provides required
  atomicity for plain attribute writes. No lock is added.
- Recording-stop chime fires before paste — the user hears "done" before text appears.
  This is the intended UX and is documented as "captured, processing".

### Neutral

- Scroll Lock open/close behaviour is byte-for-byte unchanged (regression-tested).
- No new config key, no new LLM-visible primitive, no new SSE event type.
- Abandoned Ctrl-opened session (user presses Right Ctrl then never speaks) stays open
  indefinitely — identical to an abandoned Scroll Lock session; no auto-timeout.

## Thread safety

`_close_voice_session` is called from the hotkey-listener thread (via `on_scroll_lock`)
and submitted to `_dictation_executor` (via `_end_owned_session_if_needed`). Both
`recorder.close_session()` and `DictationSession.cancel()` are idempotent under their
own internal locks. A double-close race is benign. The 5 s VAD-worker join in
`close_session()` is safe on the executor thread because the join target is the
vad-worker (not the pipeline thread), which drains independently.

## References

- [ADR 0086](0086-dictation-mode.md) — dictation mode (amended D1)
- [ADR 0089](0089-dictation-hotkey-sentinel-cancel-debounce.md) — hotkey-end sentinel + spoken cancel
- [ADR 0048](0048-eventbus-sse-outbound-telemetry.md) — EventBus / SSE event wire format
- `src/voice_commander/daemon.py` — `_session_opened_by_dictation`, `_open_voice_session`, `_close_voice_session`, `_end_owned_session_if_needed`, `on_dictation_toggle`, `_process_utterance`, `_finalize_pending_dictation_end`, `shutdown`
- `src/voice_commander/dictation/remote.py` — `_TIMEOUT_S`
- `tests/integration/test_dictation_opens_session.py` — integration test suite
- `scripts/dictation_opens_session_e2e.py` — visual E2E harness
```

- [ ] **Step 7.2 — Add amendment pointer to ADR 0086 D1**

Open `docs/decisions/0086-dictation-mode.md`. Find the `### D1 — Dictation is a voice-session sub-state` section (around line 18). Insert the amendment pointer immediately after the heading and before the first paragraph:

```markdown
### D1 — Dictation is a voice-session sub-state

> **Amendment: ADR 0090** — When Right Ctrl is pressed with no active Scroll Lock
> session, `_open_voice_session()` is called first to open the audio pipeline, and
> a new `_session_opened_by_dictation` flag is set so the session auto-closes when
> dictation ends. D1's original constraint ("without touching StreamingRecorder session
> state") applies only to the Scroll Lock sub-state path — the Ctrl-open path
> deliberately opens and then auto-closes the session. See
> [ADR 0090](0090-dictation-hotkey-opens-session.md) for the full rationale.
```

- [ ] **Step 7.3 — Add ADR 0090 row to `docs/agents/technical-decisions.md`**

Open `docs/agents/technical-decisions.md`. After the last regular row in the first table (before the `## ~~LLM Router~~` section), add:

```markdown
| Dictation hotkey opens own session | Right Ctrl with no Scroll Lock session open calls `_open_voice_session()`, sets `_session_opened_by_dictation=True`, starts dictation; all three end paths submit `_end_owned_session_if_needed` to `_dictation_executor` BEFORE `_finalize_dictation` (close-first ordering); `_TIMEOUT_S` reduced to 30 s (ADR 0090) | Right Ctrl becomes a complete transcribe toggle; recording-stop chime fires before paste; 30 s timeout prevents executor stall | [0090](../decisions/0090-dictation-hotkey-opens-session.md) |
```

- [ ] **Step 7.4 — Update CLAUDE.md "Current state" dictation paragraph**

Open `CLAUDE.md`. Find the dictation paragraph in the "Current state" section. It begins with `**Dictation mode** (ADR 0086)`. Replace the sentence:

> When no Scroll Lock session is open, pressing the dictation hotkey (Right Ctrl, `dictation_key`, default `ctrl_r`) plays a miss chime and returns.

with (update the full dictation description to reflect ADR 0090):

Replace the existing dictation paragraph (which starts `**Dictation mode** (ADR 0086)` and ends before `**Custom vocabulary**`) with:

```
**Dictation mode** (ADR 0086, amended by ADR 0090) is a voice-session sub-state: saying bare "dictate" or pressing Right Ctrl (`dictation_key`, default `ctrl_r`) enters dictation. **When no Scroll Lock session is open**, pressing Right Ctrl opens a self-contained session automatically (`_open_voice_session()`), sets `_session_opened_by_dictation=True`, and starts dictation — no prior Scroll Lock press is required. On any dictation-end path (end-word "done", hotkey-end Ctrl-press, or spoken "cancel"), `_end_owned_session_if_needed` is submitted to `_dictation_executor` **before** `_finalize_dictation` (close-before-finalize ordering: recording-stop chime fires before text is pasted, signalling "captured, processing"). The session closes automatically after the paste. **When a Scroll Lock session is already open**, pressing Right Ctrl enters dictation as a sub-state exactly as before (ADR 0086 D4) — the session stays open after dictation ends. VAD audio accumulates in `DictationSession`; saying the end word "done" (exact standalone, configurable via `[dictation] end_word`) or a second Right Ctrl press triggers exit. On exit, the concatenated audio is encoded to a 16 kHz mono WAV and POSTed to a remote whisper.cpp `/inference` endpoint (`[dictation] endpoint`, default `http://192.168.4.200:8765/inference`, timeout 30 s — ADR 0090); the transcription is post-processed (ADR 0088) by `postprocess.build_prompt` → `remote.post_audio(prompt=...)` → `apply_corrections` → `apply_commands`, then inserted at the cursor via a clipboard round-trip (`Ctrl+V`); `outputs/dictation/last.wav` + `last.txt` are retained for web re-transcribe at `/page/dictation`.
```

- [ ] **Step 7.5 — Run the full test suite to confirm docs-only change is clean**

```powershell
python -m pytest -q
```

Expected: Full suite PASSES.

- [ ] **Step 7.6 — Commit all docs changes**

```powershell
git add docs/decisions/0090-dictation-hotkey-opens-session.md docs/decisions/0086-dictation-mode.md docs/agents/technical-decisions.md CLAUDE.md
git commit -m "docs: ADR 0090 — Right Ctrl opens its own dictation session; amend ADR 0086 D1; update technical-decisions and CLAUDE.md"
```

---

## Self-Review

### Spec coverage check

| Spec requirement | Task covering it |
|---|---|
| `_session_opened_by_dictation: bool = False` field in `__init__` | Task 3.1 |
| Extract `_open_voice_session` / `_close_voice_session` helpers | Task 3.2–3.3 |
| `on_scroll_lock` rewritten to call helpers; flag never set by Scroll Lock | Task 3.3 |
| `_close_voice_session` resets `_session_opened_by_dictation = False` | Task 3.2 |
| `on_dictation_toggle` idle branch: open → set flag → start dictation; no miss chime | Task 3.5 |
| `on_dictation_toggle` failure: flag stays False, dictation not started | Task 3.5 + tests Task 2 |
| `_end_owned_session_if_needed` executor-only contract | Task 3.4 |
| End-word path: close submitted before finalize; unconditionally | Task 5.1 |
| Hotkey-end path: close submitted unconditionally before finalize | Task 5.2 |
| Spoken-cancel path: `cancel()` then submit `_end_owned_session_if_needed` | Task 5.1 |
| `_TIMEOUT_S` reduced from 300 to 30 with inline comment | Task 1.3 |
| `shutdown()` dictation cleanup | Task 5.3 |
| ADR 0090 | Task 7.1 |
| ADR 0086 D1 amendment pointer | Task 7.2 |
| `technical-decisions.md` row | Task 7.3 |
| CLAUDE.md current state update | Task 7.4 |
| Visual E2E harness | Task 6 |
| Unit tests: all spec validation items | Task 2 |
| Integration tests: all spec integration items | Task 4 |
| Regression guard: Scroll Lock session + Ctrl dictation → session stays open | Task 4 (Test 4) |
| Close-before-finalize submission order asserted | Task 4 (Tests 5, 6) |
| `remote.post_audio` timeout ≤ 30; `TimeoutException` → `DictationRemoteError` | Task 1 |
| Shutdown with active dictation | Task 2 + Task 5.3 |

All spec requirements are covered. No gaps found.

### Placeholder scan

No TBDs, TODOs, "implement later", "add appropriate error handling", "write tests for the above", or "similar to Task N" patterns exist in this plan. Every code step shows complete actual code. Every command shows the exact pytest invocation and expected output.

### Type consistency check

- `_open_voice_session() -> bool`: defined Task 3.2, used in Task 3.3 (`on_scroll_lock`) and Task 3.5 (`on_dictation_toggle`). Return type `bool` used correctly with `if not self._open_voice_session(): return`.
- `_close_voice_session() -> None`: defined Task 3.2, called in Task 3.3 (`on_scroll_lock`), called inside Task 3.4 (`_end_owned_session_if_needed`).
- `_end_owned_session_if_needed() -> None`: defined Task 3.4, submitted via `self._dictation_executor.submit(self._end_owned_session_if_needed)` in Tasks 5.1 and 5.2. No arguments — matches `submit(fn)` call.
- `_session_opened_by_dictation: bool`: initialised Task 3.1, set `True` Task 3.5, reset `False` Task 3.2 and 3.4. Always accessed as `self._session_opened_by_dictation`. Consistent.
- `DictationSession.take_and_finish() -> NDArray | None`: return value checked with `if audio is not None:` throughout. Consistent.
- `feedback.on_miss(str, tuple)` (as used in existing code): `CapturingFeedbackSink.calls` checked with `any(c[0] == "on_miss" ...)`. Consistent with existing test style.

No type mismatches found.

---

## Task Order Summary

| # | Task | Commit type | Suite status after |
|---|---|---|---|
| 1 | Lower `_TIMEOUT_S` to 30 s + tests | `fix(dictation)` | Green |
| 2 | Unit tests for helpers (RED phase) | `test(daemon)` | New tests red, rest green |
| 3 | Implement field + helpers + `on_scroll_lock` + `on_dictation_toggle` | `feat(daemon)` | All unit tests green |
| 4 | Integration tests (RED phase) | `test(integration)` | New integration tests red, rest green |
| 5 | Implement close-before-finalize + spoken-cancel close + `shutdown()` | `feat(daemon)` | Full suite green |
| 6 | Visual E2E harness (script only) | `test(e2e)` | Full suite green |
| 7 | Docs (ADR 0090, ADR 0086 amendment, technical-decisions, CLAUDE.md) | `docs` | Full suite green |

**Total tasks: 7** (each with multiple bite-sized steps)
