"""Unit tests for the voice_sprite StateMachine (ADR 0096 Phase 3).

Covers the new ``processing`` state added for the dictation.processing SSE
event — the sprite's visual indication that audio has been captured and the
server Whisper+LLM round-trip is in progress.
"""

from __future__ import annotations

from voice_sprite.state_machine import SpriteState, StateMachine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sm() -> StateMachine:
    """Return a fresh StateMachine at WARMUP state."""
    return StateMachine(heartbeat_timeout_ms=3000)


# ---------------------------------------------------------------------------
# PROCESSING state: initial field value
# ---------------------------------------------------------------------------


def test_processing_attribute_defaults_to_false() -> None:
    """StateMachine.processing starts False."""
    sm = _make_sm()
    assert sm.processing is False


# ---------------------------------------------------------------------------
# dictation.processing → PROCESSING state transition
# ---------------------------------------------------------------------------


def test_dictation_processing_event_transitions_to_processing_state() -> None:
    """dictation.processing event sets processing=True and returns PROCESSING state."""
    sm = _make_sm()
    # Simulate a dictation started
    sm.on_event("dictation.start", {})
    assert sm.dictating is True

    result = sm.on_event("dictation.processing", {})

    assert result == SpriteState.PROCESSING, (
        f"Expected PROCESSING, got {result}"
    )
    assert sm.processing is True
    assert sm.current_state == SpriteState.PROCESSING
    assert sm.target_state == SpriteState.PROCESSING


def test_dictation_processing_without_prior_start_still_sets_processing() -> None:
    """dictation.processing is handled defensively even without a prior dictation.start."""
    sm = _make_sm()
    result = sm.on_event("dictation.processing", {})
    assert result == SpriteState.PROCESSING
    assert sm.processing is True


# ---------------------------------------------------------------------------
# dictation.end after processing → idle (or at least clears processing)
# ---------------------------------------------------------------------------


def test_dictation_end_after_processing_clears_processing_flag() -> None:
    """dictation.end clears processing=False after the processing state was entered."""
    sm = _make_sm()
    sm.on_event("dictation.start", {})
    sm.on_event("dictation.processing", {})
    assert sm.processing is True

    sm.on_event("dictation.end", {"reason": "done"})

    assert sm.processing is False
    assert sm.dictating is False


def test_dictation_end_after_processing_does_not_set_cancelled_cue() -> None:
    """dictation.end {reason: done} after processing does not set cancelled_cue."""
    sm = _make_sm()
    sm.on_event("dictation.start", {})
    sm.on_event("dictation.processing", {})
    sm.on_event("dictation.end", {"reason": "done"})

    assert sm.cancelled_cue is False


# ---------------------------------------------------------------------------
# Cancel path: dictation.end {reason: cancel} skips processing
# ---------------------------------------------------------------------------


def test_cancel_path_does_not_enter_processing_state() -> None:
    """dictation.end {reason: cancel} without prior dictation.processing is safe."""
    sm = _make_sm()
    sm.on_event("dictation.start", {})
    # No dictation.processing event (cancel path skips it)
    sm.on_event("dictation.end", {"reason": "cancel"})

    assert sm.processing is False
    assert sm.cancelled_cue is True
    assert sm.dictating is False


def test_processing_false_at_start_of_new_dictation() -> None:
    """Starting a new dictation after a previous one does not carry over processing."""
    sm = _make_sm()
    # First dictation
    sm.on_event("dictation.start", {})
    sm.on_event("dictation.processing", {})
    sm.on_event("dictation.end", {"reason": "done"})
    assert sm.processing is False

    # Second dictation
    sm.on_event("dictation.start", {})
    assert sm.processing is False, (
        "processing must not carry over across dictation sessions"
    )


# ---------------------------------------------------------------------------
# PROCESSING state enum value
# ---------------------------------------------------------------------------


def test_processing_state_enum_value() -> None:
    """SpriteState.PROCESSING has the expected string value."""
    assert SpriteState.PROCESSING.value == "processing"


# ---------------------------------------------------------------------------
# Interaction with other state changes during processing
# ---------------------------------------------------------------------------


def test_dictation_end_with_empty_reason_clears_processing() -> None:
    """dictation.end with no reason key still clears processing (defensive)."""
    sm = _make_sm()
    sm.on_event("dictation.start", {})
    sm.on_event("dictation.processing", {})
    sm.on_event("dictation.end", {})  # no "reason" key

    assert sm.processing is False
    # cancelled_cue is False when reason != "cancel"
    assert sm.cancelled_cue is False
