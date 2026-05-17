"""Elements mode state machine (ADR 0087).

One-shot lifecycle: IDLE -> SCANNING -> HINTS_SHOWN -> IDLE. The session owns
no threads of its own except a single auto-dismiss timer; the daemon drives
scanning and clicking on a worker thread and feeds transcripts in via
``handle_utterance``.
"""

from __future__ import annotations

import threading
from enum import Enum, auto
from typing import Any, Protocol

from .numbers import parse_number
from .scanner import Element

# Entry words that start a scan. Kept as a constant (not config) so the
# daemon's transcript intercept needs no list-typed config plumbing.
ENTRY_WORDS: frozenset[str] = frozenset({"element", "elements"})


class _BusLike(Protocol):
    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None: ...


class ElementsState(Enum):
    IDLE = auto()
    SCANNING = auto()
    HINTS_SHOWN = auto()


class ElementsSession:
    """Tracks elements-mode state and emits overlay events."""

    def __init__(self, bus: _BusLike, *, hint_timeout_s: float = 8.0) -> None:
        self._bus = bus
        self._hint_timeout_s = hint_timeout_s
        self._lock = threading.Lock()
        self._state = ElementsState.IDLE
        self._elements: list[Element] = []
        self._timer: threading.Timer | None = None
        # Scan-generation counter: incremented on every begin_scan(). Timer
        # callbacks capture the generation at arm-time and bail if it no longer
        # matches, preventing a stale timer from a previous scan from
        # publishing elements.hide into a subsequent scan's lifecycle.
        self._generation: int = 0

    @property
    def state(self) -> ElementsState:
        with self._lock:
            return self._state

    @property
    def active(self) -> bool:
        with self._lock:
            return self._state is not ElementsState.IDLE

    def begin_scan(self) -> None:
        """IDLE -> SCANNING. No-op if a scan is already in flight."""
        with self._lock:
            if self._state is not ElementsState.IDLE:
                return
            self._generation += 1
            self._state = ElementsState.SCANNING

    def show(self, elements: list[Element], monitor_rect: tuple[int, int, int, int]) -> None:
        """SCANNING -> HINTS_SHOWN. Publishes ``elements.show`` and arms the
        auto-dismiss timer. No-op unless a scan is in flight."""
        with self._lock:
            if self._state is not ElementsState.SCANNING:
                return
            self._state = ElementsState.HINTS_SHOWN
            self._elements = list(elements)
            self._start_timer_locked()
        self._bus.publish(
            "elements.show",
            {
                "monitor": list(monitor_rect),
                "elements": [
                    {"index": e.index, "rect": list(e.rect), "label": e.label}
                    for e in elements
                ],
            },
        )

    def fail(self) -> None:
        """Abort a scan (no window / nothing found / error). Emits no event;
        the caller plays the miss chime."""
        with self._lock:
            self._cancel_timer_locked()
            self._state = ElementsState.IDLE
            self._elements = []

    def handle_utterance(self, text: str) -> Element | None:
        """Consume a transcript while hints are shown. Returns the chosen
        element for a valid number, or ``None`` (implicit cancel) otherwise.
        Always publishes ``elements.hide`` and returns to IDLE."""
        with self._lock:
            if self._state is not ElementsState.HINTS_SHOWN:
                return None
            self._cancel_timer_locked()
            self._state = ElementsState.IDLE
            elements = self._elements
            self._elements = []
        chosen: Element | None = None
        number = parse_number(text)
        if number is not None and 1 <= number <= len(elements):
            chosen = elements[number - 1]
        self._bus.publish("elements.hide", {})
        return chosen

    def cancel(self) -> None:
        """Force the session back to IDLE (e.g. the voice session closed).
        Publishes ``elements.hide`` only if an overlay was on screen."""
        with self._lock:
            if self._state is ElementsState.IDLE:
                return
            self._cancel_timer_locked()
            was_shown = self._state is ElementsState.HINTS_SHOWN
            self._state = ElementsState.IDLE
            self._elements = []
        if was_shown:
            self._bus.publish("elements.hide", {})

    def _on_timeout(self, generation: int) -> None:
        """Auto-dismiss callback: fired by the timer after hint_timeout_s.

        ``generation`` is captured at timer-arm time. If the session has moved
        on to a new scan (``_generation`` advanced), the callback is stale and
        does nothing — preventing a timer from scan #N from publishing
        ``elements.hide`` into scan #N+1's lifecycle.
        """
        with self._lock:
            if generation != self._generation:
                return  # stale timer from a previous scan — discard
            if self._state is not ElementsState.HINTS_SHOWN:
                return
            self._state = ElementsState.IDLE
            self._elements = []
            self._timer = None
        self._bus.publish("elements.hide", {})

    def _start_timer_locked(self) -> None:
        if self._hint_timeout_s <= 0:
            return
        # Capture the current generation so the callback can detect staleness.
        generation = self._generation
        self._timer = threading.Timer(
            self._hint_timeout_s, self._on_timeout, args=(generation,)
        )
        self._timer.daemon = True
        self._timer.start()

    def _cancel_timer_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
