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
    """Replies one partial per binary chunk; partial texts taken from *replies*.

    ``done_text`` is sent in the ``{"type":"done"}`` frame after the client
    sends ``{"type":"end"}`` (ADR 0094). When the ``end`` frame carries
    ``raw_transcript`` and ``done_text`` is empty, ``raw_transcript`` is used
    instead (ADR 0095 Change B — LLM-clean is a no-op in tests).
    """

    def __init__(self, replies: list[str], done_text: str = "") -> None:
        self._replies = replies
        self._done_text = done_text
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
                elif isinstance(message, str):
                    data = json.loads(message)
                    if data.get("type") == "end":
                        # ADR 0094: send done frame so finish() doesn't time out.
                        # When the test supplies a non-empty done_text, use it.
                        # Otherwise prefer raw_transcript from the end frame
                        # (ADR 0095 Change B — LLM-clean is a no-op in tests).
                        if self._done_text:
                            done_text = self._done_text
                        else:
                            done_text = data.get("raw_transcript", "")
                        await conn.send(json.dumps({"type": "done", "text": done_text}))

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
    # done_text is the transcript the proxy returns after LLM-clean (a no-op
    # in tests). This matches what LocalAgreement would commit across these
    # whole-window hypotheses given real growing-window audio (ADR 0094/0095).
    server = _MockWsServer(
        ["supa base", "base new line", "new line then code"],
        done_text="supa base new line then code",
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
