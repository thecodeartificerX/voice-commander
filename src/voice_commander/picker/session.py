"""PickerSession — daemon-side state machine for an open bare-primitive picker.

A session is the sub-state of an active voice session during which the next
utterance picks an item from the modal. Holds no Win32 / pyglet code: the
sprite renders via the SSE ``picker.open`` / ``picker.close`` events that this
class publishes.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterable
from typing import Protocol

from voice_commander.picker.types import PickerItem

logger = logging.getLogger(__name__)


class _BusLike(Protocol):
    def publish(self, event_type: str, data: dict | None = None) -> None: ...


class PickerSession:
    """Daemon-owned picker state.

    Thread-safety: every public method acquires the internal lock, so callers
    on the pipeline thread, the heartbeat thread, and the hotkey thread can
    interact without races.
    """

    def __init__(self, bus: _BusLike, now: callable = time.monotonic) -> None:  # type: ignore[valid-type]
        self._bus = bus
        self._now = now
        self._lock = threading.Lock()
        self._active = False
        self._verb = ""
        self._items: tuple[PickerItem, ...] = ()
        self._opened_at: float = 0.0

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def items(self) -> tuple[PickerItem, ...]:
        with self._lock:
            return self._items

    @property
    def verb(self) -> str:
        with self._lock:
            return self._verb

    def open(self, verb: str, items: Iterable[PickerItem]) -> None:
        """Open (or replace) the picker for *verb* with *items*.

        Emits ``picker.open`` with a payload of ``{verb, items: [{n, label}]}``.
        Empty *items* is legal but rare — callers should miss-chime instead of
        opening on empty.
        """
        items_tuple = tuple(items)
        with self._lock:
            self._active = True
            self._verb = verb
            self._items = items_tuple
            self._opened_at = self._now()
        payload = {
            "verb": verb,
            "items": [{"n": i + 1, "label": it.label} for i, it in enumerate(items_tuple)],
        }
        self._bus.publish("picker.open", payload)

    def close(self, reason: str = "select") -> None:
        """Close the picker with *reason* (``select`` | ``word`` | ``timeout`` | ``abort`` | ``out_of_range``)."""
        with self._lock:
            if not self._active:
                return
            verb = self._verb
            self._active = False
            self._verb = ""
            self._items = ()
            self._opened_at = 0.0
        self._bus.publish("picker.close", {"verb": verb, "reason": reason})

    def cancel(self, reason: str = "word") -> None:
        """Alias for :meth:`close` that emphasises non-selection paths."""
        self.close(reason=reason)
