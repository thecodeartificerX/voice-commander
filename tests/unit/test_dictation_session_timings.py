"""Unit tests for DictationSession.get_timings() (ADR 0101).

Uses a minimal in-process mock WebSocket server that can inject a 'timings'
dict into the done frame.  Validates that get_timings() returns the correct
shape after finish(), and that it returns the zero/None shape when no done
frame arrived.
"""

from __future__ import annotations

import asyncio
import json
import threading

import numpy as np
import pytest
from websockets.asyncio.server import serve

from voice_commander.dictation.session import DictationSession
from voice_commander.dictation.vocab import Vocabulary


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def publish(self, event_type, data=None):
        self.events.append((event_type, data or {}))


def _audio(n: int = 8000) -> np.ndarray:
    return np.ones(n, dtype=np.float32)


# ---------------------------------------------------------------------------
# Mock WS server — supports optional timings injection in the done frame
# ---------------------------------------------------------------------------


class _MockWsServer:
    """Minimal mock WS server that injects an optional 'timings' dict.

    Parameters
    ----------
    done_text:
        text field in the done frame.
    timings:
        Optional dict to include as the 'timings' key in the done frame.
        Pass None (default) to omit the key (simulates older server).
    drop_after_end:
        If True, close without sending a done frame (tests no-done path).
    """

    def __init__(
        self,
        done_text: str = "done text",
        timings: dict | None = None,
        drop_after_end: bool = False,
    ) -> None:
        self._done_text = done_text
        self._timings = timings
        self._drop_after_end = drop_after_end
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._ready = threading.Event()
        self.url = ""

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    async def _serve(self) -> None:
        async def handler(conn):
            async for message in conn:
                if isinstance(message, str):
                    data = json.loads(message)
                    if data.get("type") == "end":
                        if self._drop_after_end:
                            break
                        frame: dict = {"type": "done", "text": self._done_text}
                        if self._timings is not None:
                            frame["timings"] = self._timings
                        await conn.send(json.dumps(frame))
                        break

        server = await serve(handler, "localhost", 0)
        port = server.sockets[0].getsockname()[1]
        self.url = f"ws://localhost:{port}"
        self._ready.set()
        await asyncio.Future()  # run forever

    def start(self) -> str:
        self._thread.start()
        assert self._ready.wait(timeout=5.0), "mock WS server did not start"
        return self.url


# ---------------------------------------------------------------------------
# Test 1: get_timings() after successful finish — server timings present
# ---------------------------------------------------------------------------


def test_get_timings_with_server_timings() -> None:
    """After a successful finish with server timings, get_timings returns full dict."""
    server_timings = {
        "transcribe_ms": 700.0,
        "clean_ms": 100.0,
        "format_ms": 20.0,
        "server_total_ms": 820.0,
    }
    srv = _MockWsServer(done_text="hello", timings=server_timings)
    url = srv.start()

    sess = DictationSession(_FakeBus(), ws_url=url, idle_timeout_s=3.0)
    sess.start(Vocabulary())
    sess.handle_utterance(_audio(), "hello world")
    text = sess.finish()  # joins asyncio thread — happens-before barrier

    timings = sess.get_timings()

    assert text == "hello"
    assert timings["server"] == server_timings
    assert timings["roundtrip_ms"] is not None
    assert timings["roundtrip_ms"] > 0


# ---------------------------------------------------------------------------
# Test 2: get_timings() after finish with no server timings (back-compat)
# ---------------------------------------------------------------------------


def test_get_timings_without_server_timings() -> None:
    """After finish with a done frame carrying no timings, server_timings is {}."""
    srv = _MockWsServer(done_text="no timings")  # timings=None → key omitted
    url = srv.start()

    sess = DictationSession(_FakeBus(), ws_url=url, idle_timeout_s=3.0)
    sess.start(Vocabulary())
    text = sess.finish()

    timings = sess.get_timings()

    assert text == "no timings"
    assert timings["server"] == {}
    assert timings["roundtrip_ms"] > 0  # still measured


# ---------------------------------------------------------------------------
# Test 3: get_timings() when no done frame arrived → server={}, roundtrip_ms=None
# ---------------------------------------------------------------------------


def test_get_timings_no_done_frame() -> None:
    """When no done frame arrived (connection refused), get_timings returns None fields."""
    # Connect to a closed port so the asyncio thread fails at connect.
    sess = DictationSession(_FakeBus(), ws_url="ws://localhost:1", idle_timeout_s=2.0)
    sess.start(Vocabulary())
    sess.finish()  # returns "" due to endpoint error

    timings = sess.get_timings()

    assert timings["server"] == {}
    assert timings["roundtrip_ms"] is None


# ---------------------------------------------------------------------------
# Test 4: get_timings() shape — always returns exactly two keys
# ---------------------------------------------------------------------------


def test_get_timings_has_expected_keys() -> None:
    """get_timings() always returns a dict with 'server' and 'roundtrip_ms' keys."""
    sess = DictationSession(_FakeBus(), ws_url="ws://localhost:1", idle_timeout_s=1.0)
    sess.start(Vocabulary())
    sess.finish()

    timings = sess.get_timings()

    assert set(timings.keys()) == {"server", "roundtrip_ms"}


# ---------------------------------------------------------------------------
# Test 5: cancel path — session never started → roundtrip_ms is None
# ---------------------------------------------------------------------------


def test_get_timings_before_start_roundtrip_ms_none() -> None:
    """Before any start()/finish(), get_timings roundtrip_ms is None (initial state)."""
    sess = DictationSession(_FakeBus(), ws_url="ws://localhost:1", idle_timeout_s=1.0)
    # Do NOT call start() — fields are at their initialised None values.
    timings = sess.get_timings()

    assert timings["roundtrip_ms"] is None
    assert timings["server"] == {}


# ---------------------------------------------------------------------------
# Test 6: drop_after_end (server closes without done) → roundtrip_ms None
# ---------------------------------------------------------------------------


def test_get_timings_server_drops_after_end() -> None:
    """Server closes without done frame → roundtrip_ms is None."""
    srv = _MockWsServer(drop_after_end=True)
    url = srv.start()

    sess = DictationSession(_FakeBus(), ws_url=url, idle_timeout_s=2.0)
    sess.start(Vocabulary())
    sess.finish()

    timings = sess.get_timings()

    assert timings["roundtrip_ms"] is None
    assert timings["server"] == {}
