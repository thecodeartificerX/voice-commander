"""Ring-buffered chat log entries for the HUD overlay."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Literal

ChatLogStatus = Literal["ok", "error", "miss"]


@dataclass
class ChatLogEntry:
    text: str
    status: ChatLogStatus
    born_at_s: float


class ChatLog:
    """Fixed-size newest-first ring buffer with a per-entry hold+fade curve."""

    def __init__(self, max_lines: int, hold_ms: int, fade_ms: int) -> None:
        if max_lines <= 0:
            raise ValueError("max_lines must be > 0")
        if hold_ms < 0 or fade_ms < 0:
            raise ValueError("hold_ms and fade_ms must be >= 0")
        self._entries: deque[ChatLogEntry] = deque(maxlen=max_lines)
        self._hold_s = hold_ms / 1000.0
        self._fade_s = fade_ms / 1000.0

    def append(self, entry: ChatLogEntry) -> None:
        self._entries.append(entry)

    def entries(self) -> list[ChatLogEntry]:
        """Newest-first."""
        return list(reversed(self._entries))

    def opacity_of(self, entry: ChatLogEntry, now_s: float) -> float:
        age = now_s - entry.born_at_s
        if age < self._hold_s:
            return 1.0
        if self._fade_s == 0:
            return 0.0
        t = (age - self._hold_s) / self._fade_s
        if t >= 1.0:
            return 0.0
        return max(0.0, min(1.0, 1.0 - t))

    def tick(self, now_s: float) -> None:
        """Evict fully-faded entries. Call ~once per frame."""
        while self._entries and self.opacity_of(self._entries[0], now_s) == 0.0:
            self._entries.popleft()
