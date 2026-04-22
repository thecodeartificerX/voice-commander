from __future__ import annotations

import threading

from voice_sprite.event_client import SSEClient


def test_on_event_callback_called():
    """Verify on_event fires with parsed event type and data."""
    received = []
    client = SSEClient(
        base_url="http://localhost:9999",
        on_event=lambda t, d: received.append((t, d)),
        on_disconnect=lambda: None,
    )
    # Simulate calling _dispatch directly
    client._dispatch("tool_fired", {"name": "copy", "ts": 1.0})
    assert received == [("tool_fired", {"name": "copy", "ts": 1.0})]


def test_on_disconnect_callback_called():
    """Verify on_disconnect fires when connection drops."""
    disconnected = threading.Event()
    client = SSEClient(
        base_url="http://localhost:9999",
        on_event=lambda t, d: None,
        on_disconnect=lambda: disconnected.set(),
    )
    client._on_disconnect_callback()
    assert disconnected.is_set()


def test_stop_sets_event():
    client = SSEClient(
        base_url="http://localhost:9999",
        on_event=lambda t, d: None,
        on_disconnect=lambda: None,
    )
    client.stop()
    assert client._stop.is_set()
