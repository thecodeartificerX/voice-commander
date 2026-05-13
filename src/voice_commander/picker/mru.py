"""MRU tracker for focused windows.

Two layers:

* :class:`MruTracker` — a pure ring buffer + filter helper. No Win32
  dependency, fully unit-testable.
* :class:`Win32MruPump` — installs a :func:`SetWinEventHook` on a dedicated
  message-pump thread and pushes :class:`MruEntry` records into the tracker.

The pump lives at the bottom of this module so unit tests can import
:class:`MruTracker` without triggering the pywin32 import.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MruEntry:
    hwnd: int
    pid: int
    proc_name: str
    title: str


class MruTracker:
    """Ring buffer of recently foregrounded windows, newest first.

    Re-recording an hwnd already in the buffer moves it to the front; this
    matches the "switching back and forth" intuition. Thread-safe: ``record``
    and ``snapshot`` / ``top`` may be called from different threads.
    """

    def __init__(self, capacity: int = 32) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be > 0")
        self._capacity = capacity
        self._buf: deque[MruEntry] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._self_hwnds: set[int] = set()

    def register_self_hwnd(self, hwnd: int) -> None:
        """Mark *hwnd* as belonging to this daemon / sprite / modal.

        The tracker stores the set but does not filter automatically — callers
        pass ``predicate=lambda e: e.hwnd not in tracker.self_hwnds`` to
        :meth:`top` when self-exclusion is desired.
        """
        with self._lock:
            self._self_hwnds.add(hwnd)

    def unregister_self_hwnd(self, hwnd: int) -> None:
        with self._lock:
            self._self_hwnds.discard(hwnd)

    @property
    def self_hwnds(self) -> frozenset[int]:
        with self._lock:
            return frozenset(self._self_hwnds)

    def record(self, entry: MruEntry) -> None:
        """Push *entry* to the front, removing any prior occurrence of the same hwnd."""
        with self._lock:
            for i, existing in enumerate(self._buf):
                if existing.hwnd == entry.hwnd:
                    del self._buf[i]
                    break
            self._buf.appendleft(entry)

    def snapshot(self) -> list[MruEntry]:
        with self._lock:
            return list(self._buf)

    def top(
        self,
        n: int,
        predicate: Callable[[MruEntry], bool] | None = None,
    ) -> list[MruEntry]:
        if n <= 0:
            return []
        with self._lock:
            entries: Iterable[MruEntry] = list(self._buf)
        out: list[MruEntry] = []
        for entry in entries:
            if predicate is not None and not predicate(entry):
                continue
            out.append(entry)
            if len(out) >= n:
                break
        return out
