"""Unit tests for DictationSession._pending_end mechanics (Step 5 of fix/dictation-hotkey-end-race).

Written BEFORE the production code (TDD Red phase). Each test describes the
expected behaviour; all should FAIL until Step 1 is implemented in session.py.
"""

import numpy as np

from voice_commander.dictation.session import DictationSession


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def publish(self, event_type: str, data: dict | None = None) -> None:
        self.events.append((event_type, data or {}))


def _audio(n: int = 8000) -> np.ndarray:
    return np.ones(n, dtype=np.float32)


# ---------------------------------------------------------------------------
# Test 1: request_end sets pending_end
# ---------------------------------------------------------------------------


def test_request_end_sets_pending_end() -> None:
    """Calling request_end() on an active session sets pending_end to True."""
    s = DictationSession(_FakeBus())
    s.start()
    assert not s.pending_end, "pending_end should be False before request_end"
    s.request_end()
    assert s.pending_end, "pending_end should be True after request_end"


# ---------------------------------------------------------------------------
# Test 2: request_end is a no-op when session is inactive
# ---------------------------------------------------------------------------


def test_request_end_noop_when_inactive() -> None:
    """request_end() must not set pending_end when the session is not active."""
    s = DictationSession(_FakeBus())
    # Never started — inactive
    s.request_end()
    assert not s.pending_end, "pending_end must remain False for an inactive session"


# ---------------------------------------------------------------------------
# Test 3: take_and_finish clears pending_end atomically
# ---------------------------------------------------------------------------


def test_take_and_finish_clears_pending_end() -> None:
    """take_and_finish() must clear _pending_end inside the lock."""
    s = DictationSession(_FakeBus())
    s.start()
    s.handle_utterance(_audio(), "hello world")
    s.request_end()
    assert s.pending_end  # sanity
    audio = s.take_and_finish()
    assert audio is not None
    assert not s.pending_end, "take_and_finish must clear pending_end"
    assert not s.active, "take_and_finish must deactivate the session"


# ---------------------------------------------------------------------------
# Test 4: cancel clears pending_end
# ---------------------------------------------------------------------------


def test_cancel_clears_pending_end() -> None:
    """cancel() must clear pending_end so a subsequent request_end after
    scroll-lock restart doesn't carry stale state."""
    s = DictationSession(_FakeBus())
    s.start()
    s.request_end()
    assert s.pending_end  # sanity
    s.cancel()
    assert not s.pending_end, "cancel must clear pending_end"


# ---------------------------------------------------------------------------
# Test 5: request_end does NOT deactivate the session (active stays True)
# ---------------------------------------------------------------------------


def test_request_end_does_not_deactivate() -> None:
    """request_end() is a *signal* only — it must NOT flip _active to False.

    The pipeline thread must see the session as active so it can drain
    in-flight utterances before calling take_and_finish().
    """
    s = DictationSession(_FakeBus())
    s.start()
    s.request_end()
    assert s.active, "request_end must NOT deactivate; session must stay active"


# ---------------------------------------------------------------------------
# Test 6: double request_end is idempotent
# ---------------------------------------------------------------------------


def test_double_request_end_idempotent() -> None:
    """A second call to request_end() while pending_end is already set must be
    harmless and leave pending_end True."""
    s = DictationSession(_FakeBus())
    s.start()
    s.request_end()
    s.request_end()  # second call
    assert s.pending_end, "pending_end must remain True after double request_end"
    assert s.active, "session must remain active after double request_end"
