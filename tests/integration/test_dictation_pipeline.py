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
) -> tuple[StreamingDaemon, DictationSession, CapturingFeedbackSink, EventBus]:
    """Build a stripped-down daemon for dictation pipeline testing.

    Returns (daemon, dictation_session, feedback, bus).
    """
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
            _Transcription("dictate"),
            _Transcription("hello this is dictated prose"),
            _Transcription("done"),
        ],
        tmp_path=tmp_path,
    )

    audio = np.zeros(16000, dtype=np.float32)

    # 1. "dictate" → __dictation.start → session active
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
            _Transcription("dictate"),
            _Transcription("some words"),
            _Transcription("done"),
        ],
        tmp_path=tmp_path,
    )

    audio = np.zeros(16000, dtype=np.float32)
    daemon._process_utterance(audio)   # "dictate" → starts dictation
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


# ---------------------------------------------------------------------------
# Test 3: hotkey-end with in-flight utterances — the core regression fix
# ---------------------------------------------------------------------------


def test_hotkey_end_with_in_flight_utterance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Hotkey-end after queuing 2 utterances must still capture & submit audio.

    Simulates the race: hotkey fires (request_end) while audio is already in
    the queue but hasn't been processed yet. The pipeline must drain the
    utterances first, THEN call _finalize_pending_dictation_end().
    """
    pasted: list[str] = []

    monkeypatch.setattr(
        "voice_commander.dictation.remote.post_audio",
        lambda wav_bytes, endpoint, **kw: "HOTKEY TEXT",
    )
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[
            _Transcription("hello world"),   # utterance 1 (buffered)
            _Transcription("more content"),  # utterance 2 (buffered)
        ],
        tmp_path=tmp_path,
    )

    audio = np.zeros(16000, dtype=np.float32)
    dictation_session.start()

    # Simulate: request_end() fires from hotkey thread BEFORE pipeline processes
    dictation_session.request_end()

    # Both utterances are processed by the pipeline AFTER the hotkey end request
    daemon._process_utterance(audio)   # "hello world" → buffered
    daemon._process_utterance(audio)   # "more content" → buffered

    # Pipeline drain finalises (simulating what the modified _pipeline_loop does)
    daemon._finalize_pending_dictation_end()

    # Wait for dictation executor to finish
    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    assert pasted == ["HOTKEY TEXT"], (
        f"expected clipboard paste after hotkey-end drain, got: {pasted}"
    )
    assert not dictation_session.active, "session must be inactive after finalize"
    assert not dictation_session.pending_end, "pending_end must be cleared after finalize"


# ---------------------------------------------------------------------------
# Test 4: hotkey-end with empty buffer — must not crash
# ---------------------------------------------------------------------------


def test_hotkey_end_empty_buffer_no_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """request_end() followed by _finalize_pending_dictation_end() with no audio
    must not crash and must not call post_audio."""
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

    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[],
        tmp_path=tmp_path,
    )

    dictation_session.start()
    dictation_session.request_end()
    # No utterances processed — buffer is empty
    daemon._finalize_pending_dictation_end()
    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    assert posted == [], "post_audio must not be called when buffer is empty"
    assert pasted == [], "paste must not be called when buffer is empty"
    assert not dictation_session.active, "session must be inactive after finalize"


# ---------------------------------------------------------------------------
# Test 5: scroll-lock cancel wins the race — pending_end cleared
# ---------------------------------------------------------------------------


def test_hotkey_end_scroll_lock_cancel_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If cancel() is called after request_end() (scroll-lock wins the race),
    pending_end must be False and nothing must be submitted."""
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

    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[],
        tmp_path=tmp_path,
    )

    dictation_session.start()
    dictation_session.request_end()
    assert dictation_session.pending_end  # sanity

    # Scroll-lock fires cancel (races with pipeline drain)
    dictation_session.cancel()

    assert not dictation_session.pending_end, "cancel must clear pending_end"
    assert not dictation_session.active, "cancel must deactivate session"

    # Even if pipeline calls _finalize_pending_dictation_end now, it must be a no-op
    daemon._finalize_pending_dictation_end()
    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    assert posted == [], "post_audio must not be called after cancel wins"
    assert pasted == [], "paste must not be called after cancel wins"


# ---------------------------------------------------------------------------
# Test 6: end-to-end pipeline loop drains before finalise
# ---------------------------------------------------------------------------


def test_pipeline_loop_drains_before_finalize(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Full pipeline-loop regression: start dictation, put utterances in _utt_q,
    call on_dictation_toggle(), wait, assert text was pasted.

    This is the end-to-end regression test for the original race condition.
    """
    import threading

    pasted: list[str] = []

    monkeypatch.setattr(
        "voice_commander.dictation.remote.post_audio",
        lambda wav_bytes, endpoint, **kw: "PIPELINE DRAINED",
    )
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    # Two utterances that will be buffered in dictation mode
    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[
            _Transcription("spoken line one"),
            _Transcription("spoken line two"),
        ],
        tmp_path=tmp_path,
    )

    # Start the pipeline loop thread
    pipeline_thread = threading.Thread(
        target=daemon._pipeline_loop, name="vc-pipeline-test", daemon=True
    )
    pipeline_thread.start()

    audio = np.zeros(16000, dtype=np.float32)

    # Simulate an open voice session (on_dictation_toggle guards on _session_active)
    daemon._session_active = True

    # Enter dictation mode
    dictation_session.start()

    # Queue 2 utterances BEFORE triggering hotkey-end
    daemon._utt_q.put((audio, daemon._audio_gen))
    daemon._utt_q.put((audio, daemon._audio_gen))

    # Hotkey-end: uses the new request_end() path (not take_and_finish directly)
    daemon.on_dictation_toggle()

    # Wait up to 2 s for the paste to happen
    deadline = __import__("time").monotonic() + 2.0
    while __import__("time").monotonic() < deadline and not pasted:
        __import__("time").sleep(0.05)

    # Shutdown pipeline
    daemon._utt_q.put(None)
    pipeline_thread.join(timeout=3.0)
    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    assert pasted == ["PIPELINE DRAINED"], (
        f"hotkey-end should have pasted after pipeline drained; got: {pasted}"
    )
