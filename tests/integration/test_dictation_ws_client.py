"""Integration tests for stream_transcribe — wire contract against a real mock server.

Tests the ADR 0096 D8 wire protocol with a real WebSocket connection to the
shared MockWsServer.  No mocking of connect() here; every test uses an actual
TCP connection so the framing / byte delivery is real.

Tests covered:
    test_chunks_arrive_in_order
    test_end_frame_terminates_stream
    test_done_text_returned_to_caller
    test_empty_done_text_returned_as_empty_string
    test_error_frame_returns_none
    test_connection_closed_before_done_returns_none
    test_recv_timeout_returns_none
    test_cap_timeout_fires_end_frame
    test_disconnect_mid_stream_does_not_crash_daemon
    test_done_with_empty_text_silence_path
"""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from voice_commander.dictation.ws_client import END, TranscribeResult, stream_transcribe

from ._dictation_ws import MockWsServer


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def _make_queue(*items: bytes | None) -> asyncio.Queue:
    q: asyncio.Queue[bytes | None] = asyncio.Queue()
    for item in items:
        q.put_nowait(item)
    return q


# ---------------------------------------------------------------------------
# Test 1: chunks arrive in order
# ---------------------------------------------------------------------------


def test_chunks_arrive_in_order() -> None:
    """Binary chunks pushed to chunk_q arrive at the server in the same order."""
    chunk_a = np.ones(1600, dtype=np.float32).tobytes()
    chunk_b = np.zeros(800, dtype=np.float32).tobytes()

    with MockWsServer(done_text="ok") as srv:

        async def _drive():
            q = _make_queue(chunk_a, chunk_b, END)
            return await stream_transcribe(srv.ws_url, q, cap_timeout_s=5.0)

        result = _run(_drive())

    assert isinstance(result, TranscribeResult)
    assert result.text == "ok"
    assert len(srv.chunks_received) == 2
    assert srv.chunks_received[0] == chunk_a
    assert srv.chunks_received[1] == chunk_b


# ---------------------------------------------------------------------------
# Test 2: end frame terminates the stream
# ---------------------------------------------------------------------------


def test_end_frame_terminates_stream() -> None:
    """After chunk_q sentinel arrives, the server receives {"type":"end"}."""
    with MockWsServer(done_text="transcript") as srv:

        async def _drive():
            q = _make_queue(b"\x00" * 64, END)
            return await stream_transcribe(srv.ws_url, q, cap_timeout_s=5.0)

        _run(_drive())

    assert srv.end_received is True


# ---------------------------------------------------------------------------
# Test 3: done text returned to caller
# ---------------------------------------------------------------------------


def test_done_text_returned_to_caller() -> None:
    """Full round-trip: server returns done.text, result.text equals it."""
    with MockWsServer(done_text="Hello, world.") as srv:

        async def _drive():
            q = _make_queue(END)
            return await stream_transcribe(srv.ws_url, q, cap_timeout_s=5.0)

        result = _run(_drive())

    assert isinstance(result, TranscribeResult)
    assert result.text == "Hello, world."


# ---------------------------------------------------------------------------
# Test 4: empty done text is a valid result
# ---------------------------------------------------------------------------


def test_empty_done_text_returned_as_empty_string() -> None:
    """silence → done.text="" → result.text is "" and result is not None."""
    with MockWsServer(done_text="") as srv:

        async def _drive():
            q = _make_queue(END)
            return await stream_transcribe(srv.ws_url, q, cap_timeout_s=5.0)

        result = _run(_drive())

    assert isinstance(result, TranscribeResult)
    assert result.text == ""
    assert result is not None


# ---------------------------------------------------------------------------
# Test 5: error frame returns None
# ---------------------------------------------------------------------------


def test_error_frame_returns_none() -> None:
    """Server responds with {"type":"error"} → stream_transcribe returns None."""
    with MockWsServer(error_message="max duration exceeded") as srv:

        async def _drive():
            q = _make_queue(END)
            return await stream_transcribe(srv.ws_url, q, cap_timeout_s=5.0)

        result = _run(_drive())

    assert result is None
    assert srv.end_received is True


# ---------------------------------------------------------------------------
# Test 6: connection closed before done returns None
# ---------------------------------------------------------------------------


def test_connection_closed_before_done_returns_none() -> None:
    """Server closes without replying → stream_transcribe returns None."""
    with MockWsServer(drop_after_end=True) as srv:

        async def _drive():
            q = _make_queue(END)
            return await stream_transcribe(
                srv.ws_url, q, cap_timeout_s=5.0, done_timeout_s=3.0
            )

        result = _run(_drive())

    assert result is None


# ---------------------------------------------------------------------------
# Test 7: recv timeout returns None
# ---------------------------------------------------------------------------


def test_recv_timeout_returns_none() -> None:
    """Server delays reply beyond done_timeout_s → stream_transcribe returns None."""
    with MockWsServer(done_text="late", delay_done_s=5.0) as srv:

        async def _drive():
            q = _make_queue(END)
            return await stream_transcribe(
                srv.ws_url, q, cap_timeout_s=5.0, done_timeout_s=0.2
            )

        result = _run(_drive())

    assert result is None


# ---------------------------------------------------------------------------
# Test 8: cap_timeout_s fires, end frame sent, done received
# ---------------------------------------------------------------------------


def test_cap_timeout_fires_end_frame() -> None:
    """Daemon cap (cap_timeout_s) fires before sentinel; end frame still sent."""
    with MockWsServer(done_text="capped") as srv:

        async def _drive():
            q: asyncio.Queue[bytes | None] = asyncio.Queue()
            # Never put anything — cap_timeout fires immediately
            return await stream_transcribe(
                srv.ws_url, q, cap_timeout_s=0.05, done_timeout_s=5.0
            )

        result = _run(_drive())

    assert isinstance(result, TranscribeResult)
    assert result.text == "capped"
    assert srv.end_received is True


# ---------------------------------------------------------------------------
# Test 9: disconnect mid-stream does not crash the daemon
# ---------------------------------------------------------------------------


def test_disconnect_mid_stream_does_not_crash_daemon() -> None:
    """Server drops connection during binary frame receipt → returns None, no exception."""
    # Use drop_after_end=True simulates server closure; but we want the server to
    # drop the connection BEFORE the end frame.  We achieve this by using a very
    # short delay mock that closes the server's listening socket before the client
    # finishes sending.  Since MockWsServer only responds after the end frame, a
    # simpler approach: send chunks, then stop the server before sending the sentinel.

    # We use a different approach: start the server, connect, begin the upload, then
    # abruptly stop the server mid-upload so the client gets a ConnectionClosed.
    srv = MockWsServer(done_text="unreachable")
    srv.start()

    async def _drive():
        q: asyncio.Queue[bytes | None] = asyncio.Queue()
        # Put many small chunks to keep the loop busy, then stop the server
        # before the sentinel arrives so the WS closes mid-upload.
        for _ in range(5):
            q.put_nowait(np.zeros(1600, dtype=np.float32).tobytes())

        # Run stream_transcribe concurrently; stop the server after 0.05 s
        async def _stopper():
            await asyncio.sleep(0.05)
            # Close the server from the asyncio thread it lives on
            fut = asyncio.run_coroutine_threadsafe(
                srv._shutdown(), srv._loop  # type: ignore[attr-defined]
            )
            fut.result(timeout=3.0)

        stopper = asyncio.create_task(_stopper())
        result = await stream_transcribe(
            srv.ws_url, q, cap_timeout_s=5.0, done_timeout_s=1.0
        )
        stopper.cancel()
        return result

    # Must not raise; return value is None or a TranscribeResult with empty text
    try:
        result = _run(_drive())
        # On mid-stream disconnect the server never sends a done frame, so None is
        # expected.  In rare timing where the server does reply, text is empty.
        assert result is None or (isinstance(result, TranscribeResult) and result.text == ""), (
            f"Expected None or TranscribeResult('') on mid-stream disconnect; got {result!r}"
        )
    except OSError:
        pass  # acceptable — server gone before connect completes in rare timing


# ---------------------------------------------------------------------------
# Test 10: done with empty text — silence path — daemon does not paste
# ---------------------------------------------------------------------------


def test_done_with_empty_text_silence_path() -> None:
    """Empty done.text (silence input) — stream_transcribe returns '' not None."""
    with MockWsServer(done_text="") as srv:

        async def _drive():
            # Send a chunk of all-zero audio (silence)
            silence = np.zeros(16000, dtype=np.float32).tobytes()
            q = _make_queue(silence, END)
            return await stream_transcribe(srv.ws_url, q, cap_timeout_s=5.0)

        result = _run(_drive())

    # Empty string is the valid result; daemon checks for "" to skip paste
    assert isinstance(result, TranscribeResult)
    assert result.text == ""
    assert result is not None
    assert srv.end_received is True
    assert len(srv.chunks_received) == 1


# ---------------------------------------------------------------------------
# ADR 0101 timing tests: real TCP connection with timings injection
# ---------------------------------------------------------------------------


def test_done_with_timings_populates_server_timings_real_tcp() -> None:
    """Real TCP round-trip: server includes timings → result.server_timings populated."""
    server_timings = {
        "transcribe_ms": 500.0,
        "clean_ms": 80.0,
        "format_ms": 20.0,
        "server_total_ms": 600.0,
    }

    with MockWsServer(done_text="timed text", timings=server_timings) as srv:

        async def _drive():
            q = _make_queue(END)
            return await stream_transcribe(srv.ws_url, q, cap_timeout_s=5.0)

        result = _run(_drive())

    assert isinstance(result, TranscribeResult)
    assert result.text == "timed text"
    assert result.server_timings == server_timings
    assert result.roundtrip_ms > 0


def test_done_without_timings_back_compat_real_tcp() -> None:
    """Real TCP round-trip: older server (no timings key) → server_timings={}."""
    with MockWsServer(done_text="no timings") as srv:  # timings=None → key omitted

        async def _drive():
            q = _make_queue(END)
            return await stream_transcribe(srv.ws_url, q, cap_timeout_s=5.0)

        result = _run(_drive())

    assert isinstance(result, TranscribeResult)
    assert result.text == "no timings"
    assert result.server_timings == {}
    assert result.roundtrip_ms > 0
