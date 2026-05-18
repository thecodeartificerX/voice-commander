"""Integration tests for spoken dictation cancel (streaming, ADR 0092).

Drives utterances through daemon._process_utterance — the real dispatch path —
so the `elif kind == "cancel"` branch in daemon.py actually executes. Asserts
spoken "cancel" during active dictation: (a) calls DictationSession.cancel(),
(b) never pastes, and (c) emits dictation.end {"reason": "cancel"}.
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

from ._dictation_ws import MockWsServer

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
    ws_url: str,
) -> tuple["StreamingDaemon", DictationSession, CapturingFeedbackSink, EventBus]:
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
        bus=bus, ws_url=ws_url, end_word="done", cancel_word="cancel", idle_timeout_s=3.0
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
        dictation_ws_url=ws_url,
        output_dir=str(tmp_path),
    )
    daemon._transcriber_ready.set()
    return daemon, dictation_session, feedback, bus


def test_spoken_cancel_aborts_dictation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """type -> speak -> cancel : session cancelled, nothing pasted.

    The dictation.end {reason:"cancel"} event is published by
    DictationSession.cancel() onto the EventBus. EventBus.subscribe() returns
    a queue.Queue of Event objects (no callback API), so the test subscribes
    a consumer queue before driving the utterances and drains it afterwards.
    """
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    with MockWsServer(["hello world"]) as server:
        daemon, dictation_session, feedback, bus = _make_daemon(
            transcripts=[
                _Transcription("dictate"),
                _Transcription("hello world"),
                _Transcription("cancel"),
            ],
            tmp_path=tmp_path,
            ws_url=server.url,
        )
        event_q = bus.subscribe()  # returns a queue.Queue[Event]

        audio = np.zeros(16000, dtype=np.float32)
        daemon._process_utterance(audio)  # "dictate"
        assert dictation_session.active
        daemon._process_utterance(audio)  # "hello world" → streamed
        daemon._process_utterance(audio)  # "cancel" → cancel()
        daemon._dictation_executor.shutdown(wait=True)
        daemon._wav_executor.shutdown(wait=True)

    assert not dictation_session.active
    assert pasted == [], "cancel must not paste"

    # Drain the event queue and assert a dictation.end {reason:"cancel"} fired.
    seen: list[tuple[str, dict]] = []
    while not event_q.empty():
        ev = event_q.get_nowait()
        # Event is a frozen dataclass with .type and .data fields
        # (src/voice_commander/event_bus.py).
        seen.append((ev.type, ev.data))
    assert ("dictation.end", {"reason": "cancel"}) in seen, (
        f"expected dictation.end cancel event; got {seen}"
    )
