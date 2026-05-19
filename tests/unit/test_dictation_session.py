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
    next entry of *replies* (last entry repeats). After receiving the
    ``{"type":"end"}`` frame it sends a ``done`` frame carrying *done_text*
    (default: ``"done text"``), mirroring the proxy contract (ADR 0094).
    Exposes the bound URL.
    """

    def __init__(self, replies: list[str], done_text: str = "done text") -> None:
        self._replies = replies
        self._done_text = done_text
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
                    elif data.get("type") == "end":
                        # Send the done frame, matching the proxy contract.
                        await conn.send(
                            json.dumps({"type": "done", "text": self._done_text})
                        )
                else:
                    text = self._replies[min(state["i"], len(self._replies) - 1)]
                    state["i"] += 1
                    await conn.send(json.dumps({"type": "partial", "text": text, "segments": []}))

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
    """Two utterances → two partials → done frame → finish returns done.text (ADR 0094)."""
    bus = _FakeBus()
    server = _MockWsServer(["hello world", "world done"], done_text="Hello, world done.")
    url = server.start()
    sess = DictationSession(bus, ws_url=url, end_word="stop", idle_timeout_s=3.0)
    sess.start(Vocabulary())
    assert sess.handle_utterance(_audio(), "hello world") == "buffered"
    assert sess.handle_utterance(_audio(), "more speech") == "buffered"
    text = sess.finish()
    # done frame carries LLM-cleaned text; LocalAgreement fallback not needed.
    assert text == "Hello, world done."
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


# --- done-frame / ADR 0094 tests ---


def test_finish_returns_done_text_when_done_frame_received() -> None:
    """finish() returns the proxy's done.text when a done frame is received (ADR 0094)."""
    bus = _FakeBus()
    server = _MockWsServer(["raw whisper"], done_text="LLM-cleaned text.")
    url = server.start()
    sess = DictationSession(bus, ws_url=url, idle_timeout_s=3.0)
    sess.start(Vocabulary())
    sess.handle_utterance(_audio(), "something")
    text = sess.finish()
    assert text == "LLM-cleaned text."


def test_finish_returns_empty_string_when_done_text_is_empty() -> None:
    """finish() returns '' (not None) when done.text is empty — whisper heard nothing (ADR 0094)."""
    bus = _FakeBus()
    server = _MockWsServer([], done_text="")
    url = server.start()
    sess = DictationSession(bus, ws_url=url, idle_timeout_s=3.0)
    sess.start(Vocabulary())
    # No utterances streamed — server sends done frame with empty text.
    text = sess.finish()
    assert text == ""
    assert text is not None  # empty string, not None


def test_finish_falls_back_to_local_agreement_when_no_done_frame() -> None:
    """finish() uses LocalAgreement output when _final_text is None (ADR 0094 fallback)."""
    sess = DictationSession(_FakeBus(), ws_url="ws://localhost:1", idle_timeout_s=0.5)
    # With a bad URL the asyncio thread raises OSError; _final_text stays None.
    sess.start(Vocabulary())
    text = sess.finish()
    # No done frame → fallback; nothing confirmed → empty string.
    assert text == ""
    assert sess._final_text is None


# --- ws_client done-frame tests (using _MockWsServer infrastructure) ---


def test_ws_client_stream_transcribe_returns_done_text() -> None:
    """stream_transcribe returns done.text when the server sends a done frame."""
    from voice_commander.dictation.ws_client import stream_transcribe

    server = _MockWsServer(["partial one"], done_text="LLM result")
    url = server.start()

    partials: list[str] = []
    chunk_q: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def run() -> str | None:
        # Push one WAV-like binary chunk then the end sentinel.
        await chunk_q.put(b"\x00" * 16)
        await chunk_q.put(None)
        return await stream_transcribe(
            url,
            "en",
            chunk_q,
            lambda t, s: partials.append(t),
            idle_timeout_s=3.0,
            done_timeout_s=5.0,
        )

    result = asyncio.run(run())
    assert result == "LLM result"
    assert "partial one" in partials


def test_ws_client_stream_transcribe_returns_none_on_timeout() -> None:
    """stream_transcribe returns None when the server never sends a done frame."""
    import json as _json

    from websockets.asyncio.server import serve as _serve

    from voice_commander.dictation.ws_client import stream_transcribe

    # A server that replies to partials but never sends done after end.
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    url_holder: list[str] = []

    def _run_server() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_serve_no_done())

    async def _serve_no_done() -> None:
        async def handler(conn) -> None:
            async for message in conn:
                if isinstance(message, bytes):
                    await conn.send(_json.dumps({"type": "partial", "text": "w"}))
                # Intentionally ignore "end" — no done frame sent.

        server = await _serve(handler, "localhost", 0)
        port = server.sockets[0].getsockname()[1]
        url_holder.append(f"ws://localhost:{port}")
        ready.set()
        await asyncio.Future()

    t = threading.Thread(target=_run_server, daemon=True)
    t.start()
    assert ready.wait(timeout=5.0)

    chunk_q: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def run() -> str | None:
        await chunk_q.put(b"\x00" * 16)
        await chunk_q.put(None)
        return await stream_transcribe(
            url_holder[0],
            "en",
            chunk_q,
            lambda _t, _s: None,
            idle_timeout_s=3.0,
            done_timeout_s=0.5,  # short timeout so the test is fast
        )

    result = asyncio.run(run())
    assert result is None


def test_ws_client_stream_transcribe_returns_empty_string_for_empty_done_text() -> None:
    """stream_transcribe returns '' (not None) when done.text is an empty string."""
    from voice_commander.dictation.ws_client import stream_transcribe

    server = _MockWsServer([], done_text="")
    url = server.start()

    chunk_q: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def run() -> str | None:
        await chunk_q.put(None)  # immediately end
        return await stream_transcribe(
            url,
            "en",
            chunk_q,
            lambda _t, _s: None,
            idle_timeout_s=3.0,
            done_timeout_s=5.0,
        )

    result = asyncio.run(run())
    assert result == ""
    assert result is not None


# ---------------------------------------------------------------------------
# Task 7: _segments_to_timed_words helper
# ---------------------------------------------------------------------------


def test_segments_to_timed_words_applies_offset():
    from voice_commander.dictation.session import _segments_to_timed_words

    segments = [
        {"start": 0.0, "end": 0.6, "text": "the quick"},
        {"start": 0.6, "end": 1.2, "text": "brown fox"},
    ]
    words = _segments_to_timed_words(segments, committed_offset_s=2.0)
    assert [w.text for w in words] == ["the", "quick", "brown", "fox"]
    # window-relative end-times shifted by the committed offset
    assert words[1].end_s == pytest.approx(2.6)   # 0.6 + 2.0
    assert words[3].end_s == pytest.approx(3.2)   # 1.2 + 2.0


def test_segments_to_timed_words_empty_when_no_segments():
    from voice_commander.dictation.session import _segments_to_timed_words

    assert _segments_to_timed_words([], committed_offset_s=0.0) == []


# ---------------------------------------------------------------------------
# Task 8: DictationSession — frame tap, window streaming, _on_partial LA-2
# ---------------------------------------------------------------------------


class _FakeRecorder:
    """Minimal StreamingRecorder stand-in — records frame-tap set/clear."""

    def __init__(self) -> None:
        self.tap = None

    def set_frame_tap(self, cb) -> None:
        self.tap = cb


def test_start_registers_frame_tap_finish_clears_it():
    rec = _FakeRecorder()
    bus = _FakeBus()
    sess = DictationSession(bus=bus, ws_url="ws://localhost:1", idle_timeout_s=0.5)
    sess.set_recorder(rec)
    sess.start(Vocabulary())
    assert rec.tap is not None
    sess.finish()
    assert rec.tap is None


def test_cancel_clears_frame_tap():
    rec = _FakeRecorder()
    bus = _FakeBus()
    sess = DictationSession(bus=bus, ws_url="ws://localhost:1", idle_timeout_s=0.5)
    sess.set_recorder(rec)
    sess.start(Vocabulary())
    sess.cancel()
    assert rec.tap is None


def test_on_partial_publishes_committed_prefix_transcript():
    """HUD transcript follows the committed prefix, not the raw hypothesis (OI-3)."""
    bus = _FakeBus()
    sess = DictationSession(bus=bus, ws_url="ws://localhost:1", idle_timeout_s=0.5)
    sess.set_recorder(_FakeRecorder())
    sess.start(Vocabulary())

    seg1 = [{"start": 0.0, "end": 0.6, "text": "the quick"}]
    sess._on_partial("the quick", seg1)
    seg2 = [{"start": 0.0, "end": 0.9, "text": "the quick brown"}]
    sess._on_partial("the quick brown", seg2)

    transcripts = [d.get("text") for (t, d) in bus.events if t == "transcript"]
    assert transcripts[-1] == "the quick"
    sess.cancel()


def test_handle_utterance_does_not_stream_buffered_audio():
    """VAD is endpointing-only — buffered utterances are NOT pushed as chunks."""
    sess = DictationSession(bus=_FakeBus(), ws_url="ws://localhost:1", idle_timeout_s=0.5)
    sess.set_recorder(_FakeRecorder())
    sess.start(Vocabulary())
    audio = np.zeros(16000, dtype=np.float32)
    kind = sess.handle_utterance(audio, "some words")
    assert kind == "buffered"
    assert sess._chunk_q is not None and sess._chunk_q.qsize() == 0
    sess.cancel()
