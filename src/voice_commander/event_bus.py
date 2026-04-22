from __future__ import annotations

import contextlib
import logging
import queue
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
    """Thread-safe pub/sub broker with bounded per-subscriber queues.

    Designed for daemon → SSE bridge: sync ``publish()`` from any thread,
    subscriber queues consumed by FastAPI SSE generators (drained via
    ``asyncio.get_event_loop().run_in_executor``).
    """

    def __init__(
        self,
        max_buffer: int = 100,
        max_subscriber_queue: int = 1024,
    ) -> None:
        self._subscribers: list[queue.Queue[Event]] = []
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
                except queue.Full:
                    # Drop oldest, enqueue new
                    with contextlib.suppress(queue.Empty):
                        q.get_nowait()
                    with contextlib.suppress(queue.Full):
                        q.put_nowait(event)
                    self._events_dropped += 1
                    logger.debug(
                        "event_bus overflow, dropped oldest event: type=%s id=%d",
                        event.type,
                        event.id,
                    )

    def subscribe(self) -> queue.Queue[Event]:
        """Create a new subscriber queue."""
        q: queue.Queue[Event] = queue.Queue(maxsize=self._max_subscriber_queue)
        with self._lock:
            self._subscribers.append(q)
        return q

    def subscribe_with_replay(self, last_id: int) -> tuple[queue.Queue[Event], list[Event]]:
        """Atomically subscribe and replay missed events.

        Returns ``(queue, replay_list)`` inside a single lock acquisition,
        closing the window where events could be lost between subscribe
        and replay.
        """
        q: queue.Queue[Event] = queue.Queue(maxsize=self._max_subscriber_queue)
        with self._lock:
            self._subscribers.append(q)
            replay = [e for e in self._buffer if e.id > last_id]
        return q, replay

    def unsubscribe(self, q: queue.Queue[Event]) -> None:
        """Remove a subscriber queue."""
        with self._lock, contextlib.suppress(ValueError):
            self._subscribers.remove(q)

    def replay_after(self, last_id: int) -> list[Event]:
        """Return buffered events with id > last_id (for SSE reconnect)."""
        with self._lock:
            return [e for e in self._buffer if e.id > last_id]

    @property
    def events_dropped(self) -> int:
        with self._lock:
            return self._events_dropped
