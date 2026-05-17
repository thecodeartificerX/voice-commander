from __future__ import annotations

import time
from enum import Enum
from typing import Any


class SpriteState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    HEARING_SPEECH = "hearing_speech"
    THINKING = "thinking"
    LLM_THINKING = "llm_thinking"
    SUCCESS = "success"
    MISS = "miss"
    TOOL_ERROR = "tool_error"
    WARMUP = "warmup"
    CRASHED = "crashed"


# Event → target state mapping (pure data table).
# None means "no state change" (e.g. heartbeat just resets timer).
EVENT_STATE_MAP: dict[str, SpriteState | None] = {
    "session_started": SpriteState.LISTENING,
    "session_stopped": SpriteState.IDLE,
    "vad_speech": SpriteState.HEARING_SPEECH,
    "transcribing": SpriteState.THINKING,
    "matching": SpriteState.THINKING,
    "llm_thinking": SpriteState.LLM_THINKING,
    "tool_fired": SpriteState.SUCCESS,
    "miss": SpriteState.MISS,
    "tool_error": SpriteState.TOOL_ERROR,
    "warmup_start": SpriteState.WARMUP,
    "warmup_done": SpriteState.IDLE,
    "daemon_heartbeat": None,
}

# Transitions that get bespoke animations (all others = instant swap).
ANIMATED_TRANSITIONS: set[tuple[SpriteState, SpriteState]] = {
    (SpriteState.IDLE, SpriteState.LISTENING),
    (SpriteState.LISTENING, SpriteState.IDLE),
    (SpriteState.LISTENING, SpriteState.HEARING_SPEECH),
    (SpriteState.HEARING_SPEECH, SpriteState.THINKING),
    (SpriteState.THINKING, SpriteState.LLM_THINKING),
    (SpriteState.THINKING, SpriteState.SUCCESS),
    (SpriteState.LLM_THINKING, SpriteState.SUCCESS),
    (SpriteState.THINKING, SpriteState.MISS),
    (SpriteState.LLM_THINKING, SpriteState.MISS),
}

# States that hold for 1 second before returning to LISTENING.
HOLD_STATES: set[SpriteState] = {
    SpriteState.SUCCESS,
    SpriteState.MISS,
    SpriteState.TOOL_ERROR,
}
HOLD_DURATION_S = 1.0


class StateMachine:
    """Sprite state machine driven by SSE events."""

    def __init__(self, heartbeat_timeout_ms: int = 3000) -> None:
        self.current_state = SpriteState.WARMUP
        self.target_state = SpriteState.WARMUP
        self.muted = False
        self.dictating = False
        self.cancelled_cue: bool = False  # True when last dictation.end had reason="cancel"
        self._heartbeat_timeout_s = heartbeat_timeout_ms / 1000.0
        self._last_heartbeat: float = 0.0
        self._hold_timer: float | None = None
        self._return_state: SpriteState = SpriteState.LISTENING
        self._transitioning = False

    def on_event(self, event_type: str, data: dict[str, Any]) -> SpriteState | None:
        """Process an SSE event and return the new state, or None if unchanged.

        Accepts any event_type string; target state is resolved via the
        EVENT_STATE_MAP table. Unknown event types return None with no
        state change and no exception (quietly ignored).
        Filters: vad_speech events only trigger on payload active=true.
        Side-effects: resets heartbeat timer, may start hold timer for
        transient states (success, miss, tool_error).
        """
        if event_type == "daemon_heartbeat":
            self._last_heartbeat = time.monotonic()
            if self.current_state == SpriteState.CRASHED:
                self.target_state = SpriteState.IDLE
            return None

        if event_type == "muted":
            self.muted = True
            return None

        if event_type == "unmuted":
            self.muted = False
            return None

        if event_type == "dictation.start":
            self.dictating = True
            self.cancelled_cue = False  # clear any prior cancel cue on new session
            return None

        if event_type == "dictation.end":
            self.dictating = False
            reason = data.get("reason")  # defensive: some publishers may omit "reason"
            # Surface a distinct cancelled visual when reason == "cancel".
            # This covers both spoken cancel AND scroll-lock cancel — both
            # paths call DictationSession.cancel() which emits reason="cancel".
            # Renderer (voice_sprite/__main__.py or equivalent) reads
            # self.cancelled_cue to show a brief "cancelled" text badge.
            self.cancelled_cue = reason == "cancel"
            return None

        # vad_speech only triggers on active=true
        if event_type == "vad_speech" and not data.get("active", False):
            return None

        target = EVENT_STATE_MAP.get(event_type)
        if target is None:
            return None

        # If session stopped during a hold, update the return state
        if target == SpriteState.IDLE and self._hold_timer is not None:
            self._return_state = SpriteState.IDLE

        self.target_state = target

        if target in HOLD_STATES:
            self._hold_timer = time.monotonic() + HOLD_DURATION_S
            self._return_state = SpriteState.LISTENING

        pair = (self.current_state, target)
        self._transitioning = pair in ANIMATED_TRANSITIONS

        if not self._transitioning:
            self.current_state = target

        return target

    def tick(self, dt: float) -> bool:
        """Called every frame. Returns True if state changed."""
        # Heartbeat timeout → CRASHED
        if self._last_heartbeat > 0:
            elapsed = time.monotonic() - self._last_heartbeat
            if elapsed > self._heartbeat_timeout_s and self.current_state != SpriteState.CRASHED:
                self.current_state = SpriteState.CRASHED
                self.target_state = SpriteState.CRASHED
                return True

        # Hold timer expiry → return to cached return state
        if self._hold_timer is not None and time.monotonic() >= self._hold_timer:
            self._hold_timer = None
            self.target_state = self._return_state
            self.current_state = self._return_state
            return True

        return False

    def complete_transition(self) -> None:
        """Called when the renderer finishes a play-once transition animation."""
        if self._transitioning:
            self.current_state = self.target_state
            self._transitioning = False

    def force_crashed(self) -> None:
        """Force CRASHED state (used when SSE connection drops)."""
        self.current_state = SpriteState.CRASHED
        self.target_state = SpriteState.CRASHED
        self._transitioning = False
