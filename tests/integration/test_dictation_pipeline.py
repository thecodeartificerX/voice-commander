"""Daemon pipeline integration tests for the dictation mode.

Mirrors tests/integration/test_picker_pipeline.py — same scaffolding
(_StubTranscriber, _Transcription, _make_daemon), adapted for the dictation
sub-state instead of the picker sub-state.

Only the network (post_audio) and OS clipboard (paste_via_clipboard) are
monkeypatched.  Everything else — VerbRouter, DictationSession,
_process_utterance, _finalize_dictation, executor, DictationStore — is the
real production code.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from voice_commander.event_bus import EventBus
from voice_commander.feedback import CapturingFeedbackSink
from voice_commander.verb_router import VerbRouter, build_default_rules
from voice_commander.dictation.session import DictationSession


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
) -> tuple["StreamingDaemon", DictationSession, CapturingFeedbackSink, EventBus]:
    """Build a stripped-down daemon for dictation pipeline testing.

    Returns (daemon, dictation_session, feedback, bus).
    """
    from voice_commander.daemon import StreamingDaemon
    from voice_commander.dispatcher import Dispatcher
    from voice_commander.registry import get_global_registry, reset_global_registry
    from voice_commander.picker.registry import reset_global_picker_registry

    reset_global_registry()
    reset_global_picker_registry()

    registry = get_global_registry()
    bus = EventBus()
    feedback = CapturingFeedbackSink()
    dispatcher = Dispatcher(feedback=feedback, event_bus=bus)
    verb_router = VerbRouter(build_default_rules(), registry=registry, picker_registry=None)
    dictation_session = DictationSession(bus=bus, end_word="done")

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
    # _dictation_session is wired by the StreamingDaemon constructor kwarg above.
    return daemon, dictation_session, feedback, bus


# ---------------------------------------------------------------------------
# Test 1: happy path — type → content utterance → done
# ---------------------------------------------------------------------------


def test_dictation_enter_buffer_finalize(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """type -> speak -> done : audio buffered, endpoint POSTed, clipboard pasted."""
    posted: dict[str, Any] = {}

    def _fake_post(wav_bytes: bytes, endpoint: str, **kw: Any) -> str:
        posted["wav_len"] = len(wav_bytes)
        return "DICTATED TEXT"

    monkeypatch.setattr("voice_commander.dictation.remote.post_audio", _fake_post)

    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[
            _Transcription("type"),
            _Transcription("hello this is dictated prose"),
            _Transcription("done"),
        ],
        tmp_path=tmp_path,
    )

    audio = np.zeros(16000, dtype=np.float32)

    # 1. "type" → __dictation.start → session active
    daemon._process_utterance(audio)
    assert dictation_session.active is True

    # 2. dictation content → buffered; session stays active
    daemon._process_utterance(audio)
    assert dictation_session.active is True

    # 3. "done" → finalize; session is now inactive
    daemon._process_utterance(audio)
    assert dictation_session.active is False

    # 4. finalize runs on the executor — wait for it to complete; also drain
    #    the async WAV-writer so no background write races tmp_path teardown.
    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    assert posted.get("wav_len", 0) > 0, "post_audio was not called or wav was empty"
    assert pasted == ["DICTATED TEXT"], f"paste_via_clipboard was not called correctly: {pasted}"
    assert daemon._dictation_store.read_text() == "DICTATED TEXT"


# ---------------------------------------------------------------------------
# Test 2: endpoint failure → miss chime + audio stays on disk
# ---------------------------------------------------------------------------


def test_dictation_endpoint_failure_chimes_and_keeps_audio(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When the remote endpoint raises, a miss chime fires and audio is on disk."""
    from voice_commander.dictation.remote import DictationRemoteError

    def _boom(wav_bytes: bytes, endpoint: str, **kw: Any) -> str:
        raise DictationRemoteError("unreachable")

    monkeypatch.setattr("voice_commander.dictation.remote.post_audio", _boom)

    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[
            _Transcription("type"),
            _Transcription("some words"),
            _Transcription("done"),
        ],
        tmp_path=tmp_path,
    )

    audio = np.zeros(16000, dtype=np.float32)
    daemon._process_utterance(audio)   # "type" → starts dictation
    daemon._process_utterance(audio)   # "some words" → buffered
    daemon._process_utterance(audio)   # "done" → finalize submitted

    # Wait for the finalize worker to finish; drain the WAV-writer too.
    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    # Endpoint failed → on_miss was called
    assert any(c[0] == "on_miss" for c in feedback.calls), (
        f"Expected an on_miss call after endpoint failure; got {feedback.calls}"
    )

    # Audio WAV was saved to disk before the POST was attempted (so it's
    # available for re-transcribe via the web UI even after a network failure).
    assert daemon._dictation_store.read_audio() is not None, (
        "Audio WAV should be persisted on disk even when endpoint POST fails"
    )
