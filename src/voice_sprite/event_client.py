from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import Any

import httpx
from httpx_sse import connect_sse

logger = logging.getLogger(__name__)


class SSEClient:
    """Connects to the daemon's /events SSE endpoint in a background thread."""

    def __init__(
        self,
        base_url: str,
        on_event: Callable[[str, dict[str, Any]], None],
        on_disconnect: Callable[[], None],
    ) -> None:
        self._base_url = base_url
        self._on_event = on_event
        self._on_disconnect = on_disconnect
        self._last_event_id: str = "0"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="sse-client")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def _dispatch(self, event_type: str, data: dict[str, Any]) -> None:
        self._on_event(event_type, data)

    def _on_disconnect_callback(self) -> None:
        self._on_disconnect()

    def _run(self) -> None:
        """SSE consumer loop with auto-reconnect.

        Uses exponential backoff (1s -> 2s -> 4s -> 8s cap) on connection
        failure. Passes Last-Event-ID header on reconnect so the daemon
        replays missed events from its ring buffer.
        """
        backoff = 1.0
        while not self._stop.is_set():
            try:
                with httpx.Client(
                    timeout=httpx.Timeout(connect=5.0, read=35.0, write=5.0, pool=5.0)
                ) as client:
                    headers: dict[str, str] = {}
                    if self._last_event_id != "0":
                        headers["Last-Event-ID"] = self._last_event_id
                    with connect_sse(
                        client,
                        "GET",
                        f"{self._base_url}/events",
                        headers=headers,
                    ) as sse:
                        backoff = 1.0
                        for event in sse.iter_sse():
                            if self._stop.is_set():
                                return
                            if event.id:
                                self._last_event_id = event.id
                            try:
                                data = json.loads(event.data) if event.data else {}
                            except json.JSONDecodeError:
                                logger.warning(
                                    "Malformed SSE event data (ignored): %.200s",
                                    event.data,
                                )
                                data = {}
                            self._dispatch(event.event or "message", data)
            except (
                httpx.ConnectError,
                httpx.ReadTimeout,
                httpx.RemoteProtocolError,
            ) as e:
                logger.warning("SSE connection lost: %s. Reconnecting in %.0fs", e, backoff)
                self._on_disconnect_callback()
                if self._stop.wait(backoff):
                    return
                backoff = min(backoff * 2, 8.0)
            except Exception:
                logger.exception("Unexpected SSE error")
                self._on_disconnect_callback()
                if self._stop.wait(backoff):
                    return
                backoff = min(backoff * 2, 8.0)
