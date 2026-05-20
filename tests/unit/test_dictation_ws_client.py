"""Unit tests for the rewritten ws_client.stream_transcribe (ADR 0096 D8).

New protocol: raw float32 PCM binary frames → {"type":"end"} → {"type":"done"}.
No config frame, no partials, no segments, no on_partial, no prompt, no
raw_transcript_fn. A mock WebSocket is patched in-memory; no network required.

Tests are written to FAIL against the old implementation (pre-rewrite) and PASS
after the rewrite.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest

from voice_commander.dictation import ws_client


# ---------------------------------------------------------------------------
# Infrastructure: in-memory fake WebSocket
# ---------------------------------------------------------------------------

class _FakeWS:
    """In-memory WebSocket stub.

    Records every ``send()`` call in order. Replays a scripted sequence of
    replies via ``recv()``. When ``_close_on_recv`` is True the first
    ``recv()`` call raises ``ConnectionClosed`` instead of returning data.
    When ``_hang_on_recv`` is True ``recv()`` blocks forever (until the
    outer ``wait_for`` fires).
    """

    def __init__(
        self,
        replies: list[dict | bytes],
        *,
        close_on_recv: bool = False,
        hang_on_recv: bool = False,
    ) -> None:
        self.sent: list[object] = []
        self._replies = list(replies)
        self._close_on_recv = close_on_recv
        self._hang_on_recv = hang_on_recv

    async def send(self, data: object) -> None:
        self.sent.append(data)

    async def recv(self) -> str | bytes:
        if self._hang_on_recv:
            # Block forever so the caller's wait_for fires.
            await asyncio.Future()
        if self._close_on_recv:
            from websockets.exceptions import ConnectionClosed, ConnectionClosedOK
            raise ConnectionClosedOK(None, None)
        if not self._replies:
            raise AssertionError("recv() called with no scripted replies left")
        item = self._replies.pop(0)
        if isinstance(item, bytes):
            return item
        return json.dumps(item)


def _patch_connect(monkeypatch, fake: _FakeWS) -> None:
    """Replace ws_client.connect with an async context manager yielding *fake*."""

    @asynccontextmanager
    async def _fake_connect(_url):
        yield fake

    monkeypatch.setattr(ws_client, "connect", _fake_connect)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Helper: build a queue pre-loaded with chunks + optional sentinel
# ---------------------------------------------------------------------------

def _make_queue(*items: bytes | None) -> asyncio.Queue:
    q: asyncio.Queue[bytes | None] = asyncio.Queue()
    for item in items:
        q.put_nowait(item)
    return q


# ---------------------------------------------------------------------------
# Test 1: raw binary frames are sent in order; END sentinel triggers end frame
# ---------------------------------------------------------------------------

def test_sends_raw_pcm_chunks_then_end_frame(monkeypatch):
    """Chunks pushed to the queue are forwarded as binary frames; None triggers
    {"type":"end"} — no config frame precedes them."""
    chunk1 = b"\x00\x01\x02\x03" * 4  # 16 bytes of fake float32 PCM
    chunk2 = b"\x04\x05\x06\x07" * 4
    fake = _FakeWS([{"type": "done", "text": "hello"}])
    _patch_connect(monkeypatch, fake)

    async def _drive():
        q = _make_queue(chunk1, chunk2, None)
        return await ws_client.stream_transcribe("ws://x/ws", q, cap_timeout_s=5.0)

    _run(_drive())

    # First two sends must be binary PCM chunks, in order
    assert fake.sent[0] == chunk1
    assert fake.sent[1] == chunk2
    # Third send must be the end frame
    end_frame = json.loads(fake.sent[2])
    assert end_frame == {"type": "end"}
    # No extra sends
    assert len(fake.sent) == 3


# ---------------------------------------------------------------------------
# Test 2: done frame → return done.text
# ---------------------------------------------------------------------------

def test_returns_done_text_on_done_frame(monkeypatch):
    """Server replies {"type":"done","text":"hello"} → function returns "hello"."""
    fake = _FakeWS([{"type": "done", "text": "hello"}])
    _patch_connect(monkeypatch, fake)

    async def _drive():
        q = _make_queue(None)
        return await ws_client.stream_transcribe("ws://x/ws", q, cap_timeout_s=5.0)

    result = _run(_drive())
    assert result == "hello"


# ---------------------------------------------------------------------------
# Test 3: empty done.text is valid — returns "" not None
# ---------------------------------------------------------------------------

def test_returns_empty_string_for_silence_done(monkeypatch):
    """Server replies {"type":"done","text":""} for silence → returns "" not None."""
    fake = _FakeWS([{"type": "done", "text": ""}])
    _patch_connect(monkeypatch, fake)

    async def _drive():
        q = _make_queue(None)
        return await ws_client.stream_transcribe("ws://x/ws", q, cap_timeout_s=5.0)

    result = _run(_drive())
    assert result == ""
    assert result is not None


# ---------------------------------------------------------------------------
# Test 4: error frame → return None
# ---------------------------------------------------------------------------

def test_returns_none_on_error_frame(monkeypatch):
    """Server replies {"type":"error","message":"max duration exceeded"} → returns None."""
    fake = _FakeWS([{"type": "error", "message": "max duration exceeded"}])
    _patch_connect(monkeypatch, fake)

    async def _drive():
        q = _make_queue(None)
        return await ws_client.stream_transcribe("ws://x/ws", q, cap_timeout_s=5.0)

    result = _run(_drive())
    assert result is None


# ---------------------------------------------------------------------------
# Test 5: connection closed before done arrives → return None
# ---------------------------------------------------------------------------

def test_returns_none_on_connection_closed_before_done(monkeypatch):
    """Server closes after end frame with no done → returns None."""
    fake = _FakeWS([], close_on_recv=True)
    _patch_connect(monkeypatch, fake)

    async def _drive():
        q = _make_queue(None)
        return await ws_client.stream_transcribe(
            "ws://x/ws", q, cap_timeout_s=5.0, done_timeout_s=2.0
        )

    result = _run(_drive())
    assert result is None


# ---------------------------------------------------------------------------
# Test 6: recv timeout → return None
# ---------------------------------------------------------------------------

def test_returns_none_on_recv_timeout(monkeypatch):
    """Server hangs after end frame; done_timeout_s fires → returns None."""
    fake = _FakeWS([], hang_on_recv=True)
    _patch_connect(monkeypatch, fake)

    async def _drive():
        q = _make_queue(None)
        return await ws_client.stream_transcribe(
            "ws://x/ws", q, cap_timeout_s=5.0, done_timeout_s=0.05
        )

    result = _run(_drive())
    assert result is None


# ---------------------------------------------------------------------------
# Test 7: no config frame is sent — first send must be binary or the end frame
# ---------------------------------------------------------------------------

def test_no_config_frame_sent(monkeypatch):
    """Asserts the very first frame sent is binary PCM (not a JSON config frame)."""
    pcm_chunk = b"\xAB" * 64
    fake = _FakeWS([{"type": "done", "text": "ok"}])
    _patch_connect(monkeypatch, fake)

    async def _drive():
        q = _make_queue(pcm_chunk, None)
        return await ws_client.stream_transcribe("ws://x/ws", q, cap_timeout_s=5.0)

    _run(_drive())

    # First send must be bytes (the PCM chunk), NOT a JSON string
    first = fake.sent[0]
    first_repr = repr(first)[:60]
    assert isinstance(first, bytes), (
        f"First send was not binary PCM — got {type(first).__name__}: {first_repr}"
    )
    # And it must NOT be a JSON config frame
    try:
        parsed = json.loads(first)
        assert parsed.get("type") != "config", "First send was a config frame — must not send config"
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass  # raw bytes that can't be parsed as JSON — that's correct


# ---------------------------------------------------------------------------
# Test 8: cap_timeout_s fires → end frame is sent, then awaits done
# ---------------------------------------------------------------------------

def test_cap_timeout_triggers_end_frame(monkeypatch):
    """When cap_timeout_s fires the upload loop breaks and {"type":"end"} is sent."""

    # We'll use a queue that never delivers a chunk (simulating a very long dictation).
    # With a very short cap_timeout_s the loop should break and send the end frame.
    fake = _FakeWS([{"type": "done", "text": "capped"}])
    _patch_connect(monkeypatch, fake)

    async def _drive():
        q: asyncio.Queue[bytes | None] = asyncio.Queue()
        # Put nothing in the queue — wait_for will time out.
        return await ws_client.stream_transcribe(
            "ws://x/ws", q, cap_timeout_s=0.05, done_timeout_s=2.0
        )

    result = _run(_drive())

    # End frame must have been sent
    assert any(
        isinstance(s, str) and json.loads(s).get("type") == "end"
        for s in fake.sent
    ), f"No end frame found in sent frames: {fake.sent}"

    # And since the server replied with done, the result should be the done text
    assert result == "capped"


# ---------------------------------------------------------------------------
# Test 9: connection refused raises OSError (propagated to caller)
# ---------------------------------------------------------------------------

def test_connection_refused_propagates(monkeypatch):
    """A bad ws_url (connection refused) propagates OSError to the caller."""
    import socket

    # Find a port that is definitely not listening.
    s = socket.socket()
    s.bind(("localhost", 0))
    port = s.getsockname()[1]
    s.close()
    bad_url = f"ws://localhost:{port}/ws/transcribe"

    async def _drive():
        q = _make_queue(None)
        # Do NOT mock connect here — use the real one to get OSError.
        return await ws_client.stream_transcribe(bad_url, q, cap_timeout_s=1.0)

    with pytest.raises(OSError):
        _run(_drive())


# ---------------------------------------------------------------------------
# Test 10: connection closed during chunk send → return None
# ---------------------------------------------------------------------------

def test_returns_none_on_connection_closed_during_send(monkeypatch):
    """ConnectionClosed raised by ws.send (during upload) → return None."""
    from websockets.exceptions import ConnectionClosedOK

    class _ClosingWS(_FakeWS):
        async def send(self, data: object) -> None:
            self.sent.append(data)
            if isinstance(data, bytes):
                raise ConnectionClosedOK(None, None)

    fake = _ClosingWS([])
    _patch_connect(monkeypatch, fake)

    async def _drive():
        q = _make_queue(b"\x00" * 16, None)
        return await ws_client.stream_transcribe("ws://x/ws", q, cap_timeout_s=5.0)

    result = _run(_drive())
    assert result is None


# ---------------------------------------------------------------------------
# Test 11: unknown frame type while waiting for done is discarded; done eventually arrives
# ---------------------------------------------------------------------------

def test_unexpected_frame_discarded_done_still_returned(monkeypatch):
    """An unexpected frame type during done-wait is discarded; done still returned."""
    fake = _FakeWS([
        {"type": "mystery", "data": "???"},  # unexpected frame — should be discarded
        {"type": "done", "text": "final"},
    ])
    _patch_connect(monkeypatch, fake)

    async def _drive():
        q = _make_queue(None)
        return await ws_client.stream_transcribe(
            "ws://x/ws", q, cap_timeout_s=5.0, done_timeout_s=5.0
        )

    result = _run(_drive())
    assert result == "final"
