"""Unit + light-integration tests for the simplified DictationSession (ADR 0096).

A mock WebSocket server (websockets.asyncio.server.serve) stands in for the
real /ws/transcribe endpoint.  The session's own asyncio-loop thread connects
to it.  These tests exercise the new raw-PCM protocol: utterances are pushed
as float32 bytes; the server accumulates them and replies with one done frame.
No partial frames, no config handshake, no LocalAgreement.
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
from voice_commander.dictation.vocab import Vocabulary


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def publish(self, event_type, data=None):
        self.events.append((event_type, data or {}))


def _audio(n: int = 8000) -> np.ndarray:
    """Return a float32 ndarray representing n samples of audio."""
    return np.ones(n, dtype=np.float32)


class _MockWsServer:
    """Minimal mock WebSocket server for ADR 0096 protocol.

    Accumulates raw binary frames into a bytearray.  On receipt of
    ``{"type":"end"}`` sends ``{"type":"done","text":<done_text>}`` and closes.
    Exposes ``bytes_received`` and ``chunk_count`` for assertions.
    Does NOT send any partial frames; does NOT expect a config frame.
    """

    def __init__(self, done_text: str = "done text") -> None:
        self._done_text = done_text
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._ready = threading.Event()
        self.url = ""
        self._bytes_received: int = 0
        self._chunk_count: int = 0

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    async def _serve(self) -> None:
        async def handler(conn):
            async for message in conn:
                if isinstance(message, bytes):
                    self._bytes_received += len(message)
                    self._chunk_count += 1
                elif isinstance(message, str):
                    data = json.loads(message)
                    if data.get("type") == "end":
                        await conn.send(
                            json.dumps({"type": "done", "text": self._done_text})
                        )
                    # Any other JSON frame (unexpected) is ignored — no partial,
                    # no config frame expected from the new daemon.

        server = await serve(handler, "localhost", 0)
        port = server.sockets[0].getsockname()[1]
        self.url = f"ws://localhost:{port}"
        self._ready.set()
        await asyncio.Future()  # run forever

    def start(self) -> str:
        self._thread.start()
        assert self._ready.wait(timeout=5.0), "mock WS server did not start"
        return self.url

    @property
    def bytes_received(self) -> int:
        return self._bytes_received

    @property
    def chunk_count(self) -> int:
        return self._chunk_count


# ---------------------------------------------------------------------------
# Classification tests (no server needed)
# ---------------------------------------------------------------------------


def test_handle_utterance_when_inactive_is_buffered() -> None:
    s = DictationSession(_FakeBus(), ws_url="ws://localhost:1")
    assert s.handle_utterance(_audio(), "anything") == "buffered"


def test_end_word_returns_end_exact_standalone() -> None:
    server = _MockWsServer()
    url = server.start()
    sess = DictationSession(_FakeBus(), ws_url=url, end_word="done")
    sess.start(Vocabulary())
    assert sess.handle_utterance(_audio(), "I am done with this") == "buffered"
    assert sess.handle_utterance(_audio(), "Done.") == "end"
    sess.finish()


def test_handle_utterance_returns_cancel_for_cancel_word() -> None:
    server = _MockWsServer()
    url = server.start()
    sess = DictationSession(_FakeBus(), ws_url=url, end_word="done", cancel_word="cancel")
    sess.start(Vocabulary())
    assert sess.handle_utterance(_audio(), "cancel") == "cancel"
    sess.cancel()


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


# ---------------------------------------------------------------------------
# Audio byte enqueue tests (ADR 0096 D6)
# ---------------------------------------------------------------------------


def test_handle_utterance_pushes_audio_bytes_on_chunk_q_for_buffered() -> None:
    """Buffered utterances place raw float32 PCM bytes on the chunk queue."""
    server = _MockWsServer()
    url = server.start()
    sess = DictationSession(_FakeBus(), ws_url=url, end_word="stop", idle_timeout_s=2.0)
    sess.start(Vocabulary())

    audio = _audio(16000)  # 1 second of audio
    kind = sess.handle_utterance(audio, "some speech")
    assert kind == "buffered"

    # chunk_q should have exactly one item: the raw PCM bytes
    assert sess._chunk_q is not None
    assert sess._chunk_q.qsize() == 1
    item = sess._chunk_q.get_nowait()
    assert item == audio.astype(np.float32).tobytes()
    sess.cancel()


def test_handle_utterance_end_word_does_not_push_bytes() -> None:
    """End-word utterances are classification-only — no bytes on the queue."""
    server = _MockWsServer()
    url = server.start()
    sess = DictationSession(_FakeBus(), ws_url=url, end_word="done", idle_timeout_s=2.0)
    sess.start(Vocabulary())

    kind = sess.handle_utterance(_audio(), "done")
    assert kind == "end"
    assert sess._chunk_q is not None and sess._chunk_q.qsize() == 0
    sess.finish()


def test_handle_utterance_cancel_word_does_not_push_bytes() -> None:
    """Cancel-word utterances are classification-only — no bytes on the queue."""
    server = _MockWsServer()
    url = server.start()
    sess = DictationSession(_FakeBus(), ws_url=url, end_word="done", cancel_word="cancel", idle_timeout_s=2.0)
    sess.start(Vocabulary())

    kind = sess.handle_utterance(_audio(), "cancel")
    assert kind == "cancel"
    assert sess._chunk_q is not None and sess._chunk_q.qsize() == 0
    sess.cancel()


# ---------------------------------------------------------------------------
# Streaming round-trip (mock server)
# ---------------------------------------------------------------------------


def test_start_publishes_dictation_start() -> None:
    bus = _FakeBus()
    url = _MockWsServer().start()
    sess = DictationSession(bus, ws_url=url)
    sess.start(Vocabulary())
    assert ("dictation.start", {}) in bus.events
    sess.finish()


def test_finish_returns_done_text_from_ws_client() -> None:
    """finish() returns done.text from the server's done frame."""
    bus = _FakeBus()
    server = _MockWsServer(done_text="Hello, world.")
    url = server.start()
    sess = DictationSession(bus, ws_url=url, end_word="stop", idle_timeout_s=3.0)
    sess.start(Vocabulary())
    sess.handle_utterance(_audio(), "hello world")
    sess.handle_utterance(_audio(), "more speech")
    text = sess.finish()
    assert text == "Hello, world."
    assert ("dictation.end", {"reason": "done"}) in bus.events
    assert not sess.active


def test_finish_returns_empty_string_when_ws_client_returns_none() -> None:
    """finish() returns '' (not None) when the WS never connected (ADR 0096)."""
    bus = _FakeBus()
    sess = DictationSession(bus, ws_url="ws://localhost:1", idle_timeout_s=2.0)
    sess.start(Vocabulary())
    text = sess.finish()
    assert text == ""
    assert text is not None  # str, not None
    assert sess.error == "endpoint"


def test_finish_returns_empty_string_on_empty_done_text() -> None:
    """finish() returns '' when server's done.text is empty (silence/no speech)."""
    bus = _FakeBus()
    server = _MockWsServer(done_text="")
    url = server.start()
    sess = DictationSession(bus, ws_url=url, idle_timeout_s=3.0)
    sess.start(Vocabulary())
    text = sess.finish()
    assert text == ""
    assert text is not None


def test_cancel_drains_and_returns_no_text() -> None:
    """cancel() closes the WS, discards the transcript, publishes cancel event."""
    bus = _FakeBus()
    server = _MockWsServer(done_text="should not be returned")
    url = server.start()
    sess = DictationSession(bus, ws_url=url, idle_timeout_s=2.0)
    sess.start(Vocabulary())
    sess.handle_utterance(_audio(), "hello world")
    sess.cancel()
    assert not sess.active
    assert ("dictation.end", {"reason": "cancel"}) in bus.events
    # finish() after cancel is a no-op returning ""
    assert sess.finish() == ""


def test_request_end_then_finish_completes() -> None:
    """request_end() sets pending_end; finish() clears it and returns transcript."""
    server = _MockWsServer(done_text="speech text")
    url = server.start()
    sess = DictationSession(_FakeBus(), ws_url=url, idle_timeout_s=3.0)
    sess.start(Vocabulary())
    assert not sess.pending_end
    sess.request_end()
    assert sess.pending_end
    text = sess.finish()
    assert text == "speech text"
    assert not sess.pending_end


def test_session_construction_without_recorder_works() -> None:
    """DictationSession no longer requires set_recorder — construction is sufficient."""
    sess = DictationSession(_FakeBus(), ws_url="ws://localhost:1")
    # No set_recorder call — session must not have this method at all.
    assert not hasattr(sess, "set_recorder"), (
        "set_recorder must be removed; it was deleted in Phase 2 (ADR 0096)"
    )
    assert not hasattr(sess, "_recorder"), (
        "_recorder field must be removed in Phase 2 (ADR 0096)"
    )


def test_session_has_no_agreement_attribute() -> None:
    """LocalAgreement fields must be gone (ADR 0096 D7)."""
    sess = DictationSession(_FakeBus(), ws_url="ws://localhost:1")
    assert not hasattr(sess, "_agreement"), "_agreement must be removed"
    assert not hasattr(sess, "_agreement_lock"), "_agreement_lock must be removed"
    assert not hasattr(sess, "_confirmed"), "_confirmed must be removed"


def test_session_has_no_on_partial_method() -> None:
    """_on_partial is deleted — it was the LocalAgreement callback (ADR 0096 D7)."""
    sess = DictationSession(_FakeBus(), ws_url="ws://localhost:1")
    assert not hasattr(sess, "_on_partial"), "_on_partial must be removed"


def test_session_has_no_window_or_frame_tap_fields() -> None:
    """Frame tap and DictationWindow fields must be gone (ADR 0096 D7)."""
    sess = DictationSession(_FakeBus(), ws_url="ws://localhost:1")
    assert not hasattr(sess, "_window"), "_window must be removed"
    assert not hasattr(sess, "_warned_no_segments"), "_warned_no_segments must be removed"


def test_bytes_sent_to_server_match_audio_input() -> None:
    """Total bytes received by mock server equals sum of utterance pcm bytes."""
    server = _MockWsServer(done_text="ok")
    url = server.start()
    sess = DictationSession(_FakeBus(), ws_url=url, idle_timeout_s=4.0)
    sess.start(Vocabulary())

    utterances = [_audio(4000), _audio(8000), _audio(2000)]
    for utt in utterances:
        sess.handle_utterance(utt, "some words")
    sess.finish()

    expected_bytes = sum(utt.astype(np.float32).nbytes for utt in utterances)
    assert server.bytes_received == expected_bytes
    assert server.chunk_count == len(utterances)


# ---------------------------------------------------------------------------
# Deleted-method / attribute existence guards (Phase 2 regression blockers)
# ---------------------------------------------------------------------------


def test_build_raw_transcript_is_deleted() -> None:
    """_build_raw_transcript was the raw_transcript_fn for ws_client — now gone."""
    sess = DictationSession(_FakeBus(), ws_url="ws://localhost:1")
    assert not hasattr(sess, "_build_raw_transcript")


def test_no_window_step_cap_fields() -> None:
    """_window_step_ms and _window_cap_ms constructor params / fields are gone."""
    sess = DictationSession(_FakeBus(), ws_url="ws://localhost:1")
    assert not hasattr(sess, "_window_step_ms")
    assert not hasattr(sess, "_window_cap_ms")


def test_max_dictation_s_stored_correctly() -> None:
    """max_dictation_s constructor param is stored as _max_dictation_s."""
    sess = DictationSession(_FakeBus(), ws_url="ws://localhost:1", max_dictation_s=120.0)
    assert sess._max_dictation_s == 120.0
