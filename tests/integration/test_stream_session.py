"""Integration test: full streaming dictation pipeline, mic + WS faked."""

from __future__ import annotations

import asyncio
import json
import queue

import numpy as np
import pytest
from websockets.asyncio.server import serve

import voice_commander.dictation_stream.sink as sink_mod
from voice_commander.dictation_stream.chunker import Chunker
from voice_commander.dictation_stream.config import StreamDictationConfig
from voice_commander.dictation_stream.session import StreamSession


class _FakeCapture:
    """Feeds canned raw blocks to raw_q on start, the None sentinel on stop."""

    native_rate = 16_000

    def __init__(self, raw_q: queue.Queue, blocks: list[np.ndarray]) -> None:
        self._raw_q = raw_q
        self._blocks = blocks

    def start(self) -> None:
        for block in self._blocks:
            self._raw_q.put(block)

    def stop(self) -> None:
        self._raw_q.put(None)


class _FakeVad:
    """Emits a 1 s utterance once per frame fed."""

    def process(self, frame):
        return np.ones(16_000, dtype=np.float32)


@pytest.fixture
async def mock_server():
    """WS server: replies a two-word partial per chunk so LocalAgreement runs."""
    replies = ["hello world", "world done"]
    state = {"i": 0}

    async def handler(conn):
        async for message in conn:
            if isinstance(message, (bytes, bytearray)):
                text = replies[min(state["i"], len(replies) - 1)]
                state["i"] += 1
                await conn.send(json.dumps({"type": "partial", "text": text}))

    async with serve(handler, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        yield f"ws://localhost:{port}"


async def test_full_pipeline_pastes_stabilised_transcript(mock_server, monkeypatch):
    pasted: list[str] = []
    monkeypatch.setattr(sink_mod, "paste_via_clipboard", pasted.append)

    config = StreamDictationConfig(ws_url=mock_server, idle_timeout_seconds=3)
    raw_q: queue.Queue = queue.Queue()
    # Two raw blocks of one 512-frame each -> _FakeVad emits one chunk per block.
    blocks = [np.zeros(512, dtype=np.float32), np.zeros(512, dtype=np.float32)]

    session = StreamSession(
        config,
        capture=_FakeCapture(raw_q, blocks),
        chunker_factory=lambda rate: Chunker(rate, min_chunk_seconds=1, vad=_FakeVad()),
        raw_q=raw_q,
    )
    session.start()
    # stop() blocks on thread joins — run it off the event loop so the mock
    # server keeps serving.
    pasted_text = await asyncio.get_running_loop().run_in_executor(None, session.stop)

    # commit("hello world")->[]; commit("world done")->["hello"]; finalize->["world","done"]
    assert pasted_text == "hello world done"
    assert pasted == ["hello world done"]
