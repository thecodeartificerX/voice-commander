"""Unit + light-integration tests for the streaming DictationSession.

A mock WebSocket server (websockets.asyncio.server.serve) stands in for the
real /ws/transcribe endpoint. The session's own asyncio-loop thread connects
to it. These tests exercise classification, the streaming round-trip, and the
finish/cancel exit paths.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading

import numpy as np
import pytest
from websockets.asyncio.server import serve

from voice_commander.dictation.session import DictationSession
from voice_commander.dictation.vocab import Correction, Vocabulary


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def publish(self, event_type, data=None):
        self.events.append((event_type, data or {}))


def _audio(n: int = 8000) -> np.ndarray:
    return np.ones(n, dtype=np.float32)


class _MockWsServer:
    """Runs websockets.serve on a background asyncio loop thread.

    Replies one ``partial`` frame per binary chunk; the partial text is the
    next entry of *replies* (last entry repeats). Exposes the bound URL.
    """

    def __init__(self, replies: list[str]) -> None:
        self._replies = replies
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._ready = threading.Event()
        self.url = ""
        self.configs: list[dict] = []

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    async def _serve(self) -> None:
        state = {"i": 0}

        async def handler(conn):
            async for message in conn:
                if isinstance(message, str):
                    data = json.loads(message)
                    if data.get("type") == "config":
                        self.configs.append(data)
                else:
                    text = self._replies[min(state["i"], len(self._replies) - 1)]
                    state["i"] += 1
                    await conn.send(json.dumps({"type": "partial", "text": text}))

        server = await serve(handler, "localhost", 0)
        port = server.sockets[0].getsockname()[1]
        self.url = f"ws://localhost:{port}"
        self._ready.set()
        await asyncio.Future()  # run forever

    def start(self) -> str:
        self._thread.start()
        assert self._ready.wait(timeout=5.0), "mock WS server did not start"
        return self.url


# --- classification (no server needed) ---


def test_handle_utterance_when_inactive_is_buffered() -> None:
    s = DictationSession(_FakeBus(), ws_url="ws://localhost:1")
    assert s.handle_utterance(_audio(), "anything") == "buffered"


def test_end_word_returns_end_exact_standalone() -> None:
    s = _MockWsServer([])
    url = s.start()
    sess = DictationSession(_FakeBus(), ws_url=url, end_word="done")
    sess.start(Vocabulary())
    assert sess.handle_utterance(_audio(), "I am done with this") == "buffered"
    assert sess.handle_utterance(_audio(), "Done.") == "end"
    sess.finish()


def test_cancel_word_collision_disables_cancel(caplog) -> None:
    bus = _FakeBus()
    with caplog.at_level(logging.WARNING, logger="voice_commander.dictation.session"):
        sess = DictationSession(
            bus, ws_url="ws://localhost:1", end_word="done", cancel_word="done"
        )
    assert sess._cancel_word is None
    assert any(
        "collision" in r.message.lower() or "cancel" in r.message.lower()
        for r in caplog.records
    )


def test_cancel_word_empty_disables_cancel(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="voice_commander.dictation.session"):
        sess = DictationSession(
            _FakeBus(), ws_url="ws://localhost:1", end_word="done", cancel_word=""
        )
    assert sess._cancel_word is None


# --- streaming round-trip (mock server) ---


def test_start_publishes_dictation_start() -> None:
    bus = _FakeBus()
    url = _MockWsServer([]).start()
    sess = DictationSession(bus, ws_url=url)
    sess.start(Vocabulary())
    assert ("dictation.start", {}) in bus.events
    sess.finish()


def test_streamed_utterances_are_stabilised_by_finish() -> None:
    """Two utterances → two partials → LocalAgreement → finish returns text."""
    bus = _FakeBus()
    server = _MockWsServer(["hello world", "world done"])
    url = server.start()
    sess = DictationSession(bus, ws_url=url, end_word="stop", idle_timeout_s=3.0)
    sess.start(Vocabulary())
    assert sess.handle_utterance(_audio(), "hello world") == "buffered"
    assert sess.handle_utterance(_audio(), "more speech") == "buffered"
    text = sess.finish()
    # commit("hello world")->[]; commit("world done")->["hello"];
    # finalize->["world","done"]
    assert text == "hello world done"
    assert ("dictation.end", {"reason": "done"}) in bus.events
    assert not sess.active


def test_finish_returns_empty_on_connect_failure() -> None:
    """A bad ws_url → asyncio thread records error → finish() returns ''."""
    bus = _FakeBus()
    sess = DictationSession(bus, ws_url="ws://localhost:1", idle_timeout_s=2.0)
    sess.start(Vocabulary())
    text = sess.finish()
    assert text == ""
    assert sess.error == "endpoint"


def test_cancel_discards_and_publishes_cancel() -> None:
    bus = _FakeBus()
    server = _MockWsServer(["hello world"])
    url = server.start()
    sess = DictationSession(bus, ws_url=url, idle_timeout_s=2.0)
    sess.start(Vocabulary())
    sess.handle_utterance(_audio(), "hello world")
    sess.cancel()
    assert not sess.active
    assert ("dictation.end", {"reason": "cancel"}) in bus.events
    # finish() after cancel is a no-op returning ""
    assert sess.finish() == ""


def test_config_frame_carries_prompt_from_vocab() -> None:
    """build_prompt(vocab) is sent in the config handshake."""
    server = _MockWsServer([])
    url = server.start()
    sess = DictationSession(_FakeBus(), ws_url=url, idle_timeout_s=2.0)
    vocab = Vocabulary(vocab=("Supabase", "Postgres"))
    sess.start(vocab)
    sess.handle_utterance(_audio(), "filler")  # one chunk forces a config send
    sess.finish()
    assert server.configs, "server received no config frame"
    cfg = server.configs[0]
    # Field name confirmed in Phase 2 — adjust if Phase 2 found a different name.
    assert cfg.get("initial_prompt") == "Supabase, Postgres"


def test_request_end_sets_pending_end() -> None:
    server = _MockWsServer([])
    url = server.start()
    sess = DictationSession(_FakeBus(), ws_url=url, idle_timeout_s=2.0)
    sess.start(Vocabulary())
    assert not sess.pending_end
    sess.request_end()
    assert sess.pending_end
    sess.finish()
    assert not sess.pending_end
