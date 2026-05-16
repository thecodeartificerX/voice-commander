"""DictationSession — daemon sub-state that buffers dictation audio.

While active the daemon routes every utterance here instead of the VerbRouter.
Non-end utterances have their audio appended to a buffer; the end word triggers
finalisation. Mirrors PickerSession's role as a voice-session sub-state.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Literal, Protocol

import numpy as np
import numpy.typing as npt

from ..verb_router import _normalize_spoken

logger = logging.getLogger(__name__)


class _BusLike(Protocol):
    """The minimal EventBus surface DictationSession depends on.

    Structurally satisfied by :class:`~voice_commander.event_bus.EventBus`.
    """

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None: ...


UtteranceKind = Literal["buffered", "end"]


class DictationSession:
    """Holds dictation state + the in-memory audio buffer for one dictation."""

    def __init__(self, bus: _BusLike, end_word: str = "done") -> None:
        self._bus = bus
        self._end_word = _normalize_spoken(end_word)
        self._lock = threading.Lock()
        self._active = False
        self._buffer: list[npt.NDArray[np.float32]] = []
        self._pending_end = threading.Event()

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def pending_end(self) -> bool:
        """True when the hotkey-end path has requested finalisation.

        Set by :meth:`request_end`; cleared by :meth:`take_and_finish` and
        :meth:`cancel`.  Safe to read from any thread without holding the lock
        because :class:`threading.Event.is_set` is atomic in CPython.
        """
        return self._pending_end.is_set()

    def request_end(self) -> None:
        """Signal that the hotkey-end path wants to finalise dictation.

        Called from the hotkey thread.  Does NOT deactivate the session — the
        pipeline thread polls :attr:`pending_end` and calls
        :meth:`take_and_finish` after draining in-flight utterances.

        A no-op (with no logging) when the session is already inactive — covers
        the race where scroll-lock close wins first.
        """
        with self._lock:
            if not self._active:
                return
            self._pending_end.set()
        logger.debug("dictation: pending_end requested")

    def start(self) -> None:
        """Enter dictation mode; clear any previous buffer."""
        with self._lock:
            self._active = True
            self._buffer = []
        self._bus.publish("dictation.start", {})
        logger.info("dictation: started")

    def handle_utterance(
        self, audio: npt.NDArray[np.float32], text: str
    ) -> UtteranceKind:
        """Classify an utterance: ``"end"`` if it is the end word, else ``"buffered"``.

        End-word utterances are NOT appended to the buffer. This method never
        changes session state — on an ``"end"`` result the caller MUST call
        ``finish()`` to deactivate the session and publish the end event. A
        no-op returning ``"buffered"`` when inactive (lost race with finish/cancel).
        """
        with self._lock:
            if not self._active:
                return "buffered"
            if _normalize_spoken(text) == self._end_word:
                return "end"
            self._buffer.append(audio)
            return "buffered"

    def take_audio(self) -> npt.NDArray[np.float32] | None:
        """Return the concatenated buffered audio, or None if nothing buffered."""
        with self._lock:
            if not self._buffer:
                return None
            return np.concatenate(self._buffer)

    def finish(self) -> None:
        """End dictation normally (end word reached)."""
        with self._lock:
            if not self._active:
                return
            self._active = False
            self._buffer = []
        self._bus.publish("dictation.end", {"reason": "done"})
        logger.info("dictation: finished")

    def take_and_finish(self) -> npt.NDArray[np.float32] | None:
        """Atomically capture the buffered audio and end dictation.

        Holds the lock across the buffer read AND the deactivation, so a
        concurrent end-word finalize (pipeline thread) and a hardware
        toggle-off (hotkey thread) cannot both capture the same audio and
        double-submit it for transcription.

        Returns the concatenated buffered audio, or ``None`` when there is
        nothing to finalize — either the buffer was empty, or the session
        was already inactive because the other thread won the race. In both
        ``None`` cases the caller must NOT submit anything.
        """
        with self._lock:
            if not self._active:
                return None
            self._active = False
            self._pending_end.clear()
            audio = np.concatenate(self._buffer) if self._buffer else None
            self._buffer = []
        self._bus.publish("dictation.end", {"reason": "done"})
        logger.info("dictation: finished")
        return audio

    def cancel(self) -> None:
        """Abort dictation (e.g. scroll-lock closed the session); drop the buffer."""
        with self._lock:
            was_active = self._active
            self._active = False
            self._pending_end.clear()
            self._buffer = []
        if was_active:
            self._bus.publish("dictation.end", {"reason": "cancel"})
            logger.info("dictation: cancelled")
