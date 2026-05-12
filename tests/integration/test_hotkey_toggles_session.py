"""Integration tests: hotkey toggle drives session open/close on the daemon.

The real HotkeyController attaches an OS-level keyboard hook via pynput,
which requires physical hardware and elevated OS permissions. All tests here
are marked ``@pytest.mark.hardware`` and exercise the daemon's ``on_toggle``
method directly — no real hotkey listener is started.

The test strategy is:
  1. Build a StreamingDaemon with a stub recorder (tracks open/close calls).
  2. Call ``daemon.on_scroll_lock()`` programmatically — same code path that the
     real HotkeyController fires on key release.
  3. Assert the stub recorder saw the expected open/close sequence.

This validates the toggle logic (is_open → close, is_closed → open) without
touching any OS keyboard subsystem.
"""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# CUDA guard (kept for parity; hotkey tests do not require CUDA)
# ---------------------------------------------------------------------------

try:
    import ctranslate2  # type: ignore[import]

    HAS_CUDA = ctranslate2.get_cuda_device_count() > 0
except Exception:
    HAS_CUDA = False

requires_cuda = pytest.mark.skipif(not HAS_CUDA, reason="CUDA not available")


# ---------------------------------------------------------------------------
# Stub recorder: tracks open/close calls without touching hardware
# ---------------------------------------------------------------------------


class _StubRecorder:
    """Minimal recorder stub with the same interface as StreamingRecorder.

    Tracks ``open_session`` and ``close_session`` call counts and toggles
    ``is_open`` accordingly, mirroring the real object's state machine.
    """

    def __init__(self) -> None:
        self._open = False
        self.open_calls = 0
        self.close_calls = 0

    @property
    def is_open(self) -> bool:
        return self._open

    def open_session(self) -> None:
        self._open = True
        self.open_calls += 1

    def close_session(self) -> None:
        self._open = False
        self.close_calls += 1


# ---------------------------------------------------------------------------
# Fixture: StreamingDaemon with all real subsystems replaced by stubs
# ---------------------------------------------------------------------------


@pytest.fixture()
def stub_daemon(tmp_path: Any) -> Any:
    """StreamingDaemon wired with stub recorder + null feedback + stub T/M/D.

    No models are loaded; no threads are started; no hardware is touched.
    The pipeline thread is NOT started (we're only testing on_toggle logic).
    """
    from voice_commander.daemon import StreamingDaemon
    from voice_commander.feedback import NullFeedbackSink

    recorder = _StubRecorder()
    feedback = NullFeedbackSink()

    # Stub transcriber, resolver, dispatcher — on_toggle never calls them.
    transcriber = MagicMock()
    transcriber.load.return_value = None
    transcriber.unload.return_value = None

    dispatcher = MagicMock()

    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=recorder,  # type: ignore[arg-type]
        transcriber=transcriber,
        verb_router=MagicMock(),
        dispatcher=dispatcher,
        output_dir=str(tmp_path / "outputs"),
    )
    return daemon


# ---------------------------------------------------------------------------
# Test 1 — two toggles: open then close
# ---------------------------------------------------------------------------


@pytest.mark.hardware
@pytest.mark.integration
def test_toggle_opens_and_closes(stub_daemon: Any) -> None:
    """Two on_toggle() calls: first opens, second closes.

    Verifies the fundamental toggle contract:
      - recorder.open_session() called exactly once.
      - recorder.close_session() called exactly once.
      - recorder ends in closed state.
    """
    recorder: _StubRecorder = stub_daemon._recorder  # type: ignore[assignment]

    # Pre-condition: recorder starts closed.
    assert not recorder.is_open
    assert recorder.open_calls == 0
    assert recorder.close_calls == 0

    # First toggle: should open the session.
    stub_daemon.on_scroll_lock()
    assert recorder.is_open
    assert recorder.open_calls == 1
    assert recorder.close_calls == 0

    # Second toggle: should close the session.
    stub_daemon.on_scroll_lock()
    assert not recorder.is_open
    assert recorder.open_calls == 1
    assert recorder.close_calls == 1


# ---------------------------------------------------------------------------
# Test 2 — rapid alternating toggles are applied in order
# ---------------------------------------------------------------------------


@pytest.mark.hardware
@pytest.mark.integration
def test_multiple_toggles_alternate(stub_daemon: Any) -> None:
    """Four toggles: open → close → open → close.

    Ensures the toggle logic isn't sticky after the first cycle.
    """
    recorder: _StubRecorder = stub_daemon._recorder  # type: ignore[assignment]

    for expected_open_count, expected_close_count in [
        (1, 0),  # after toggle 1
        (1, 1),  # after toggle 2
        (2, 1),  # after toggle 3
        (2, 2),  # after toggle 4
    ]:
        stub_daemon.on_scroll_lock()
        assert recorder.open_calls == expected_open_count
        assert recorder.close_calls == expected_close_count

    assert not recorder.is_open


# ---------------------------------------------------------------------------
# Test 3 — recorder failure on open is reported to feedback, session stays closed
# ---------------------------------------------------------------------------


@pytest.mark.hardware
@pytest.mark.integration
def test_toggle_open_failure_reported(tmp_path: Any) -> None:
    """If recorder.open_session() raises, on_error is called and session stays closed."""
    from voice_commander.daemon import StreamingDaemon
    from voice_commander.feedback import CapturingFeedbackSink

    class _FailingRecorder(_StubRecorder):
        def open_session(self) -> None:
            raise RuntimeError("simulated device error")

    recorder = _FailingRecorder()
    feedback = CapturingFeedbackSink()

    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=recorder,  # type: ignore[arg-type]
        transcriber=MagicMock(),
        verb_router=MagicMock(),
        dispatcher=MagicMock(),
        output_dir=str(tmp_path / "outputs"),
    )

    # Toggle: open will fail.
    daemon.on_scroll_lock()

    # Recorder must still be closed.
    assert not recorder.is_open

    # Feedback must have received an on_error call.
    error_events = [(name, args) for name, args in feedback.calls if name == "on_error"]
    assert len(error_events) >= 1, "Expected on_error to be called after open failure"

    subsystem, err = error_events[0][1]
    assert "recorder" in subsystem.lower(), f"Unexpected subsystem string: {subsystem!r}"
    assert isinstance(err, RuntimeError)


# ---------------------------------------------------------------------------
# Test 4 — on_toggle is thread-safe under concurrent callers
# ---------------------------------------------------------------------------


@pytest.mark.hardware
@pytest.mark.integration
def test_toggle_thread_safety(stub_daemon: Any) -> None:
    """Concurrent on_toggle() calls from two threads must not corrupt state.

    The recorder's open/close counts must equal the number of times
    on_toggle() was actually called (no double-open or double-close), and the
    daemon must not raise. We can only assert the total toggle budget is
    consistent — the exact interleaving is non-deterministic.
    """
    recorder: _StubRecorder = stub_daemon._recorder  # type: ignore[assignment]
    errors: list[BaseException] = []

    def _do_toggles(n: int) -> None:
        for _ in range(n):
            try:
                stub_daemon.on_scroll_lock()
            except Exception as exc:
                errors.append(exc)

    n_per_thread = 10
    t1 = threading.Thread(target=_do_toggles, args=(n_per_thread,))
    t2 = threading.Thread(target=_do_toggles, args=(n_per_thread,))
    t1.start()
    t2.start()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)

    assert not errors, f"on_toggle raised exceptions under concurrency: {errors}"

    total_toggles = recorder.open_calls + recorder.close_calls
    expected_total = 2 * n_per_thread
    assert total_toggles == expected_total, (
        f"Expected {expected_total} total open+close calls, got {total_toggles}. "
        f"(open={recorder.open_calls}, close={recorder.close_calls})"
    )

    # Final state must be consistent: open iff more opens than closes.
    expected_is_open = (recorder.open_calls - recorder.close_calls) == 1
    assert recorder.is_open == expected_is_open


