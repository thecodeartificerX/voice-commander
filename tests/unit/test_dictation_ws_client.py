"""Unit tests for ws_client.stream_transcribe — segments + raw_transcript wiring.

These tests use a fake in-memory WebSocket; no network. They assert the frame
contract (ADR 0095): partials carry a segments array to on_partial; the end
frame carries raw_transcript.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest

from voice_commander.dictation import ws_client


class _FakeWS:
    """In-memory WebSocket: records sends, replays a scripted reply list."""

    def __init__(self, replies: list[dict]) -> None:
        self.sent: list[object] = []
        self._replies = list(replies)

    async def send(self, data: object) -> None:
        self.sent.append(data)

    async def recv(self) -> str:
        if not self._replies:
            raise AssertionError("recv() called with no scripted replies left")
        return json.dumps(self._replies.pop(0))


def _patch_connect(monkeypatch, fake: _FakeWS) -> None:
    @asynccontextmanager
    async def _fake_connect(_url):
        yield fake

    monkeypatch.setattr(ws_client, "connect", _fake_connect)


def _run(coro):
    return asyncio.run(coro)


def test_partial_forwards_text_and_segments(monkeypatch):
    fake = _FakeWS(
        [
            {
                "type": "partial",
                "text": "the quick",
                "segments": [{"start": 0.0, "end": 0.6, "text": "the quick"}],
            },
            {"type": "done", "text": "The quick.", "raw": "the quick"},
        ]
    )
    _patch_connect(monkeypatch, fake)
    received: list[tuple[str, list]] = []

    async def _drive():
        q: asyncio.Queue = asyncio.Queue()
        await q.put(b"WAVDATA")
        await q.put(None)
        return await ws_client.stream_transcribe(
            "ws://x/ws", "en", q,
            lambda text, segs: received.append((text, segs)),
            idle_timeout_s=5.0,
        )

    result = _run(_drive())
    assert result == "The quick."
    assert received == [("the quick", [{"start": 0.0, "end": 0.6, "text": "the quick"}])]


def test_partial_segments_default_empty_when_omitted(monkeypatch):
    fake = _FakeWS(
        [
            {"type": "partial", "text": "hello"},  # no segments key
            {"type": "done", "text": "Hello."},
        ]
    )
    _patch_connect(monkeypatch, fake)
    received: list[tuple[str, list]] = []

    async def _drive():
        q: asyncio.Queue = asyncio.Queue()
        await q.put(b"WAVDATA")
        await q.put(None)
        return await ws_client.stream_transcribe(
            "ws://x/ws", "en", q,
            lambda text, segs: received.append((text, segs)),
            idle_timeout_s=5.0,
        )

    _run(_drive())
    assert received == [("hello", [])]


def test_end_frame_carries_raw_transcript(monkeypatch):
    fake = _FakeWS([{"type": "done", "text": "Cleaned.", "raw": "cleaned"}])
    _patch_connect(monkeypatch, fake)

    async def _drive():
        q: asyncio.Queue = asyncio.Queue()
        await q.put(None)  # immediate end — no chunks
        return await ws_client.stream_transcribe(
            "ws://x/ws", "en", q, lambda _t, _s: None,
            idle_timeout_s=5.0, raw_transcript_fn=lambda: "cleaned",
        )

    result = _run(_drive())
    assert result == "Cleaned."
    end_frames = [json.loads(s) for s in fake.sent if isinstance(s, str) and '"end"' in s]
    assert end_frames[-1] == {"type": "end", "raw_transcript": "cleaned"}


def test_end_frame_omits_raw_transcript_when_none(monkeypatch):
    fake = _FakeWS([{"type": "done", "text": ""}])
    _patch_connect(monkeypatch, fake)

    async def _drive():
        q: asyncio.Queue = asyncio.Queue()
        await q.put(None)
        return await ws_client.stream_transcribe(
            "ws://x/ws", "en", q, lambda _t, _s: None,
            idle_timeout_s=5.0, raw_transcript_fn=None,
        )

    _run(_drive())
    end_frames = [json.loads(s) for s in fake.sent if isinstance(s, str) and '"end"' in s]
    assert end_frames[-1] == {"type": "end"}
