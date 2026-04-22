from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .charsheet import AnimInfo, CharSheet
from .state_machine import SpriteState

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class SpriteRenderer:
    """Manages frame selection and animation timing from a charsheet."""

    def __init__(self, charsheet: CharSheet) -> None:
        self._cs = charsheet
        self._current_anim: AnimInfo | None = None
        self._frame_index: int = 0
        self._frame_timer: float = 0.0
        self._frame_duration: float = 1.0 / charsheet.fps
        self._state = SpriteState.WARMUP
        self._transition_anim: AnimInfo | None = None
        self._transition_done = False
        # When muted, we render the IDLE pose (alt sitting = disengaged)
        # regardless of logical state, so the cat reads as "not listening".
        # Grey tint applied by the window on top is a secondary cue.
        self._muted = False
        self._set_anim(charsheet.get_state_anim(SpriteState.WARMUP))

    def set_muted(self, muted: bool) -> None:
        """Toggle muted rendering. When muted, frame_region always resolves
        to the IDLE animation regardless of the current state — visually
        communicates that the cat is not processing input."""
        self._muted = muted

    def _set_anim(self, anim: AnimInfo) -> None:
        self._current_anim = anim
        self._frame_index = 0
        self._frame_timer = 0.0

    def set_state(self, state: SpriteState) -> None:
        """Switch to *state*, playing a transition animation if one exists
        in the charsheet, otherwise swapping frames instantly.
        """
        # Check for transition animation
        transition = self._cs.get_transition(self._state, state)
        if transition is not None:
            self._transition_anim = transition
            self._transition_done = False
            self._set_anim(transition)
        else:
            self._set_anim(self._cs.get_state_anim(state))
            self._transition_anim = None
        self._state = state

    def tick(self, dt: float) -> None:
        """Advance animation by dt seconds."""
        if self._current_anim is None:
            return
        self._frame_timer += dt
        if self._frame_timer >= self._frame_duration:
            self._frame_timer -= self._frame_duration
            self._frame_index += 1
            if self._frame_index >= self._current_anim.frames:
                if self._transition_anim is not None and self._transition_anim.once:
                    # Transition complete → switch to target state's idle anim
                    self._transition_anim = None
                    self._transition_done = True
                    self._set_anim(self._cs.get_state_anim(self._state))
                else:
                    self._frame_index = 0  # loop

    @property
    def frame_region(self) -> tuple[int, int, int, int]:
        """Return (x, y, width, height) of the current frame in the charsheet.

        Per-state pitch overrides (anim.frame_width / frame_height) let rows
        with wider or taller poses slice cleanly — e.g. sleeping cat on row 6
        is 48-wide while sitting poses on row 0 are 32-wide.
        """
        anim = self._current_anim
        if self._muted:
            # Override to IDLE anim (disengaged pose) regardless of state.
            anim = self._cs.get_state_anim(SpriteState.IDLE)
        if anim is None:
            return (0, 0, self._cs.frame_width, self._cs.frame_height)
        fw = anim.frame_width or self._cs.frame_width
        fh = anim.frame_height or self._cs.frame_height
        # Frame index loops within the mute-override anim's frame count so
        # the IDLE animation cycles cleanly even if the underlying state's
        # frame count is different.
        frame_idx = self._frame_index % anim.frames
        x = frame_idx * fw
        y = anim.row * fh
        return (x, y, fw, fh)

    def reload_charsheet(self, charsheet: CharSheet) -> None:
        """Hot-reload the charsheet (e.g. after file change)."""
        self._cs = charsheet
        anim = charsheet.get_state_anim(self._state)
        self._set_anim(anim)
        self._transition_anim = None

    @property
    def charsheet(self) -> CharSheet:
        return self._cs
