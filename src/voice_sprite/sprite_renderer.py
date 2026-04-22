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
        self._set_anim(charsheet.get_state_anim(SpriteState.WARMUP))

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
        """Return (x, y, width, height) of the current frame in the charsheet."""
        if self._current_anim is None:
            return (0, 0, self._cs.frame_width, self._cs.frame_height)
        x = self._frame_index * self._cs.frame_width
        y = self._current_anim.row * self._cs.frame_height
        return (x, y, self._cs.frame_width, self._cs.frame_height)

    def reload_charsheet(self, charsheet: CharSheet) -> None:
        """Hot-reload the charsheet (e.g. after file change)."""
        self._cs = charsheet
        anim = charsheet.get_state_anim(self._state)
        self._set_anim(anim)
        self._transition_anim = None

    @property
    def charsheet(self) -> CharSheet:
        return self._cs
