"""Integration test: DictationSession end-to-end with the ADR 0096 server-side protocol.

Drives a real DictationSession against the shared MockWsServer (raw PCM
binary frames -> done frame; no partials, no config frame).  Also runs the
corrections/commands post-processing that _finalize_dictation applies,
asserting the full transcript-shaping pipeline.
"""

from __future__ import annotations

import numpy as np

from voice_commander.dictation.postprocess import apply_commands, apply_corrections
from voice_commander.dictation.session import DictationSession
from voice_commander.dictation.vocab import Command, Correction, Vocabulary

from ._dictation_ws import MockWsServer


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def publish(self, event_type, data=None):
        self.events.append((event_type, data or {}))


def _audio(n: int = 8000) -> np.ndarray:
    return np.ones(n, dtype=np.float32)


def test_streaming_session_pastes_done_text() -> None:
    """Daemon dictates -> mock returns done with text -> vocab post-processing applied."""
    with MockWsServer(done_text="supa base new line then code") as srv:
        bus = _FakeBus()
        sess = DictationSession(
            bus, ws_url=srv.ws_url, end_word="done", idle_timeout_s=3.0
        )
        vocab = Vocabulary(
            corrections=(Correction(wrong="supa base", right="Supabase"),),
            commands=(Command(phrase="new line", action="newline"),),
        )
        sess.start(vocab)
        sess.handle_utterance(_audio(), "supa base")
        sess.handle_utterance(_audio(), "new line then code")
        raw = sess.finish()

    assert raw == "supa base new line then code"

    text = apply_corrections(raw, vocab.corrections)
    text = apply_commands(text, vocab.commands)
    assert text == "Supabase\nthen code"

    assert ("dictation.start", {}) in bus.events
    assert ("dictation.end", {"reason": "done"}) in bus.events
    assert srv.chunk_count == 2
    assert srv.end_received is True


def test_bytes_received_matches_audio_sent() -> None:
    """mock.bytes_received == sum of all PCM bytes sent by the session."""
    with MockWsServer(done_text="ok") as srv:
        bus = _FakeBus()
        sess = DictationSession(
            bus, ws_url=srv.ws_url, end_word="stop", idle_timeout_s=3.0
        )
        utts = [_audio(8000), _audio(4000), _audio(12000)]
        sess.start(Vocabulary())
        for a in utts:
            sess.handle_utterance(a, "speech")
        sess.finish()

    expected_bytes = sum(a.astype(np.float32).nbytes for a in utts)
    actual_bytes = sum(len(c) for c in srv.chunks_received)
    assert actual_bytes == expected_bytes


def test_no_partial_frames_server_sent() -> None:
    """Mock never sends partial frames; end_received confirms protocol compliance."""
    with MockWsServer(done_text="result") as srv:
        bus = _FakeBus()
        sess = DictationSession(
            bus, ws_url=srv.ws_url, end_word="stop", idle_timeout_s=3.0
        )
        sess.start(Vocabulary())
        sess.handle_utterance(_audio(), "some speech")
        sess.handle_utterance(_audio(), "more words")
        text = sess.finish()

    assert text == "result"
    assert srv.chunk_count > 0
    assert srv.end_received is True


def test_cap_timeout_sends_end_and_awaits_done() -> None:
    """max_dictation_s=0.1 fires the daemon cap; mock still replies with done."""
    with MockWsServer(done_text="capped text") as srv:
        bus = _FakeBus()
        sess = DictationSession(
            bus,
            ws_url=srv.ws_url,
            end_word="stop",
            idle_timeout_s=3.0,
            max_dictation_s=0.1,
        )
        sess.start(Vocabulary())
        text = sess.finish()

    assert text == "capped text"
    assert srv.end_received is True


def test_empty_silence_done_text() -> None:
    """Mock returns done.text="" (silence); finish() returns "" (not None)."""
    with MockWsServer(done_text="") as srv:
        bus = _FakeBus()
        sess = DictationSession(
            bus, ws_url=srv.ws_url, end_word="stop", idle_timeout_s=3.0
        )
        sess.start(Vocabulary())
        text = sess.finish()

    assert text == ""
    assert text is not None
    assert srv.end_received is True


def test_disconnect_mid_stream() -> None:
    """Mock closes after end frame without done; session returns "" gracefully."""
    with MockWsServer(drop_after_end=True) as srv:
        bus = _FakeBus()
        sess = DictationSession(
            bus, ws_url=srv.ws_url, end_word="stop", idle_timeout_s=2.0
        )
        sess.start(Vocabulary())
        sess.handle_utterance(_audio(), "some speech")
        text = sess.finish()

    assert text == ""


def test_connect_failure_returns_empty_string() -> None:
    """A bad ws_url (connection refused) -> finish() returns '' with error set."""
    bus = _FakeBus()
    sess = DictationSession(bus, ws_url="ws://localhost:1", idle_timeout_s=2.0)
    sess.start(Vocabulary())
    text = sess.finish()

    assert text == ""
    assert sess.error == "endpoint"
