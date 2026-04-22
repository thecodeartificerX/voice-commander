from __future__ import annotations

import time

from voice_sprite.state_machine import (
    EVENT_STATE_MAP,
    SpriteState,
    StateMachine,
)


def test_initial_state_is_warmup():
    sm = StateMachine()
    assert sm.current_state == SpriteState.WARMUP


def test_session_started_transitions_to_listening():
    sm = StateMachine()
    sm.current_state = SpriteState.IDLE
    result = sm.on_event("session_started", {})
    assert result == SpriteState.LISTENING


def test_session_stopped_transitions_to_idle():
    sm = StateMachine()
    sm.current_state = SpriteState.LISTENING
    sm.on_event("session_stopped", {})
    # LISTENING → IDLE is an animated transition; target_state updates immediately
    # but current_state stays until the animation completes.
    assert sm.target_state == SpriteState.IDLE


def test_vad_speech_active_true():
    sm = StateMachine()
    sm.current_state = SpriteState.LISTENING
    result = sm.on_event("vad_speech", {"active": True})
    assert result == SpriteState.HEARING_SPEECH


def test_vad_speech_active_false_ignored():
    sm = StateMachine()
    sm.current_state = SpriteState.LISTENING
    result = sm.on_event("vad_speech", {"active": False})
    assert result is None
    assert sm.current_state == SpriteState.LISTENING


def test_tool_fired_transitions_to_success():
    sm = StateMachine()
    sm.current_state = SpriteState.THINKING
    result = sm.on_event("tool_fired", {"name": "copy"})
    assert result == SpriteState.SUCCESS


def test_miss_transitions_to_miss():
    sm = StateMachine()
    sm.current_state = SpriteState.THINKING
    sm.on_event("miss", {})
    assert sm.target_state == SpriteState.MISS


def test_muted_sets_overlay():
    sm = StateMachine()
    sm.on_event("muted", {})
    assert sm.muted is True
    sm.on_event("unmuted", {})
    assert sm.muted is False


def test_muted_does_not_change_state():
    sm = StateMachine()
    sm.current_state = SpriteState.LISTENING
    sm.on_event("muted", {})
    assert sm.current_state == SpriteState.LISTENING


def test_heartbeat_resets_timer():
    sm = StateMachine()
    sm.on_event("daemon_heartbeat", {})
    assert sm._last_heartbeat > 0


def test_heartbeat_timeout_forces_crashed():
    sm = StateMachine(heartbeat_timeout_ms=100)
    sm.current_state = SpriteState.IDLE
    sm._last_heartbeat = time.monotonic() - 1.0  # 1s ago, timeout is 0.1s
    changed = sm.tick(0.016)
    assert changed is True
    assert sm.current_state == SpriteState.CRASHED


def test_heartbeat_recovery_after_crashed():
    sm = StateMachine()
    sm.current_state = SpriteState.CRASHED
    sm.on_event("daemon_heartbeat", {})
    assert sm.target_state == SpriteState.IDLE


def test_hold_state_returns_to_listening():
    sm = StateMachine()
    sm.current_state = SpriteState.LISTENING
    sm.on_event("tool_fired", {"name": "copy"})
    # Simulate hold timer expiry
    sm._hold_timer = time.monotonic() - 0.1
    changed = sm.tick(0.016)
    assert changed is True
    assert sm.current_state == SpriteState.LISTENING


def test_warmup_done_transitions_to_idle():
    sm = StateMachine()
    assert sm.current_state == SpriteState.WARMUP
    sm.on_event("warmup_done", {})
    assert sm.current_state == SpriteState.IDLE


def test_unknown_event_returns_none():
    sm = StateMachine()
    result = sm.on_event("bogus_event", {})
    assert result is None


def test_force_crashed():
    sm = StateMachine()
    sm.current_state = SpriteState.LISTENING
    sm.force_crashed()
    assert sm.current_state == SpriteState.CRASHED
    assert sm.target_state == SpriteState.CRASHED


def test_all_states_in_event_map():
    """Every non-crashed, non-muted state should be reachable via at least one event."""
    reachable = set(v for v in EVENT_STATE_MAP.values() if v is not None)
    unreachable = {SpriteState.CRASHED}  # only reachable via heartbeat timeout
    for state in SpriteState:
        if state not in unreachable:
            assert state in reachable, f"{state} not reachable via any event"


def test_complete_transition_updates_current_state():
    sm = StateMachine()
    sm.current_state = SpriteState.LISTENING
    # LISTENING → IDLE is an animated transition
    sm.on_event("session_stopped", {})
    assert sm._transitioning is True
    assert sm.current_state == SpriteState.LISTENING  # not yet updated
    assert sm.target_state == SpriteState.IDLE
    sm.complete_transition()
    assert sm.current_state == SpriteState.IDLE
    assert sm._transitioning is False


def test_hold_timer_returns_to_idle_after_session_stop():
    sm = StateMachine()
    sm.current_state = SpriteState.THINKING
    sm.on_event("tool_fired", {"name": "copy"})
    # During hold, session stops
    sm.on_event("session_stopped", {})
    # Expire hold timer
    sm._hold_timer = time.monotonic() - 0.1
    changed = sm.tick(0.016)
    assert changed is True
    assert sm.current_state == SpriteState.IDLE  # not LISTENING
