"""Integration test: streaming DictationSession end-to-end with post-processing.

Drives a real DictationSession against an in-process mock WebSocket server,
then runs the same corrections/commands passes _finalize_dictation will run,
asserting the full transcript-shaping pipeline.
"""

from __future__ import annotations

import asyncio
import json
import threading

import numpy as np
from websockets.asyncio.server import serve

from voice_commander.dictation.postprocess import apply_commands, apply_corrections
from voice_commander.dictation.session import DictationSession
from voice_commander.dictation.vocab import Command, Correction, Vocabulary


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def publish(self, event_type, data=None):
        self.events.append((event_type, data or {}))


class _MockWsServer:
    """Replies one partial per binary chunk; partial texts taken from *replies*."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = replies
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._ready = threading.Event()
        self.url = ""

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    async def _serve(self) -> None:
        state = {"i": 0}

        async def handler(conn):
            async for message in conn:
                if isinstance(message, (bytes, bytearray)):
                    text = self._replies[min(state["i"], len(self._replies) - 1)]
                    state["i"] += 1
                    await conn.send(json.dumps({"type": "partial", "text": text}))

        server = await serve(handler, "localhost", 0)
        port = server.sockets[0].getsockname()[1]
        self.url = f"ws://localhost:{port}"
        self._ready.set()
        await asyncio.Future()

    def start(self) -> str:
        self._thread.start()
        assert self._ready.wait(timeout=5.0)
        return self.url


def _audio(n: int = 8000) -> np.ndarray:
    return np.ones(n, dtype=np.float32)


def test_streaming_session_then_postprocessing() -> None:
    """Chunks streamed → stabilised → corrections + commands applied."""
    # Partials chosen so LocalAgreement confirms "supa base new line then code".
    server = _MockWsServer(
        ["supa base", "base new line", "new line then code"]
    )
    url = server.start()
    bus = _FakeBus()
    sess = DictationSession(bus, ws_url=url, end_word="done", idle_timeout_s=3.0)

    vocab = Vocabulary(
        corrections=(Correction(wrong="supa base", right="Supabase"),),
        commands=(Command(phrase="new line", action="newline"),),
    )
    sess.start(vocab)
    sess.handle_utterance(_audio(), "supa base")
    sess.handle_utterance(_audio(), "base new line")
    sess.handle_utterance(_audio(), "new line then code")
    raw = sess.finish()

    # finish() returns the raw stabilised transcript.
    assert raw == "supa base new line then code"

    # _finalize_dictation will then apply corrections + commands.
    text = apply_corrections(raw, vocab.corrections)
    text = apply_commands(text, vocab.commands)
    assert text == "Supabase\nthen code"

    assert ("dictation.start", {}) in bus.events
    assert ("dictation.end", {"reason": "done"}) in bus.events
