"""Unit tests for stream_transcribe timing / TranscribeResult fields (ADR 0101).

Extends the existing ws_client test suite with assertions about the new
TranscribeResult return type — specifically the server_timings and
roundtrip_ms fields.  Uses the same in-memory _FakeWS pattern as
test_dictation_ws_client.py.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest

from voice_commander.dictation import ws_client
from voice_commander.dictation.ws_client import TranscribeResult


# ---------------------------------------------------------------------------
# Infrastructure (mirrors test_dictation_ws_client.py — kept local so tests
# are fully self-contained and can run independently)
# ---------------------------------------------------------------------------


class _FakeWS:
    """In-memory WebSocket stub (see test_dictation_ws_client.py for full docs)."""

    def __init__(self, replies: list[dict | bytes]) -> None:
        self.sent: list[object] = []
        self._replies = list(replies)

    async def send(self, data: object) -> None:
        self.sent.append(data)

    async def recv(self) -> str | bytes:
        if not self._replies:
            raise AssertionError("recv() called with no scripted replies left")
        item = self._replies.pop(0)
        if isinstance(item, bytes):
            return item
        return json.dumps(item)


def _patch_connect(monkeypatch, fake: _FakeWS) -> None:
    @asynccontextmanager
    async def _fake_connect(_url):
        yield fake

    monkeypatch.setattr(ws_client, "connect", _fake_connect)


def _run(coro):
    return asyncio.run(coro)


def _make_queue(*items: bytes | None) -> asyncio.Queue:
    q: asyncio.Queue[bytes | None] = asyncio.Queue()
    for item in items:
        q.put_nowait(item)
    return q


# ---------------------------------------------------------------------------
# Test 1: done frame WITH timings → server_timings populated, roundtrip_ms > 0
# ---------------------------------------------------------------------------


def test_done_with_timings_populates_server_timings(monkeypatch) -> None:
    """A done frame carrying a 'timings' dict → result.server_timings is populated."""
    server_timings = {
        "transcribe_ms": 800.0,
        "clean_ms": 120.0,
        "format_ms": 30.0,
        "server_total_ms": 950.0,
    }
    fake = _FakeWS([{"type": "done", "text": "hello", "timings": server_timings}])
    _patch_connect(monkeypatch, fake)

    async def _drive():
        q = _make_queue(None)
        return await ws_client.stream_transcribe("ws://x/ws", q, cap_timeout_s=5.0)

    result = _run(_drive())

    assert isinstance(result, TranscribeResult)
    assert result.text == "hello"
    assert result.server_timings == server_timings


def test_done_with_timings_roundtrip_ms_positive(monkeypatch) -> None:
    """roundtrip_ms is always > 0 when a done frame is received."""
    server_timings = {"server_total_ms": 500.0}
    fake = _FakeWS([{"type": "done", "text": "ok", "timings": server_timings}])
    _patch_connect(monkeypatch, fake)

    async def _drive():
        q = _make_queue(None)
        return await ws_client.stream_transcribe("ws://x/ws", q, cap_timeout_s=5.0)

    result = _run(_drive())

    assert isinstance(result, TranscribeResult)
    assert result.roundtrip_ms > 0


# ---------------------------------------------------------------------------
# Test 2: done frame WITHOUT timings → server_timings == {}, roundtrip_ms > 0
# ---------------------------------------------------------------------------


def test_done_without_timings_server_timings_empty_dict(monkeypatch) -> None:
    """A done frame with no 'timings' key → result.server_timings is {} (back-compat)."""
    fake = _FakeWS([{"type": "done", "text": "back-compat"}])
    _patch_connect(monkeypatch, fake)

    async def _drive():
        q = _make_queue(None)
        return await ws_client.stream_transcribe("ws://x/ws", q, cap_timeout_s=5.0)

    result = _run(_drive())

    assert isinstance(result, TranscribeResult)
    assert result.text == "back-compat"
    assert result.server_timings == {}


def test_done_without_timings_roundtrip_ms_positive(monkeypatch) -> None:
    """roundtrip_ms is measured even when the server sends no timings."""
    fake = _FakeWS([{"type": "done", "text": "ok"}])
    _patch_connect(monkeypatch, fake)

    async def _drive():
        q = _make_queue(None)
        return await ws_client.stream_transcribe("ws://x/ws", q, cap_timeout_s=5.0)

    result = _run(_drive())

    assert isinstance(result, TranscribeResult)
    assert result.roundtrip_ms > 0


# ---------------------------------------------------------------------------
# Test 3: done frame with timings=None → server_timings is {} (null-guard)
# ---------------------------------------------------------------------------


def test_done_with_timings_null_falls_back_to_empty_dict(monkeypatch) -> None:
    """timings=null in the JSON frame → server_timings normalised to {} (not None)."""
    fake = _FakeWS([{"type": "done", "text": "null timings", "timings": None}])
    _patch_connect(monkeypatch, fake)

    async def _drive():
        q = _make_queue(None)
        return await ws_client.stream_transcribe("ws://x/ws", q, cap_timeout_s=5.0)

    result = _run(_drive())

    assert isinstance(result, TranscribeResult)
    assert result.server_timings == {}


# ---------------------------------------------------------------------------
# Test 4: failure paths still return None (regression guard for ADR 0101 change)
# ---------------------------------------------------------------------------


def test_error_frame_still_returns_none(monkeypatch) -> None:
    """Error frame → None (unchanged from pre-ADR-0101 behaviour)."""
    fake = _FakeWS([{"type": "error", "message": "oops"}])
    _patch_connect(monkeypatch, fake)

    async def _drive():
        q = _make_queue(None)
        return await ws_client.stream_transcribe("ws://x/ws", q, cap_timeout_s=5.0)

    result = _run(_drive())
    assert result is None


def test_connection_closed_still_returns_none(monkeypatch) -> None:
    """ConnectionClosed while waiting for done → None (unchanged)."""
    from websockets.exceptions import ConnectionClosedOK

    class _ClosingWS(_FakeWS):
        async def recv(self) -> str | bytes:
            raise ConnectionClosedOK(None, None)

    fake = _ClosingWS([])
    _patch_connect(monkeypatch, fake)

    async def _drive():
        q = _make_queue(None)
        return await ws_client.stream_transcribe("ws://x/ws", q, cap_timeout_s=5.0)

    result = _run(_drive())
    assert result is None


# ---------------------------------------------------------------------------
# Test 5: TranscribeResult is a frozen dataclass (immutability check)
# ---------------------------------------------------------------------------


def test_transcribe_result_is_immutable() -> None:
    """TranscribeResult is a frozen dataclass — attribute assignment must raise."""
    r = TranscribeResult(text="hi", server_timings={}, roundtrip_ms=1.0)
    with pytest.raises((AttributeError, TypeError)):
        r.text = "mutated"  # type: ignore[misc]
