from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Event:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    id: int = 0


class EventBus:
    """Thread-safe pub/sub broker with bounded per-subscriber async queues.

    Designed for daemon → SSE bridge: sync ``publish()`` from any thread,
    async ``subscribe()`` queues consumed by FastAPI SSE generators.
    """

    def __init__(
        self,
        max_buffer: int = 100,
        max_subscriber_queue: int = 1024,
    ) -> None:
        self._subscribers: list[asyncio.Queue[Event]] = []
        self._lock = threading.Lock()
        self._buffer: list[Event] = []
        self._max_buffer = max_buffer
        self._max_subscriber_queue = max_subscriber_queue
        self._next_id = 1
        self._events_dropped = 0

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Non-blocking publish from any thread."""
        with self._lock:
            event = Event(
                type=event_type,
                data=data or {},
                ts=time.time(),
                id=self._next_id,
            )
            self._next_id += 1
            self._buffer.append(event)
            if len(self._buffer) > self._max_buffer:
                self._buffer = self._buffer[-self._max_buffer :]
            for q in self._subscribers:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    # Drop oldest, enqueue new
                    with contextlib.suppress(asyncio.QueueEmpty):
                        q.get_nowait()
                    with contextlib.suppress(asyncio.QueueFull):
                        q.put_nowait(event)
                    self._events_dropped += 1

    def subscribe(self) -> asyncio.Queue[Event]:
        """Create a new subscriber queue."""
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._max_subscriber_queue)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[Event]) -> None:
        """Remove a subscriber queue."""
        with self._lock, contextlib.suppress(ValueError):
            self._subscribers.remove(q)

    def replay_after(self, last_id: int) -> list[Event]:
        """Return buffered events with id > last_id (for SSE reconnect)."""
        with self._lock:
            return [e for e in self._buffer if e.id > last_id]

    @property
    def events_dropped(self) -> int:
        return self._events_dropped
