"""Unit tests for DictationSession._pending_end mechanics (Step 5 of fix/dictation-hotkey-end-race).

Updated for the streaming DictationSession (ADR 0092): ws_url is required;
start() takes a Vocabulary; take_and_finish() → finish().
"""

import numpy as np

from voice_commander.dictation.session import DictationSession
from voice_commander.dictation.vocab import Vocabulary


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def publish(self, event_type: str, data: dict | None = None) -> None:
        self.events.append((event_type, data or {}))


def _audio(n: int = 8000) -> np.ndarray:
    return np.ones(n, dtype=np.float32)


def _session() -> DictationSession:
    """Build a DictationSession with a fast-failing WS URL (port 1 → refused)."""
    return DictationSession(
        bus=_FakeBus(),
        ws_url="ws://localhost:1",
        end_word="done",
        idle_timeout_s=0.5,
    )


# ---------------------------------------------------------------------------
# Test 1: request_end sets pending_end
# ---------------------------------------------------------------------------


def test_request_end_sets_pending_end() -> None:
    """Calling request_end() on an active session sets pending_end to True."""
    s = _session()
    s.start(Vocabulary())
    assert not s.pending_end, "pending_end should be False before request_end"
    s.request_end()
    assert s.pending_end, "pending_end should be True after request_end"
    s.cancel()  # cleanup


# ---------------------------------------------------------------------------
# Test 2: request_end is a no-op when session is inactive
# ---------------------------------------------------------------------------


def test_request_end_noop_when_inactive() -> None:
    """request_end() must not set pending_end when the session is not active."""
    s = _session()
    # Never started — inactive
    s.request_end()
    assert not s.pending_end, "pending_end must remain False for an inactive session"


# ---------------------------------------------------------------------------
# Test 3: finish() clears pending_end
# ---------------------------------------------------------------------------


def test_take_and_finish_clears_pending_end() -> None:
    """finish() must clear _pending_end (replaces old take_and_finish)."""
    s = _session()
    s.start(Vocabulary())
    s.handle_utterance(_audio(), "hello world")
    s.request_end()
    assert s.pending_end  # sanity
    s.finish()  # streaming: calls finish() instead of take_and_finish()
    assert not s.pending_end, "finish must clear pending_end"
    assert not s.active, "finish must deactivate the session"


# ---------------------------------------------------------------------------
# Test 4: cancel clears pending_end
# ---------------------------------------------------------------------------


def test_cancel_clears_pending_end() -> None:
    """cancel() must clear pending_end so a subsequent request_end after
    scroll-lock restart doesn't carry stale state."""
    s = _session()
    s.start(Vocabulary())
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
    in-flight utterances before calling finish().
    """
    s = _session()
    s.start(Vocabulary())
    s.request_end()
    assert s.active, "request_end must NOT deactivate; session must stay active"
    s.cancel()  # cleanup


# ---------------------------------------------------------------------------
# Test 6: double request_end is idempotent
# ---------------------------------------------------------------------------


def test_double_request_end_idempotent() -> None:
    """A second call to request_end() while pending_end is already set must be
    harmless and leave pending_end True."""
    s = _session()
    s.start(Vocabulary())
    s.request_end()
    s.request_end()  # second call
    assert s.pending_end, "pending_end must remain True after double request_end"
    assert s.active, "session must remain active after double request_end"
    s.cancel()  # cleanup
