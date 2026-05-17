"""DictationSession — daemon sub-state that buffers dictation audio.

While active the daemon routes every utterance here instead of the VerbRouter.
Non-end utterances have their audio appended to a buffer.  Finalisation happens
via one of three exit paths:

(a) **End-word path** — the pipeline thread recognises the configured end word
    (default ``"done"``), calls :meth:`take_and_finish`, captures the buffer,
    deactivates the session, and submits audio for transcription.

(b) **Hotkey-end path** — the hotkey thread calls :meth:`request_end`, which
    sets :attr:`pending_end` without deactivating the session.  The pipeline
    thread polls :attr:`pending_end`; while it is set, the pipeline uses a
    short timed get on the utterance queue (``_DICTATION_DRAIN_TIMEOUT_S``).
    Once the queue drains (timeout expires with no new item), the pipeline
    calls ``_finalize_pending_dictation_end`` → :meth:`take_and_finish`,
    which atomically captures the buffer and deactivates the session.

(c) **Spoken-cancel path** — :meth:`handle_utterance` recognises the configured
    cancel word (default ``"cancel"``; exact normalized match only — a longer
    phrase containing the cancel word is buffered, not cancelled) and returns
    ``"cancel"``.  The pipeline thread calls :meth:`cancel` to drop the buffer
    and abort the session without any transcription or paste.  The cancel word
    is validated against the end word at construction time; a collision disables
    spoken cancel (logged as a WARNING) to prevent ambiguity.

All paths converge on either :meth:`take_and_finish` (paths a and b) or
:meth:`cancel` (path c), both of which hold the lock across deactivation and
buffer disposal, preventing double-action races.  Mirrors PickerSession's role
as a voice-session sub-state.
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


UtteranceKind = Literal["buffered", "end", "cancel"]


class DictationSession:
    """Holds dictation state + the in-memory audio buffer for one dictation."""

    def __init__(
        self,
        bus: _BusLike,
        end_word: str = "done",
        cancel_word: str = "cancel",
    ) -> None:
        self._bus = bus
        self._end_word = _normalize_spoken(end_word)
        self._lock = threading.Lock()
        self._active = False
        self._buffer: list[npt.NDArray[np.float32]] = []
        self._pending_end = threading.Event()

        # Validate cancel_word against end_word and emptiness.
        # Cross-field validation lives here (not in config loader — no cross-field
        # stage exists there; precedent: dictation_key == hotkey_key warn-and-degrade
        # in daemon.py).
        normalized_cancel = _normalize_spoken(cancel_word)
        if not normalized_cancel:
            logger.warning(
                "dictation: cancel_word %r is empty after normalization; "
                "spoken cancel disabled",
                cancel_word,
            )
            self._cancel_word: str | None = None
        elif normalized_cancel == self._end_word:
            logger.warning(
                "dictation: cancel_word %r collides with end_word %r; "
                "spoken cancel disabled to avoid ambiguity",
                cancel_word,
                end_word,
            )
            self._cancel_word = None
        else:
            self._cancel_word = normalized_cancel

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
        """Classify an utterance: ``"end"``, ``"cancel"``, or ``"buffered"``.

        * ``"end"`` — transcript is the end word (exact normalized match).
          NOT appended to the buffer; caller MUST call :meth:`take_and_finish`.
        * ``"cancel"`` — transcript is the cancel word (exact normalized match,
          when ``_cancel_word`` is not ``None``). NOT appended to the buffer;
          caller MUST call :meth:`cancel` to discard the session.
        * ``"buffered"`` — audio appended to buffer; session stays active.

        A no-op returning ``"buffered"`` when inactive (lost race with
        take_and_finish/cancel).
        """
        with self._lock:
            # Guard 1 — session inactive: no-op, matches existing behaviour
            # for lost races with take_and_finish / cancel.
            if not self._active:
                return "buffered"

            normalized = _normalize_spoken(text)

            # Guard 2 — end word: existing path, unchanged.
            if normalized == self._end_word:
                return "end"

            # Guard 3 — cancel word: new path (ADR 0089); do NOT buffer.
            if self._cancel_word is not None and normalized == self._cancel_word:
                return "cancel"

            # Default — buffer the audio.
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
