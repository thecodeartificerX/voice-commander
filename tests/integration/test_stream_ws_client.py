"""Integration tests for the streaming dictation WebSocket client."""

from __future__ import annotations

import asyncio
import json

import pytest
from websockets.asyncio.server import serve

from voice_commander.dictation_stream.ws_client import stream_transcribe


@pytest.fixture
async def mock_server():
    """An in-process WS server that replies one partial per binary chunk."""
    state = {"configs": [], "chunks": 0, "ended": False, "behaviour": "ok"}

    async def handler(conn):
        async for message in conn:
            if isinstance(message, str):
                data = json.loads(message)
                if data["type"] == "config":
                    state["configs"].append(data)
                elif data["type"] == "end":
                    state["ended"] = True
            else:
                state["chunks"] += 1
                if state["behaviour"] == "error":
                    await conn.send(json.dumps({"type": "error", "detail": "boom"}))
                else:
                    await conn.send(
                        json.dumps({"type": "partial", "text": f"word{state['chunks']}"})
                    )

    async with serve(handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        yield f"ws://localhost:{port}", state


async def test_streams_chunks_collects_partials_and_ends(mock_server):
    url, state = mock_server
    chunk_q: asyncio.Queue = asyncio.Queue()
    await chunk_q.put(b"chunk-a")
    await chunk_q.put(b"chunk-b")
    await chunk_q.put(None)
    partials: list[str] = []

    await stream_transcribe(url, "en", chunk_q, partials.append, idle_timeout_s=5.0)

    assert state["configs"] == [{"type": "config", "language": "en"}]
    assert state["chunks"] == 2
    assert state["ended"] is True
    assert partials == ["word1", "word2"]


async def test_server_error_frame_stops_streaming(mock_server):
    url, state = mock_server
    state["behaviour"] = "error"
    chunk_q: asyncio.Queue = asyncio.Queue()
    await chunk_q.put(b"chunk-a")
    await chunk_q.put(b"chunk-b")
    await chunk_q.put(None)
    partials: list[str] = []

    await stream_transcribe(url, "en", chunk_q, partials.append, idle_timeout_s=5.0)

    assert state["chunks"] == 1  # stopped after the first chunk's error reply
    assert partials == []
    assert state["ended"] is True  # error path still sends the end frame


async def test_idle_timeout_ends_session(mock_server):
    url, state = mock_server
    chunk_q: asyncio.Queue = asyncio.Queue()  # never fed — forces the idle path
    partials: list[str] = []

    await stream_transcribe(url, "en", chunk_q, partials.append, idle_timeout_s=0.2)

    assert state["ended"] is True
    assert partials == []


async def test_connect_failure_raises_oserror():
    chunk_q: asyncio.Queue = asyncio.Queue()
    await chunk_q.put(None)
    with pytest.raises(OSError):
        await stream_transcribe(
            "ws://localhost:1", "en", chunk_q, lambda _t: None, idle_timeout_s=1.0
        )
