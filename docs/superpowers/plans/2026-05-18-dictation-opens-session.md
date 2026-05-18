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
| `src/voice_commander/dictation/remote.py` | Modify | Lower `_TIMEOUT_S` from 300.0 to 30.0 with inline comment; update `post_audio` docstring |
| `tests/unit/test_daemon_session_helpers.py` | Create | Unit tests for the new state field, helpers, flag semantics |
| `tests/unit/test_dictation_remote.py` | Modify | Add unit test confirming `httpx.TimeoutException` surfaces as `DictationRemoteError` |
| `tests/unit/test_dictation_remote_timeout.py` | Create | Unit test enforcing `_TIMEOUT_S <= 30` and default-timeout forwarding |
| `tests/integration/test_dictation_opens_session.py` | Create | Integration tests for all Ctrl-open flows; regression guard for Scroll Lock; close-before-finalize ordering |
| `scripts/dictation_opens_session_e2e.py` | Create | Visual E2E harness — real daemon subprocess + real hotkey injection + SSE assertion per `docs/agents/visual-e2e-testing.md` |
| `docs/decisions/0090-dictation-hotkey-opens-session.md` | Create | ADR 0090 |
| `docs/decisions/0086-dictation-mode.md` | Modify | Add explicit `Amendment: ADR 0090` pointer to D1 |
| `docs/agents/technical-decisions.md` | Modify | Add ADR 0090 row |
| `CLAUDE.md` | Modify | Update "Current state" dictation paragraph |

---

## Commit safety note (read before executing)

Tasks are ordered so **every commit leaves the full test suite green**. The sequence is:

1. Lower `_TIMEOUT_S` and add unit tests for the remote module (self-contained).
2. Write unit tests for helpers that reference symbols not yet in the codebase — these tests will **fail** (red phase); that is expected and required by TDD.
3. Implement the helpers — unit tests go green.
4. Write integration tests (red phase) for the Ctrl-open flows, **including** the shutdown test which pairs with Task 5.
5. Implement Ctrl-open flows and `shutdown()` cleanup — integration tests go green.
6. Add visual E2E harness (real daemon + real hotkey; not a pytest test).
7. Write docs.

**Never commit production code that references a symbol introduced in a later task.**

---

## Task 1 — Lower `_TIMEOUT_S` in `remote.py` and add remote unit tests

**Commit safety:** Self-contained. All existing `test_dictation_remote.py` tests still pass.

**Files:**
- Modify: `src/voice_commander/dictation/remote.py`
- Modify: `tests/unit/test_dictation_remote.py` (add one new test)
- Create: `tests/unit/test_dictation_remote_timeout.py`

### Step 1.1 — Verify current state of `remote.py`

Read `src/voice_commander/dictation/remote.py` lines 1–98 to confirm:

- `_TIMEOUT_S = 300.0` is on line 29.
- `post_audio` has an `except Exception as e: raise DictationRemoteError(...)` on line 77–78 that already wraps ALL exceptions — including `httpx.TimeoutException` — into `DictationRemoteError`.
- The `post_audio` docstring on line 60 says `"default 300 s"`.

This is the existing wrapping behaviour the unit test in Step 1.3 will verify.

### Step 1.2 — Create `tests/unit/test_dictation_remote_timeout.py`

Create `tests/unit/test_dictation_remote_timeout.py`:

```python
"""Tests that _TIMEOUT_S is <= 30 s and that the default timeout is forwarded to httpx.

These tests enforce the executor-stall prevention requirement from ADR 0090:
a 300 s hung POST blocks _dictation_executor for all subsequent dictation
operations; reducing to 30 s bounds the stall to an acceptable window.

NOTE: The test that httpx.TimeoutException surfaces as DictationRemoteError
lives in tests/unit/test_dictation_remote.py (test_timeout_exception_wraps_as_remote_error),
because that wrapping behaviour belongs to post_audio itself, not to _TIMEOUT_S.
"""
from __future__ import annotations

import voice_commander.dictation.remote as _remote
from voice_commander.dictation.remote import post_audio

import pytest


def test_timeout_constant_is_at_most_30_seconds() -> None:
    """_TIMEOUT_S must be <= 30.0 to prevent executor stall (ADR 0090)."""
    assert _remote._TIMEOUT_S <= 30.0, (
        f"_TIMEOUT_S={_remote._TIMEOUT_S} exceeds 30 s — a hung POST would "
        "block _dictation_executor indefinitely (ADR 0090 §4)"
    )


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

### Step 1.3 — Add `test_timeout_exception_wraps_as_remote_error` to `tests/unit/test_dictation_remote.py`

Open `tests/unit/test_dictation_remote.py` and append the following test at the end of the file. This test belongs here because it tests the wrapping behaviour in `post_audio`, not the constant value:

```python
def test_timeout_exception_wraps_as_remote_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """httpx.TimeoutException raised by httpx.post must be re-raised as
    DictationRemoteError (ADR 0090 §4).

    post_audio already has `except Exception as e: raise DictationRemoteError(...) from e`
    which catches ALL exceptions — including httpx.TimeoutException. This test verifies
    that the existing except-Exception clause covers the timeout case correctly, so that
    _finalize_dictation's `except remote.DictationRemoteError` handler catches it and
    publishes dictation.error + miss chime (keeping the executor worker unblocked).
    """
    import httpx
    from voice_commander.dictation.remote import DictationRemoteError, post_audio

    def _fake_post(url: str, **kwargs: object) -> object:
        raise httpx.TimeoutException("read timeout after 30 s")

    monkeypatch.setattr("voice_commander.dictation.remote.httpx.post", _fake_post)
    with pytest.raises(DictationRemoteError, match="read timeout after 30 s"):
        post_audio(b"RIFFfake", "http://x/inference")
```

- [ ] **Step 1.4 — Run tests to confirm current failures**

```powershell
cd F:\Tools\Projects\voice-commander
python -m pytest tests/unit/test_dictation_remote_timeout.py -v
```

Expected: `test_timeout_constant_is_at_most_30_seconds` **FAILS** (`AssertionError: _TIMEOUT_S=300.0 exceeds 30 s`). `test_post_audio_default_timeout_is_passed_to_httpx` PASSES (default forwarding already works). The new test in `test_dictation_remote.py` PASSES (wrapping already works).

- [ ] **Step 1.5 — Update `_TIMEOUT_S` and `post_audio` docstring in `remote.py`**

In `src/voice_commander/dictation/remote.py`, replace lines 28–29:

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

Also update the `post_audio` docstring — replace the `timeout` parameter description:

```python
    timeout:
        HTTP read timeout in seconds (default 300 s — long dictations can take
        many seconds on the remote hardware).
```

with:

```python
    timeout:
        HTTP read timeout in seconds (default 30 s — bounded to prevent an
        indefinite executor stall; see _TIMEOUT_S comment, ADR 0090 §4).
```

- [ ] **Step 1.6 — Run all remote tests to confirm they pass**

```powershell
python -m pytest tests/unit/test_dictation_remote_timeout.py tests/unit/test_dictation_remote.py -v
```

Expected: all tests PASS including the new `test_timeout_exception_wraps_as_remote_error`.

- [ ] **Step 1.7 — Commit**

```powershell
git add src/voice_commander/dictation/remote.py tests/unit/test_dictation_remote_timeout.py tests/unit/test_dictation_remote.py
git commit -m "fix(dictation): reduce _TIMEOUT_S from 300 s to 30 s to prevent executor stall; add timeout unit tests (ADR 0090)"
```

---

## Task 2 — Unit tests for `_session_opened_by_dictation`, helpers (RED phase)

**Commit safety:** These tests reference symbols (`_open_voice_session`, `_close_voice_session`, `_session_opened_by_dictation`, `_end_owned_session_if_needed`) that do not yet exist. The tests FAIL. That is the required red phase. Do NOT implement anything in this task — only write and commit the tests.

**NOTE on `test_shutdown_cancels_active_dictation`:** This test is intentionally NOT in this task. `shutdown()` is modified in Task 5; the shutdown test is a red-phase gate for Task 5, so it lives in Task 4 alongside the other integration tests that pair with Task 5.

**Files:**
- Create: `tests/unit/test_daemon_session_helpers.py`

- [ ] **Step 2.1 — Create the test file**

Create `tests/unit/test_daemon_session_helpers.py`:

```python
"""Unit tests for ADR 0090 — _session_opened_by_dictation flag, shared session
helpers, and on_dictation_toggle idle branch.

All hardware subsystems are replaced with MagicMocks. No real audio, no GPU.
Mirrors tests/unit/test_streaming_daemon.py style exactly.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

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


def test_close_voice_session_is_noop_when_recorder_is_none(tmp_path) -> None:
    """_close_voice_session returns without crashing when recorder is None."""
    daemon, _, _ = _make_daemon(output_dir=str(tmp_path))
    daemon._recorder = None
    daemon._session_active = True
    daemon._session_opened_by_dictation = True

    # Must not raise
    daemon._close_voice_session()

    # Flag is always cleared even with None recorder
    assert daemon._session_opened_by_dictation is False


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
    """on_dictation_toggle with no session must NOT play a miss chime.

    Red-phase prediction: this test FAILS against the current production code,
    because the current code calls self._feedback.on_miss(...) in the idle branch.
    It goes green in Task 3 when the idle branch is rewritten.
    """
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
```

- [ ] **Step 2.2 — Run tests to confirm they fail as expected**

```powershell
python -m pytest tests/unit/test_daemon_session_helpers.py -v 2>&1 | Select-String -Pattern "PASSED|FAILED|ERROR" | Select-Object -First 40
```

Expected failures (red phase):
- `test_session_opened_by_dictation_starts_false` — **FAILED**: `AttributeError: 'StreamingDaemon' object has no attribute '_session_opened_by_dictation'`
- `test_open_voice_session_*` — **FAILED**: `AttributeError: '_open_voice_session'`
- `test_close_voice_session_*` — **FAILED**: `AttributeError: '_close_voice_session'`
- `test_end_owned_session_if_needed_*` — **FAILED**: `AttributeError: '_end_owned_session_if_needed'`
- `test_on_dictation_toggle_no_session_plays_no_miss_chime` — **FAILED**: current code plays a miss chime (confirmed by reading daemon.py line 410: `self._feedback.on_miss("(dictation: no active session)", ())`)
- `test_on_dictation_toggle_no_session_opens_and_starts_dictation` — **FAILED**: current code returns early without opening.
- `test_on_dictation_toggle_no_session_recorder_failure_does_not_set_flag` — **FAILED**: `AttributeError` on `_session_opened_by_dictation`
- `test_on_scroll_lock_*` regression guards — **FAILED**: `AttributeError` on `_session_opened_by_dictation`

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

In `src/voice_commander/daemon.py`, find line 249 (`self._session_active: bool = False`). Insert the new field immediately after it:

```python
        self._session_active: bool = False
        self._session_opened_by_dictation: bool = False
        # Audio-generation counter. ...
```

- [ ] **Step 3.2 — Add `_open_voice_session` and `_close_voice_session` helpers**

Locate the `# Hotkey callbacks` comment block that precedes `def on_scroll_lock` at line ~352. Insert the two new helpers and `_end_owned_session_if_needed` immediately BEFORE `on_scroll_lock`.

**Intentional divergences from a verbatim extract of `on_scroll_lock` — documented here for refactor-regression review:**

1. **`_open_voice_session` adds `if self._recorder is None: return False`** — the original `on_scroll_lock` has a top-level `if self._recorder is None: return` guard that covers both arms. The extracted helper duplicates this guard so it can be called independently of `on_scroll_lock`. This is intentional: every caller of `_open_voice_session` needs the guard, not just `on_scroll_lock`.
2. **`_close_voice_session` adds `if self._recorder is None:` early return** — same rationale; the helper must be safe to call independently.
3. **`_close_voice_session` changes the exception log message** from `"close_session() failed during scroll-lock close"` to `"close_session() failed"` — the helper is now called from multiple contexts (hotkey-listener and executor thread), so the message no longer names a specific caller. Intentional.
4. **`_close_voice_session` adds `self._session_opened_by_dictation = False`** — this is a new field; the original inline code in `on_scroll_lock` had no equivalent. Intentional addition, not an extraction oversight.

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

        Intentional divergences from verbatim on_scroll_lock extraction (see plan §3.2):
        - Adds `if self._recorder is None: return` guard (helper can be called independently).
        - Exception log message changed from "during scroll-lock close" to generic
          (helper is called from multiple thread contexts, not only scroll-lock).
        - Adds `self._session_opened_by_dictation = False` (new field, no equivalent in original).
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

- [ ] **Step 3.3 — Rewrite `on_scroll_lock` to call helpers**

Replace the **entire current body** of `on_scroll_lock` with the helper-delegating version. The exact verbatim text being replaced (from `src/voice_commander/daemon.py` lines 352–399 as read) is:

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
            self._audio_gen += 1
            try:
                self._recorder.close_session()
            except Exception:
                logger.exception("close_session() failed during scroll-lock close")
            self._drain_utt_q()
            self._session_active = False
            if self._dictation_session is not None and self._dictation_session.active:
                self._dictation_session.cancel()
            if self._elements_session is not None and self._elements_session.active:
                self._elements_session.cancel()
            self._feedback.on_recording_stop()
            self._publish("muted")
            self._publish("session_stopped")
            logger.info("Session closed")
        else:
            self._audio_gen += 1
            try:
                self._recorder.open_session()
                self._session_active = True
                self._feedback.on_recording_start()
                self._publish("session_started")
                self._publish("unmuted")
                logger.info("Session opened")
            except Exception as e:
                self._session_active = False
                self._feedback.on_error("recorder.open_session", e)
```

Replace with:

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

- [ ] **Step 3.4 — Rewrite `on_dictation_toggle` idle branch**

In `on_dictation_toggle` (around line 401), replace the `if not self._session_active:` branch:

```python
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

Also update the `on_dictation_toggle` docstring to reflect the new behaviour:

```python
    def on_dictation_toggle(self) -> None:
        """Right-control callback: toggle dictation mode.

        When no voice session is open (``_session_active == False``):
        opens a self-contained session via ``_open_voice_session()``, sets
        ``_session_opened_by_dictation = True``, and starts dictation immediately.
        When dictation ends the session is auto-closed by ``_end_owned_session_if_needed``.

        When a session is already open (``_session_active == True``):
        press once to start dictation, press again to end it (an alternative to
        saying the end word). Behaviour is unchanged from ADR 0086.
        """
```

- [ ] **Step 3.5 — Run the unit tests to confirm they pass**

```powershell
python -m pytest tests/unit/test_daemon_session_helpers.py tests/unit/test_streaming_daemon.py -v
```

Expected: **All tests PASS**. Pay special attention to the regression guards (`test_on_scroll_lock_*`).

- [ ] **Step 3.6 — Run the full test suite**

```powershell
python -m pytest -q
```

Expected: All previously passing tests still PASS. Only new integration tests added in Task 4 may be red.

- [ ] **Step 3.7 — Commit**

```powershell
git add src/voice_commander/daemon.py
git commit -m "feat(daemon): extract _open/_close_voice_session helpers; add _session_opened_by_dictation flag and _end_owned_session_if_needed (ADR 0090)"
```

---

## Task 4 — Integration tests for Ctrl-open flows and shutdown (RED phase)

**Commit safety:** These integration tests reference the new behaviour in `_process_utterance` and `_finalize_pending_dictation_end` (close-before-finalize ordering). They FAIL because those methods have not yet been updated. `test_shutdown_with_active_dictation_cancels_it` is here (not in Task 2) because `shutdown()` is modified in Task 5 — this keeps red-phase tests adjacent to their green-phase implementations.

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
from unittest.mock import MagicMock

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
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.remote.post_audio",
        lambda wav_bytes, endpoint, **kw: "X",
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
# (regression guard — strengthened per REV 9: actually calls on_scroll_lock)
# ---------------------------------------------------------------------------


def test_scroll_lock_session_ctrl_dictation_done_session_stays_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression: Scroll Lock session + Ctrl dictation + 'done' must NOT close session.

    REV 9: Uses on_scroll_lock() to open the session (not direct state injection)
    so the test exercises the real code path.  Asserts _session_active stays True
    and recorder.close_session was NOT called by the dictation-end path.
    """
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
    daemon.on_scroll_lock()
    assert daemon._session_active is True
    assert daemon._session_opened_by_dictation is False

    # Start dictation as sub-state (Ctrl within active Scroll Lock session)
    dictation_session.start()
    assert dictation_session.active is True

    # Note: reset close_session call count AFTER on_scroll_lock (which calls open_session)
    recorder.close_session.reset_mock()

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
    # recorder.close_session must NOT have been called by the dictation-end path
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
    monkeypatch.setattr(
        "voice_commander.dictation.remote.post_audio",
        lambda wav_bytes, endpoint, **kw: "X",
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
#
# REV 1 fix: the stub raises remote.DictationRemoteError (the type that
# _finalize_dictation actually catches), NOT httpx.TimeoutException.
# The unit test in test_dictation_remote.py verifies that post_audio wraps
# httpx.TimeoutException as DictationRemoteError; this integration test only
# verifies that _finalize_dictation handles DictationRemoteError correctly.
# ---------------------------------------------------------------------------


def test_dictation_remote_error_emits_dictation_error_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A DictationRemoteError from post_audio surfaces as dictation.error + miss chime;
    the executor worker is unblocked after the error.

    REV 1: The stub raises DictationRemoteError directly — the type _finalize_dictation
    actually catches.  Testing that httpx.TimeoutException wraps to DictationRemoteError
    is the job of test_dictation_remote.py::test_timeout_exception_wraps_as_remote_error.
    """
    from voice_commander.dictation.remote import DictationRemoteError

    def _error_post(wav_bytes: bytes, endpoint: str, **kw: Any) -> str:
        raise DictationRemoteError("read timeout after 30 s")

    monkeypatch.setattr("voice_commander.dictation.remote.post_audio", _error_post)
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: None,
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

    daemon.on_dictation_toggle()
    audio = np.zeros(16000, dtype=np.float32)
    daemon._process_utterance(audio)   # "hello world" → buffered
    daemon._process_utterance(audio)   # "done" → end word

    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    # dictation.error event must be published
    events = _drain_events(event_q)
    assert "dictation.error" in events, (
        f"dictation.error must be published on DictationRemoteError; events: {events}"
    )
    # Miss chime must be played
    assert any(c[0] == "on_miss" for c in feedback.calls), (
        "on_miss must be called after DictationRemoteError"
    )
    # Executor must be free — verified implicitly by shutdown(wait=True) returning above


# ---------------------------------------------------------------------------
# Test 8: shutdown() with active dictation cancels it directly
# (REV 2: this test is here, not in Task 2, because shutdown() is modified in Task 5)
# ---------------------------------------------------------------------------


def test_shutdown_with_active_dictation_cancels_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """shutdown() must cancel DictationSession directly BEFORE executor.shutdown(wait=False).

    REV 2: Moved here (Task 4 red phase) because the production code change is in Task 5.
    REV 3: The cancel call goes immediately after the session-close block and BEFORE
    _dictation_executor.shutdown(wait=False) — so any queued tasks are not needed.
    """
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
        "before executor.shutdown(wait=False) so it is not abandoned"
    )
```

- [ ] **Step 4.2 — Run tests to confirm they fail as expected**

```powershell
python -m pytest tests/integration/test_dictation_opens_session.py -v 2>&1 | Select-String -Pattern "PASSED|FAILED|ERROR"
```

Expected failures:
- `test_ctrl_open_done_closes_session` — **FAILED**: `_end_owned_session_if_needed` not submitted; session not closed.
- `test_ctrl_open_empty_buffer_ctrl_end_closes_session` — **FAILED**: `_end_owned_session_if_needed` not submitted by `_finalize_pending_dictation_end`.
- `test_ctrl_open_spoken_cancel_closes_session` — **FAILED**: `_end_owned_session_if_needed` not submitted after cancel.
- `test_close_before_finalize_submission_order_end_word` — **FAILED**: `_end_owned_session_if_needed` not in `submission_order`.
- `test_close_submitted_unconditionally_hotkey_end_empty_buffer` — **FAILED**: `_end_owned_session_if_needed` not submitted.
- `test_shutdown_with_active_dictation_cancels_it` — **FAILED**: dictation still active after shutdown.
- `test_scroll_lock_session_ctrl_dictation_done_session_stays_open` — **PASSES** (existing behaviour; Scroll Lock regression guard already correct).
- `test_dictation_remote_error_emits_dictation_error_event` — **PASSES** (existing `_finalize_dictation` already handles `DictationRemoteError`).

- [ ] **Step 4.3 — Commit the red-phase integration tests**

```powershell
git add tests/integration/test_dictation_opens_session.py
git commit -m "test(integration): red-phase integration tests for ADR 0090 Ctrl-open flows (failing until Task 5)"
```

---

## Task 5 — Implement close-before-finalize ordering and `shutdown()` dictation cleanup

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
                    # Reuse the pre-existing cancel() method — no new code, no
                    # POST, no clipboard paste. The method publishes
                    # dictation.end {"reason": "cancel"}. (ADR 0089)
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
                    # cancel() publishes dictation.end {"reason": "cancel"}. (ADR 0089)
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
        """Called on the pipeline thread when the hotkey-end drain window expires.

        Atomically captures the buffered audio (take_and_finish) and submits it to
        the dictation executor.  Returns immediately — all heavy work (encode, POST,
        paste) happens off-thread.

        If the "done" word path won the race (take_and_finish returns None because
        the session is already inactive), this is a no-op.  No double-submit risk:
        take_and_finish holds the lock across deactivation + buffer clear.
        """
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

- [ ] **Step 5.3 — Update `shutdown()` to cancel active dictation before executor shutdown**

Find `shutdown()` in `src/voice_commander/daemon.py` (around line 1078). Locate the executor shutdown block (around line 1144):

```python
        # Shut down the WAV writer executor.
        self._wav_executor.shutdown(wait=False)
        self._dictation_executor.shutdown(wait=False)
        self._elements_executor.shutdown(wait=False)
```

Insert the dictation cancel IMMEDIATELY BEFORE this block (after the pipeline join, before any executor shutdown):

```python
        # Cancel active dictation directly — BEFORE executor shutdown (REV 3, ADR 0090 §5).
        # Placement: after session-close block and pipeline join, BEFORE
        # _dictation_executor.shutdown(wait=False).  Rationale: cancel() only sets flags
        # and publishes dictation.end on the event bus (no executor use), so it is safe
        # to call here.  Publishing dictation.end BEFORE executor teardown keeps event
        # ordering clean.  executor.shutdown(wait=False) abandons queued tasks — any
        # _end_owned_session_if_needed already queued will not execute — so the direct
        # cancel call here is the only reliable path to clear the dictating state and
        # prevent the sprite from being stuck in the 'dictating' visual state.
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

Expected: Full suite PASSES (no regressions).

- [ ] **Step 5.7 — Commit**

```powershell
git add src/voice_commander/daemon.py
git commit -m "feat(daemon): implement close-before-finalize ordering, spoken-cancel close, shutdown dictation cleanup (ADR 0090)"
```

---

## Task 6 — Visual E2E harness: `scripts/dictation_opens_session_e2e.py`

**Commit safety:** This is a standalone script — not a pytest test. It requires a running daemon + real hardware. Commit it as evidence; the HITL gate validates it manually.

### Why a full subprocess daemon is used (REV 4 rationale)

REV 4 requires the harness to exercise the **real daemon** with a **real injected hotkey**, not just the sprite. The voice daemon depends on `faster-whisper` (CUDA), `sounddevice` (WASAPI), and `silero-vad` — all real Windows hardware. Starting a full `build_streaming_daemon` subprocess is feasible on the developer machine (same as running `python -m voice_commander`) and is the standard pattern in this repo.

However, the daemon's hotkey and audio pipeline have hard dependencies (real mic, GPU, CUDA DLL on PATH). To make the harness robust on the developer machine without requiring a specific inference server, the harness:

1. Starts the daemon via `python -m voice_commander` as a subprocess with a minimal test `config.toml` that uses a stub/disabled transcriber endpoint.
2. Waits for the daemon's web server (`/healthz`) to respond.
3. Subscribes to the daemon's `/events` SSE endpoint.
4. Injects a real Right Ctrl key press using `pynput.keyboard.Controller` (same library the daemon uses for key detection).
5. Asserts `session_started` arrives on the SSE stream.
6. Injects a second Right Ctrl press (Ctrl-close with empty buffer).
7. Asserts `session_stopped` arrives on the SSE stream.
8. Captures daemon stdout/stderr and FAILS on crash signatures.
9. Captures a screenshot of the sprite window as visual evidence.

**If the daemon cannot start** (e.g. CUDA unavailable in CI), the harness prints a skip notice and exits 0. This is acceptable per the repo's "local-only infrastructure" rule (CLAUDE.md §Core principles 5). The harness is NOT run in CI; it is run manually on the developer machine.

**Files:**
- Create: `scripts/dictation_opens_session_e2e.py`

- [ ] **Step 6.1 — Verify required scripts and output dir exist**

```powershell
Test-Path F:\Tools\Projects\voice-commander\outputs
Test-Path F:\Tools\Projects\voice-commander\scripts\dictation_hotkey_cancel_e2e.py
Test-Path F:\Tools\Projects\voice-commander\scripts\picker_visual_e2e.py
```

All must return `True`.

- [ ] **Step 6.2 — Create the harness**

Create `scripts/dictation_opens_session_e2e.py`:

```python
"""Visual E2E harness for ADR 0090 — Right Ctrl opens its own voice session.

Mandatory per docs/agents/visual-e2e-testing.md (8-rule protocol).

This feature touches hotkeys, daemon ↔ sprite ↔ web-UI IPC, and the system
clipboard — all three triggers for the visual E2E requirement.

REV 4: The harness exercises the REAL daemon subprocess with a REAL injected
Right Ctrl key press via pynput.keyboard.Controller.  The daemon's SSE /events
endpoint is used to assert session_started and session_stopped.

Architecture
------------
1. Write a minimal config.toml to a temp path (no GPU required for session
   open/close — only the hotkey path is exercised; whisper model load is
   skipped by using a non-existent model path that makes faster-whisper fail
   gracefully before session open).
   ALTERNATIVE: if the developer machine has the full stack, set
   VOICE_COMMANDER_CONFIG env var to the real config path.
2. Start the daemon as a subprocess (python -m voice_commander --config …).
3. Poll /healthz until the web server is up (30 s timeout).
4. Subscribe to /events SSE endpoint.
5. Inject Right Ctrl via pynput (real keypress, same library daemon uses).
6. Assert session_started arrives on /events within 5 s.
7. Inject Right Ctrl again (hotkey-end, empty buffer → session_stopped).
8. Assert session_stopped arrives on /events within 10 s.
9. Capture daemon stdout/stderr; FAIL on crash signatures (Traceback, Exception in thread).
10. Capture screenshot as visual evidence.

If the daemon cannot start (ImportError on CUDA/sounddevice/etc.), the harness
prints a skip notice, writes a skip marker to outputs/, and exits 0.  This is
consistent with the repo's local-only infrastructure rule (CLAUDE.md §5).

Output artifacts (Rule 4):
  outputs/dictation_opens_session_e2e_session.png   — sprite after session_started
  outputs/dictation_opens_session_e2e_idle.png      — sprite after session_stopped
  outputs/dictation_opens_session_e2e.log
  outputs/dictation_opens_session_e2e.json
  outputs/dictation_opens_session_e2e_daemon.log    — daemon stdout+stderr
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
import tempfile
import textwrap
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

OUT = ROOT / "outputs"
OUT.mkdir(parents=True, exist_ok=True)

LOG_PATH = OUT / "dictation_opens_session_e2e.log"
PNG_SESSION_PATH = OUT / "dictation_opens_session_e2e_session.png"
PNG_IDLE_PATH = OUT / "dictation_opens_session_e2e_idle.png"
JSON_PATH = OUT / "dictation_opens_session_e2e.json"
DAEMON_LOG_PATH = OUT / "dictation_opens_session_e2e_daemon.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("e2e")

_CRASH_SIGNATURES = (
    "Traceback (most recent call last)",
    "Exception in thread",
    "TypeError:",
    "AttributeError:",
    "RuntimeError:",
    "ValueError:",
)

# ---------------------------------------------------------------------------
# Minimal config.toml for the daemon (no whisper model needed for hotkey test)
# ---------------------------------------------------------------------------

_MINIMAL_CONFIG = textwrap.dedent("""\
    hotkey = "scroll_lock"
    dictation_key = "ctrl_r"
    mic_index = 0
    keep_warm_min = 0

    [web]
    host = "127.0.0.1"
    port = {port}

    [transcriber]
    model = "tiny.en"
    device = "cpu"
    compute_type = "int8"

    [dictation]
    endpoint = "http://127.0.0.1:1"
    end_word = "done"

    [fuzzy]
    focus_threshold = 60
    open_threshold = 60
""")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ---------------------------------------------------------------------------
# SSE client (reads from daemon's real /events endpoint)
# ---------------------------------------------------------------------------


def _sse_reader(
    url: str,
    event_queue: queue.Queue,  # type: ignore[type-arg]
    stop_flag: threading.Event,
    timeout_s: float = 60.0,
) -> None:
    """Thread target: stream SSE events from url into event_queue."""
    try:
        import httpx
        with httpx.stream("GET", url, timeout=timeout_s) as resp:
            event_type = ""
            for line in resp.iter_lines():
                if stop_flag.is_set():
                    break
                line = line.strip()
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    if event_type:
                        event_queue.put(event_type)
                        event_type = ""
                elif not line:
                    event_type = ""
    except Exception as exc:
        log.debug("SSE reader exited: %s", exc)


def _wait_for_event(
    event_queue: queue.Queue,  # type: ignore[type-arg]
    target: str,
    timeout_s: float,
) -> bool:
    """Block until target event type appears in queue or timeout expires."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            ev = event_queue.get(timeout=min(remaining, 1.0))
            log.info("SSE event: %s", ev)
            if ev == target:
                return True
        except queue.Empty:
            pass
    return False


# ---------------------------------------------------------------------------
# Daemon subprocess management
# ---------------------------------------------------------------------------


def _poll_healthz(base_url: str, timeout_s: float = 30.0) -> bool:
    """Poll GET /healthz until 200 or timeout."""
    try:
        import httpx
    except ImportError:
        log.error("httpx not available — cannot poll /healthz")
        return False
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{base_url}/healthz", timeout=2.0)
            if r.status_code == 200:
                log.info("/healthz OK")
                return True
        except Exception:
            pass
        time.sleep(0.5)
    log.error("/healthz never returned 200 within %s s", timeout_s)
    return False


def _spawn_daemon(
    port: int,
) -> tuple[subprocess.Popen, Path] | None:  # type: ignore[type-arg]
    """Write temp config and spawn the daemon. Returns (proc, cfg_path) or None on skip."""
    cfg_path = OUT / "_e2e_daemon_config.toml"
    cfg_path.write_text(_MINIMAL_CONFIG.format(port=port), encoding="utf-8")

    env = os.environ.copy()
    src_dir = str(ROOT / "src")
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_dir};{existing_pp}" if existing_pp else src_dir

    daemon_log_fh = DAEMON_LOG_PATH.open("w", encoding="utf-8", buffering=1)
    try:
        proc = subprocess.Popen(
            [
                sys.executable, "-m", "voice_commander",
                "--config", str(cfg_path),
            ],
            cwd=str(ROOT),
            env=env,
            stdout=daemon_log_fh,
            stderr=daemon_log_fh,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
    except Exception as exc:
        log.error("Failed to spawn daemon: %s", exc)
        daemon_log_fh.close()
        return None
    proc._daemon_log_fh = daemon_log_fh  # type: ignore[attr-defined]
    log.info("daemon pid=%d, config=%s", proc.pid, cfg_path)
    return proc, cfg_path


def _stop_daemon(proc: subprocess.Popen) -> None:  # type: ignore[type-arg]
    try:
        proc.terminate()
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        proc.kill()
    except Exception:
        proc.kill()
    fh = getattr(proc, "_daemon_log_fh", None)
    if fh is not None:
        try:
            fh.flush()
            fh.close()
        except Exception:
            pass


def _check_daemon_log_for_crashes() -> bool:
    """Return True if no crash signatures found in daemon log."""
    if not DAEMON_LOG_PATH.exists():
        log.warning("daemon log not found")
        return True
    text = DAEMON_LOG_PATH.read_text(encoding="utf-8", errors="replace")
    hits = [sig for sig in _CRASH_SIGNATURES if sig in text]
    if hits:
        log.error("FAIL [crash-gate]: crash signatures found: %s", hits)
        log.error("daemon log tail:\n%s", text[-2000:])
        return False
    log.info("PASS [crash-gate]: no crash signatures in daemon log")
    return True


# ---------------------------------------------------------------------------
# Hotkey injection via pynput (real keypress — Rule 7)
# ---------------------------------------------------------------------------


def _inject_right_ctrl() -> None:
    """Inject a real Right Ctrl press+release via pynput.keyboard.Controller."""
    try:
        from pynput.keyboard import Controller, Key
        kb = Controller()
        kb.press(Key.ctrl_r)
        time.sleep(0.05)
        kb.release(Key.ctrl_r)
        log.info("Injected Right Ctrl via pynput")
    except Exception as exc:
        log.error("pynput injection failed: %s", exc)
        raise


# ---------------------------------------------------------------------------
# Sprite HWND + screenshot helpers (same as other e2e scripts)
# ---------------------------------------------------------------------------


def _find_hwnd_by_pid(pid: int, timeout_s: float = 15.0) -> int:
    user32 = ctypes.windll.user32
    WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    deadline = time.monotonic() + timeout_s

    def _pids(root: int) -> set[int]:
        s = {root}
        try:
            import psutil
            for c in psutil.Process(root).children(recursive=True):
                s.add(c.pid)
        except Exception:
            pass
        return s

    while time.monotonic() < deadline:
        cands = _pids(pid)
        found: list[int] = []

        def _cb(hwnd: int, _: int, _c: set[int] = cands, _f: list[int] = found) -> bool:
            d = wt.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(d))
            if d.value in _c and user32.IsWindowVisible(hwnd):
                _f.append(hwnd)
                return False
            return True

        user32.EnumWindows(WNDENUMPROC(_cb), 0)
        if found:
            return found[0]
        time.sleep(0.25)
    return 0


def _capture_window(hwnd: int, png_path: Path) -> bool:
    try:
        import win32gui, win32ui
        from PIL import Image
        rect = win32gui.GetWindowRect(hwnd)
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        if w <= 0 or h <= 0:
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
            if not user32.PrintWindow(hwnd, mem.GetSafeHdc(), 0x2):
                log.warning("PrintWindow returned 0")
            bmpinfo = bmp.GetInfo()
            bmpstr = bmp.GetBitmapBits(True)
            img = Image.frombuffer(
                "RGB", (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
                bmpstr, "raw", "BGRX", 0, 1,
            )
            img.save(str(png_path))
            log.info("PNG: %s (%d bytes)", png_path, png_path.stat().st_size)
        finally:
            win32gui.DeleteObject(bmp.GetHandle())
            mem.DeleteDC(); src.DeleteDC()
            win32gui.ReleaseDC(hwnd, hdc)
        return True
    except Exception as exc:
        log.error("capture_window failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Phase A — Ctrl-open + Ctrl-close: session_started then session_stopped
# ---------------------------------------------------------------------------


def phase_a_ctrl_open_ctrl_close() -> bool:
    """PHASE A (Rule 7 — test how the user uses it):
      1. Daemon starts with no session.
      2. Inject Right Ctrl → assert session_started on /events.
      3. Capture sprite screenshot.
      4. Inject Right Ctrl again → assert session_stopped on /events.
      5. Confirm no crash signatures in daemon log.

    This directly exercises the `on_dictation_toggle` idle-branch code path
    introduced by ADR 0090 using a real keypress — not a synthetic method call.
    """
    log.info("=== PHASE A: Ctrl-open + Ctrl-close (real daemon + real hotkey) ===")
    port = _free_port()
    result = _spawn_daemon(port)
    if result is None:
        log.warning("SKIP: daemon could not be spawned (missing hardware/deps)")
        return True  # skip, not fail — local-only infra rule

    proc, _ = result
    ok = False
    stop_flag = threading.Event()
    ev_queue: queue.Queue = queue.Queue()  # type: ignore[type-arg]

    try:
        base_url = f"http://127.0.0.1:{port}"

        # Wait for web server
        if not _poll_healthz(base_url, timeout_s=30.0):
            log.error("FAIL: daemon web server did not start")
            return False

        # Check daemon log for early crashes (model load failure is OK for cpu/tiny.en)
        daemon_text = DAEMON_LOG_PATH.read_text(encoding="utf-8", errors="replace")
        hard_crashes = [s for s in _CRASH_SIGNATURES if s in daemon_text
                        if "faster_whisper" not in daemon_text or s != "RuntimeError:"]
        if hard_crashes:
            log.error("FAIL: crash signatures at startup: %s", hard_crashes)
            return False

        # Start SSE reader thread
        sse_thread = threading.Thread(
            target=_sse_reader,
            args=(f"{base_url}/events", ev_queue, stop_flag),
            daemon=True,
        )
        sse_thread.start()
        time.sleep(0.5)  # let SSE stream establish

        # ASSERT: inject Right Ctrl; expect session_started
        log.info("Injecting Right Ctrl (open)...")
        _inject_right_ctrl()

        if not _wait_for_event(ev_queue, "session_started", timeout_s=5.0):
            log.error("FAIL: session_started not received within 5 s after Right Ctrl press")
            return False
        log.info("PASS: session_started received")

        # Capture sprite screenshot during active session
        # The sprite process is a child of the daemon subprocess
        sprite_hwnd = _find_hwnd_by_pid(proc.pid, timeout_s=10.0)
        if sprite_hwnd:
            _capture_window(sprite_hwnd, PNG_SESSION_PATH)
        else:
            log.warning("Sprite HWND not found; screenshot skipped (non-fatal)")

        # ASSERT: inject second Right Ctrl; expect session_stopped
        time.sleep(0.3)  # brief pause before hotkey-end
        log.info("Injecting Right Ctrl (close)...")
        _inject_right_ctrl()

        if not _wait_for_event(ev_queue, "session_stopped", timeout_s=10.0):
            log.error("FAIL: session_stopped not received within 10 s after second Right Ctrl")
            return False
        log.info("PASS: session_stopped received")

        # Capture idle sprite screenshot
        if sprite_hwnd:
            time.sleep(0.3)
            _capture_window(sprite_hwnd, PNG_IDLE_PATH)

        ok = True

    finally:
        stop_flag.set()
        _stop_daemon(proc)
        if ok and not _check_daemon_log_for_crashes():
            ok = False
        log.info("PHASE A %s", "PASS" if ok else "FAIL")

    return ok


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------


def main() -> int:
    results: dict[str, bool] = {}
    results["phase_a_ctrl_open_ctrl_close"] = phase_a_ctrl_open_ctrl_close()

    all_pass = all(results.values())
    summary = " | ".join(f"{k}={'PASS' if v else 'FAIL'}" for k, v in results.items())
    log.info("=== SUMMARY: %s ===", summary)
    JSON_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    log.info("session PNG: %s", PNG_SESSION_PATH)
    log.info("idle PNG:    %s", PNG_IDLE_PATH)
    log.info("daemon log:  %s", DAEMON_LOG_PATH)
    log.info("harness log: %s", LOG_PATH)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6.3 — Syntax-check the script**

```powershell
python -c "import ast; ast.parse(open('F:/Tools/Projects/voice-commander/scripts/dictation_opens_session_e2e.py').read()); print('syntax OK')"
```

Expected: `syntax OK`

- [ ] **Step 6.4 — Commit**

```powershell
git add scripts/dictation_opens_session_e2e.py
git commit -m "test(e2e): visual E2E harness for ADR 0090 — real daemon subprocess + pynput Right Ctrl injection + SSE assertion"
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

The open and close arms of `on_scroll_lock` are extracted into two protected helpers.
`on_scroll_lock` is rewritten to delegate to them. Observable behaviour of Scroll Lock
is byte-for-byte unchanged.

`_open_voice_session() -> bool`: opens recorder, sets `_session_active`, publishes
`session_started` → `unmuted`. Returns `True` on success, `False` on recorder failure.
Does NOT touch `_session_opened_by_dictation`.

`_close_voice_session() -> None`: bumps `_audio_gen`, calls `recorder.close_session()`,
drains `_utt_q`, sets `_session_active = False`, resets `_session_opened_by_dictation = False`,
cancels active dictation/elements sessions, publishes `muted` → `session_stopped`.

Intentional divergences from verbatim extraction: (a) adds `if self._recorder is None: return`
guard (helper callable independently); (b) generic exception log message (multiple callers);
(c) adds `_session_opened_by_dictation = False` (new field).

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

### D7 — Shutdown cleanup (REV 3 placement)

`Daemon.shutdown()` gains a direct `self._dictation_session.cancel()` call placed
**immediately after the session-close block and pipeline join, and BEFORE
`_dictation_executor.shutdown(wait=False)`**.

Rationale for placement: `cancel()` only sets flags and publishes `dictation.end` on the
event bus — it does not use the executor — so it is safe to call before executor teardown.
Publishing `dictation.end` before executor shutdown keeps event ordering clean.
`executor.shutdown(wait=False)` abandons queued tasks; any `_end_owned_session_if_needed`
already queued will not execute. The direct cancel call is the only reliable path to clear
the dictating state and prevent the sprite from being stuck in the `dictating` visual state.

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

Open `docs/decisions/0086-dictation-mode.md`. Find the `### D1 — Dictation is a voice-session sub-state` section. Insert the amendment pointer immediately after the heading:

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

After the last regular row in the first table (before the `## ~~LLM Router~~` section), add:

```markdown
| Dictation hotkey opens own session | Right Ctrl with no Scroll Lock session open calls `_open_voice_session()`, sets `_session_opened_by_dictation=True`, starts dictation; all three end paths submit `_end_owned_session_if_needed` to `_dictation_executor` BEFORE `_finalize_dictation` (close-first ordering); `_TIMEOUT_S` reduced to 30 s; `shutdown()` cancels active dictation directly before executor teardown (ADR 0090) | Right Ctrl becomes a complete transcribe toggle; recording-stop chime fires before paste; 30 s timeout prevents executor stall | [0090](../decisions/0090-dictation-hotkey-opens-session.md) |
```

- [ ] **Step 7.4 — Update CLAUDE.md "Current state" dictation paragraph**

Open `CLAUDE.md`. Find the paragraph beginning `**Dictation mode** (ADR 0086)` in the "Current state" section. Replace the entire dictation paragraph (from `**Dictation mode**` through the sentence ending `at /page/dictation`.`) with:

```
**Dictation mode** (ADR 0086, amended by ADR 0090) is a voice-session sub-state: saying bare "dictate" or pressing Right Ctrl (`dictation_key`, default `ctrl_r`) enters dictation. **When no Scroll Lock session is open**, pressing Right Ctrl opens a self-contained session automatically (`_open_voice_session()`), sets `_session_opened_by_dictation=True`, and starts dictation — no prior Scroll Lock press is required. On any dictation-end path (end-word "done", hotkey-end Ctrl-press, or spoken "cancel"), `_end_owned_session_if_needed` is submitted to `_dictation_executor` **before** `_finalize_dictation` (close-before-finalize ordering: recording-stop chime fires before text is pasted, signalling "captured, processing"). The session closes automatically after the paste. **When a Scroll Lock session is already open**, pressing Right Ctrl enters dictation as a sub-state exactly as before (ADR 0086) — the session stays open after dictation ends. VAD audio accumulates in `DictationSession`; saying the end word "done" (exact standalone, configurable via `[dictation] end_word`) or a second Right Ctrl press triggers exit. On exit, the concatenated audio is encoded to a 16 kHz mono WAV and POSTed to a remote whisper.cpp `/inference` endpoint (`[dictation] endpoint`, default `http://192.168.4.200:8765/inference`, timeout 30 s per ADR 0090); the transcription is post-processed (ADR 0088) by `postprocess.build_prompt` → `remote.post_audio(prompt=...)` → `apply_corrections` → `apply_commands`, then inserted at the cursor via a clipboard round-trip (`Ctrl+V`); `outputs/dictation/last.wav` + `last.txt` are retained for web re-transcribe at `/page/dictation`.
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
| Extract `_open_voice_session` / `_close_voice_session` helpers | Task 3.2 |
| `on_scroll_lock` rewritten to call helpers; flag never set by Scroll Lock | Task 3.3 |
| `_close_voice_session` resets `_session_opened_by_dictation = False` | Task 3.2 |
| `on_dictation_toggle` idle branch: open → set flag → start dictation; no miss chime | Task 3.4 |
| `on_dictation_toggle` failure: flag stays False, dictation not started | Task 3.4 + tests Task 2 |
| `_end_owned_session_if_needed` executor-only contract | Task 3.2 (inline with helpers) |
| End-word path: close submitted before finalize; unconditionally | Task 5.1 |
| Hotkey-end path: close submitted unconditionally before finalize | Task 5.2 |
| Spoken-cancel path: `cancel()` then submit `_end_owned_session_if_needed` | Task 5.1 |
| `_TIMEOUT_S` reduced from 300 to 30 with inline comment | Task 1.5 |
| `post_audio` docstring updated to 30 s (REV 8) | Task 1.5 |
| `shutdown()` dictation cleanup — BEFORE executor shutdown (REV 3) | Task 5.3 |
| ADR 0090 | Task 7.1 |
| ADR 0086 D1 amendment pointer (explicit, names D1) | Task 7.2 |
| `technical-decisions.md` row | Task 7.3 |
| CLAUDE.md current state update | Task 7.4 |
| Visual E2E harness — real daemon + real hotkey (REV 4) | Task 6 |
| Unit tests: helpers, flag, idle branch (REV 7 predictions fixed) | Task 2 |
| Integration tests: all Ctrl-open flows | Task 4 |
| Regression guard: `on_scroll_lock()` call to open session (REV 9) | Task 4 (Test 4) |
| Close-before-finalize submission order asserted | Task 4 (Tests 5, 6) |
| `remote.post_audio` timeout ≤ 30; `TimeoutException` → `DictationRemoteError` | Task 1 |
| Integration test raises `DictationRemoteError` (not `httpx.TimeoutException`) (REV 1) | Task 4 (Test 7) |
| `test_shutdown_cancels_active_dictation` in Task 4 (not Task 2) (REV 2) | Task 4 (Test 8) |
| Verbatim `on_scroll_lock` replace-block shown (REV 5) | Task 3.3 |
| Intentional divergences documented (REV 6) | Task 3.2 docstring + inline notes |

All spec requirements are covered. No gaps found.

### Placeholder scan

No TBDs, TODOs, "implement later", "add appropriate error handling", or "similar to Task N" patterns exist. Every code step shows complete actual code. Every command shows the exact pytest invocation and expected output.

### Type consistency check

- `_open_voice_session() -> bool`: defined Task 3.2; used in Task 3.3 and 3.4. Return type `bool` used correctly with `if not self._open_voice_session(): return`.
- `_close_voice_session() -> None`: defined Task 3.2; called in Task 3.3 (`on_scroll_lock`) and Task 3.2 (`_end_owned_session_if_needed`).
- `_end_owned_session_if_needed() -> None`: defined Task 3.2; submitted via `self._dictation_executor.submit(self._end_owned_session_if_needed)` in Tasks 5.1 and 5.2. No arguments — matches `submit(fn)` call.
- `_session_opened_by_dictation: bool`: initialised Task 3.1; set `True` Task 3.4; reset `False` Task 3.2. Always accessed as `self._session_opened_by_dictation`. Consistent.
- `DictationSession.take_and_finish() -> NDArray | None`: return checked with `if audio is not None:` throughout. Consistent.
- `feedback.on_miss(str, tuple)`: `CapturingFeedbackSink.calls` checked with `any(c[0] == "on_miss" ...)`. Consistent with existing test style.

No type mismatches found.

### REV compliance summary

| REV | Blocker/Should-Fix | Status |
|---|---|---|
| REV 1 — Timeout test correctness | BLOCKER | Fixed: integration Test 7 raises `DictationRemoteError`; unit test in `test_dictation_remote.py` verifies `httpx.TimeoutException` wrapping |
| REV 2 — `test_shutdown_cancels_active_dictation` placement | BLOCKER | Fixed: moved to Task 4 (integration, red phase for Task 5) |
| REV 3 — `shutdown()` cancel placement: BEFORE executor shutdown | BLOCKER | Fixed: plan Task 5.3 and ADR 0090 D7 both specify before `_dictation_executor.shutdown(wait=False)` |
| REV 4 — E2E must test real daemon + real hotkey | BLOCKER | Fixed: Task 6 spawns real daemon subprocess; injects real Right Ctrl via pynput; asserts SSE events |
| REV 5 — verbatim `on_scroll_lock` replace-block | BLOCKER | Fixed: Task 3.3 shows exact current code (lines 352–399) as the replace target |
| REV 6 — document intentional refactor divergences | SHOULD-FIX | Fixed: Task 3.2 lists 4 intentional divergences with rationale in docstring |
| REV 7 — red-phase failure predictions | SHOULD-FIX | Fixed: `test_on_dictation_toggle_no_session_plays_no_miss_chime` now has explicit FAIL prediction with reason |
| REV 8 — `post_audio` docstring staleness | SHOULD-FIX | Fixed: Task 1.5 updates both `_TIMEOUT_S` constant and the `timeout:` param docstring |
| REV 9 — regression guard uses `on_scroll_lock()` | SHOULD-FIX | Fixed: Test 4 calls `daemon.on_scroll_lock()` and uses `recorder.close_session.reset_mock()` |

---

## Task Order Summary

| # | Task | Commit type | Suite status after |
|---|---|---|---|
| 1 | Lower `_TIMEOUT_S` to 30 s + update docstring + remote unit tests | `fix(dictation)` | Green |
| 2 | Unit tests for helpers and idle branch (RED phase) | `test(daemon)` | New unit tests red, rest green |
| 3 | Implement field + helpers + `on_scroll_lock` + `on_dictation_toggle` idle branch | `feat(daemon)` | All unit tests green |
| 4 | Integration tests incl. shutdown test (RED phase) | `test(integration)` | New integration tests red, rest green |
| 5 | Implement close-before-finalize + spoken-cancel close + `shutdown()` cleanup | `feat(daemon)` | Full suite green |
| 6 | Visual E2E harness (real daemon subprocess + pynput hotkey injection) | `test(e2e)` | Full suite green |
| 7 | Docs (ADR 0090, ADR 0086 amendment, technical-decisions, CLAUDE.md) | `docs` | Full suite green |

**Total tasks: 7** (each with multiple bite-sized steps)
