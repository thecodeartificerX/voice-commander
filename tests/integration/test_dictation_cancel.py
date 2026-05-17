"""Integration tests for spoken dictation cancel (ADR 0089 Change 3).

Mirrors test_dictation_pipeline.py — same scaffolding (_StubTranscriber,
_Transcription, _make_daemon). Tests that spoken "cancel" during active
dictation: (a) calls DictationSession.cancel(), (b) never submits audio to
the remote endpoint, (c) never pastes to clipboard, and (d) emits
dictation.end with {"reason": "cancel"}.

ALL tests drive utterances through daemon._process_utterance() — the real
daemon dispatch path — NOT via bare handle_utterance() calls. This ensures
the elif kind == "cancel": self._dictation_session.cancel() branch in
daemon.py actually executes.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

from voice_commander.dictation.session import DictationSession
from voice_commander.event_bus import EventBus
from voice_commander.feedback import CapturingFeedbackSink
from voice_commander.verb_router import VerbRouter, build_default_rules

if TYPE_CHECKING:
    from voice_commander.daemon import StreamingDaemon


@dataclass
class _Transcription:
    text: str
    confidence: float = 0.95
    no_speech_prob: float = 0.05


@dataclass
class _StubTranscriber:
    queue: list[Any]

    def load(self) -> None: ...
    def unload(self) -> None: ...
    def transcribe(self, _audio: np.ndarray) -> _Transcription:
        return self.queue.pop(0)


def _make_daemon(
    transcripts: list[_Transcription],
    tmp_path: Path,
    cancel_word: str = "cancel",
) -> tuple[StreamingDaemon, DictationSession, CapturingFeedbackSink, EventBus]:
    """Build a stripped-down daemon for dictation cancel testing."""
    from voice_commander.daemon import StreamingDaemon
    from voice_commander.dispatcher import Dispatcher
    from voice_commander.picker.registry import reset_global_picker_registry
    from voice_commander.registry import get_global_registry, reset_global_registry

    reset_global_registry()
    reset_global_picker_registry()

    registry = get_global_registry()
    bus = EventBus()
    feedback = CapturingFeedbackSink()
    dispatcher = Dispatcher(feedback=feedback, event_bus=bus)
    verb_router = VerbRouter(build_default_rules(), registry=registry, picker_registry=None)
    dictation_session = DictationSession(
        bus=bus,
        end_word="done",
        cancel_word=cancel_word,
    )

    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=None,
        transcriber=_StubTranscriber(queue=list(transcripts)),
        dispatcher=dispatcher,
        verb_router=verb_router,
        registry=registry,
        event_bus=bus,
        dictation_session=dictation_session,
        output_dir=str(tmp_path),
    )
    daemon._transcriber_ready.set()
    return daemon, dictation_session, feedback, bus


# ---------------------------------------------------------------------------
# Test 1: Spoken cancel — no POST, no paste, correct SSE reason
# ---------------------------------------------------------------------------


def test_spoken_cancel_no_post_no_paste(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Saying 'cancel' during active dictation must not POST audio and not paste.

    Drives utterances through _process_utterance() — the real dispatch path.
    """
    posted: list[bytes] = []

    def _fake_post(wav_bytes: bytes, endpoint: str, **kw: Any) -> str:
        posted.append(wav_bytes)
        return "SHOULD NOT APPEAR"

    monkeypatch.setattr("voice_commander.dictation.remote.post_audio", _fake_post)

    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[
            _Transcription("some words"),  # buffered
            _Transcription("cancel"),       # triggers cancel via _process_utterance
        ],
        tmp_path=tmp_path,
    )

    event_q = bus.subscribe()

    audio = np.zeros(16000, dtype=np.float32)
    dictation_session.start()

    # Drive through the real dispatch path
    daemon._process_utterance(audio)  # "some words" → buffered
    assert dictation_session.active is True

    daemon._process_utterance(audio)  # "cancel" → handle_utterance returns "cancel"
                                       # → daemon calls dictation_session.cancel()
    assert dictation_session.active is False, "session must be inactive after spoken cancel"

    # Finalize executor — must be a no-op (nothing submitted)
    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    assert posted == [], f"post_audio must NOT be called after spoken cancel; got {posted}"
    assert pasted == [], f"paste must NOT be called after spoken cancel; got {pasted}"

    # Drain event queue and check for dictation.end with reason="cancel"
    events: list[tuple[str, dict]] = []
    while not event_q.empty():
        ev = event_q.get_nowait()
        events.append((ev.type, ev.data))
    cancel_events = [
        (et, d) for (et, d) in events
        if et == "dictation.end" and d.get("reason") == "cancel"
    ]
    assert cancel_events, (
        f"Expected dictation.end {{reason:'cancel'}} SSE event; events: {events}"
    )


# ---------------------------------------------------------------------------
# Test 2: Spoken cancel does not buffer the cancel-word audio
# ---------------------------------------------------------------------------


def test_spoken_cancel_does_not_buffer_audio(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The cancel-word audio chunk is NOT appended to the buffer; after cancel()
    the buffer is empty.

    Drives utterances through _process_utterance() — the real dispatch path.
    Asserts: session inactive, buffer empty (take_audio() is None), no POST,
    no paste, dictation.end {reason:'cancel'} published.
    """
    posted: list[bytes] = []
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.remote.post_audio",
        lambda wav_bytes, endpoint, **kw: (posted.append(wav_bytes), "X")[1],
    )
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[
            _Transcription("hello world"),  # buffered (distinct audio)
            _Transcription("cancel"),       # triggers cancel via _process_utterance
        ],
        tmp_path=tmp_path,
    )

    event_q = bus.subscribe()

    audio_content = np.ones(8000, dtype=np.float32)
    audio_cancel = np.ones(8000, dtype=np.float32) * 99.0

    dictation_session.start()

    # "hello world" → buffered via _process_utterance (real dispatch path)
    daemon._process_utterance(audio_content)
    assert dictation_session.active is True

    # "cancel" → handle_utterance returns "cancel" → daemon calls cancel()
    daemon._process_utterance(audio_cancel)

    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    # Session must be inactive
    assert dictation_session.active is False, "session must be inactive after cancel"

    # Buffer must be empty — cancel() clears it; cancel-word audio was never buffered
    assert dictation_session.take_audio() is None, (
        "Buffer must be empty after cancel(); cancel-word audio must not have been buffered"
    )

    # No POST, no paste
    assert posted == [], f"post_audio must NOT be called after spoken cancel; got {posted}"
    assert pasted == [], f"paste must NOT be called after spoken cancel; got {pasted}"

    # Drain event queue and check for dictation.end with reason="cancel"
    events: list[tuple[str, dict]] = []
    while not event_q.empty():
        ev = event_q.get_nowait()
        events.append((ev.type, ev.data))
    cancel_events = [
        (et, d) for (et, d) in events
        if et == "dictation.end" and d.get("reason") == "cancel"
    ]
    assert cancel_events, (
        f"Expected dictation.end {{reason:'cancel'}} SSE event; events: {events}"
    )


# ---------------------------------------------------------------------------
# Test 3: Spoken cancel after hotkey-end pending → cancel wins
# ---------------------------------------------------------------------------


def test_spoken_cancel_wins_race_with_pending_hotkey_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If hotkey-end is pending (request_end set) and the next utterance is the
    cancel word, cancel wins: no audio submitted, dictation.end {reason:cancel}.

    Drives utterances through _process_utterance() — the real dispatch path.
    """
    posted: list[bytes] = []

    def _fake_post(wav_bytes: bytes, endpoint: str, **kw: Any) -> str:
        posted.append(wav_bytes)
        return "X"

    monkeypatch.setattr("voice_commander.dictation.remote.post_audio", _fake_post)
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[
            _Transcription("some audio"),  # buffered
            _Transcription("cancel"),       # spoken cancel
        ],
        tmp_path=tmp_path,
    )

    event_q = bus.subscribe()

    audio = np.zeros(16000, dtype=np.float32)
    dictation_session.start()

    # Buffer one utterance via the real dispatch path
    daemon._process_utterance(audio)   # "some audio" → buffered

    # Hotkey-end fires (simulates concurrent Ctrl press)
    dictation_session.request_end()
    assert dictation_session.pending_end is True  # sanity

    # Cancel utterance arrives AFTER pending_end is set — via real dispatch path
    daemon._process_utterance(audio)   # "cancel" → cancel() wins

    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    assert not dictation_session.active, "session must be inactive after cancel wins"
    assert not dictation_session.pending_end, "pending_end must be cleared by cancel"
    assert posted == [], f"post_audio must NOT be called when cancel wins; got {posted}"
    assert pasted == [], f"paste must NOT be called when cancel wins; got {pasted}"

    # Drain event queue and check for dictation.end with reason="cancel"
    events: list[tuple[str, dict]] = []
    while not event_q.empty():
        ev = event_q.get_nowait()
        events.append((ev.type, ev.data))
    cancel_events = [
        (et, d) for (et, d) in events
        if et == "dictation.end" and d.get("reason") == "cancel"
    ]
    assert cancel_events, (
        f"Expected dictation.end {{reason:'cancel'}} when cancel wins race; events: {events}"
    )


# ---------------------------------------------------------------------------
# Test 4: cancel_word == end_word collision → cancel disabled, word buffers
# ---------------------------------------------------------------------------


def test_cancel_word_collision_disables_spoken_cancel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When cancel_word == end_word, spoken cancel is disabled; the collision
    word acts as the end word (triggers finalization, not cancel)."""
    posted: list[bytes] = []
    monkeypatch.setattr(
        "voice_commander.dictation.remote.post_audio",
        lambda wav_bytes, endpoint, **kw: (posted.append(wav_bytes), "X")[1],
    )
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    # cancel_word == end_word == "done" — collision disables spoken cancel
    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[
            _Transcription("hello"),   # buffered
            _Transcription("done"),    # acts as end word (not cancel)
        ],
        tmp_path=tmp_path,
        cancel_word="done",  # collision with end_word
    )

    assert dictation_session._cancel_word is None, (
        "Collision must disable spoken cancel (_cancel_word=None)"
    )

    audio = np.zeros(16000, dtype=np.float32)
    dictation_session.start()

    # Drive through real dispatch path
    daemon._process_utterance(audio)   # "hello" → buffered
    daemon._process_utterance(audio)   # "done" → end word path (not cancel)

    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    # "done" triggered the end-word path → post and paste happened
    assert len(pasted) == 1, (
        f"'done' should have triggered end-word path (not cancel) when collision; "
        f"pasted={pasted}"
    )
    assert not dictation_session.active, "session must be inactive after end-word exit"
