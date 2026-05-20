"""Daemon pipeline integration tests for streaming dictation mode (ADR 0092).

Builds a real daemon; the DictationSession streams to an in-process mock
WebSocket server. Only the OS clipboard (paste_via_clipboard) is monkeypatched.
Everything else — VerbRouter, DictationSession, _process_utterance,
_finalize_dictation, executor, DictationStore — is real production code.
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
    """Build a stripped-down daemon whose DictationSession streams to *ws_url*."""
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
        bus=bus, ws_url=ws_url, end_word="done", idle_timeout_s=3.0
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


def test_dictation_enter_stream_finalize(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """type -> speak -> done : utterances streamed, transcript pasted."""
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    with MockWsServer(["hello world", "world done"], done_text="hello world") as server:
        daemon, dictation_session, feedback, bus = _make_daemon(
            transcripts=[
                _Transcription("dictate"),
                _Transcription("hello world"),
                _Transcription("done"),
            ],
            tmp_path=tmp_path,
            ws_url=server.url,
        )
        audio = np.zeros(16000, dtype=np.float32)

        daemon._process_utterance(audio)  # "dictate" → session active
        assert dictation_session.active is True

        daemon._process_utterance(audio)  # content → streamed
        assert dictation_session.active is True

        daemon._process_utterance(audio)  # "done" → finalize submitted
        daemon._dictation_executor.shutdown(wait=True)
        daemon._wav_executor.shutdown(wait=True)

    assert dictation_session.active is False
    # One content utterance "hello world" → one WS chunk → partial "hello world".
    # commit("hello world") → hypothesis=["hello","world"], confirmed=[].
    # finalize() → ["hello","world"]. Final transcript: "hello world".
    assert pasted == ["hello world"], f"unexpected paste: {pasted}"
    assert daemon._dictation_store.read_text() == "hello world"


def test_dictation_endpoint_failure_chimes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A bad ws_url → finish() returns '' with error → miss chime fires."""
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    # No server — point at a closed port to force a connect failure.
    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[
            _Transcription("dictate"),
            _Transcription("some words"),
            _Transcription("done"),
        ],
        tmp_path=tmp_path,
        ws_url="ws://localhost:1",
    )
    audio = np.zeros(16000, dtype=np.float32)
    daemon._process_utterance(audio)  # "dictate"
    daemon._process_utterance(audio)  # "some words"
    daemon._process_utterance(audio)  # "done" → finalize

    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    assert any(c[0] == "on_miss" for c in feedback.calls), (
        f"expected an on_miss call after endpoint failure; got {feedback.calls}"
    )
    assert pasted == []


def test_hotkey_end_with_in_flight_utterance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Hotkey-end after queuing 2 utterances streams them, then finalizes."""
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    with MockWsServer(["hello world", "world more", "more text"], done_text="hello world more text") as server:
        daemon, dictation_session, feedback, bus = _make_daemon(
            transcripts=[
                _Transcription("hello world"),
                _Transcription("more text"),
            ],
            tmp_path=tmp_path,
            ws_url=server.url,
        )
        audio = np.zeros(16000, dtype=np.float32)
        dictation_session.start(daemon._load_vocab())
        dictation_session.request_end()
        daemon._process_utterance(audio)  # streamed
        daemon._process_utterance(audio)  # streamed
        daemon._finalize_pending_dictation_end()
        daemon._dictation_executor.shutdown(wait=True)
        daemon._wav_executor.shutdown(wait=True)

    assert pasted, f"expected a paste after hotkey-end drain; got {pasted}"
    assert not dictation_session.active
    assert not dictation_session.pending_end


def test_hotkey_end_empty_session_no_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """request_end() then _finalize_pending_dictation_end() with no speech
    must not crash and must not paste."""
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    with MockWsServer([]) as server:
        daemon, dictation_session, feedback, bus = _make_daemon(
            transcripts=[], tmp_path=tmp_path, ws_url=server.url
        )
        dictation_session.start(daemon._load_vocab())
        dictation_session.request_end()
        daemon._finalize_pending_dictation_end()
        daemon._dictation_executor.shutdown(wait=True)
        daemon._wav_executor.shutdown(wait=True)

    assert pasted == [], "paste must not be called when nothing was streamed"
    assert not dictation_session.active


def test_scroll_lock_cancel_wins_the_race(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """cancel() after request_end() clears pending_end; finalize is a no-op."""
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    with MockWsServer([]) as server:
        daemon, dictation_session, feedback, bus = _make_daemon(
            transcripts=[], tmp_path=tmp_path, ws_url=server.url
        )
        dictation_session.start(daemon._load_vocab())
        dictation_session.request_end()
        assert dictation_session.pending_end
        dictation_session.cancel()
        assert not dictation_session.pending_end
        assert not dictation_session.active

        daemon._finalize_pending_dictation_end()
        daemon._dictation_executor.shutdown(wait=True)
        daemon._wav_executor.shutdown(wait=True)

    assert pasted == []


def test_pipeline_loop_drains_before_finalize(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Full pipeline-loop regression: queue utterances, on_dictation_toggle(),
    assert the streamed transcript is pasted after the drain."""
    import threading
    import time

    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    with MockWsServer(["line one", "one line two", "line two end"], done_text="line one line two end") as server:
        daemon, dictation_session, feedback, bus = _make_daemon(
            transcripts=[
                _Transcription("spoken line one"),
                _Transcription("spoken line two"),
            ],
            tmp_path=tmp_path,
            ws_url=server.url,
        )
        pipeline_thread = threading.Thread(
            target=daemon._pipeline_loop, name="vc-pipeline-test", daemon=True
        )
        pipeline_thread.start()

        audio = np.zeros(16000, dtype=np.float32)
        daemon._session_active = True
        dictation_session.start(daemon._load_vocab())
        daemon._utt_q.put((audio, daemon._audio_gen))
        daemon._utt_q.put((audio, daemon._audio_gen))
        daemon.on_dictation_toggle()

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not pasted:
            time.sleep(0.05)

        daemon._utt_q.put(None)
        pipeline_thread.join(timeout=3.0)
        daemon._dictation_executor.shutdown(wait=True)
        daemon._wav_executor.shutdown(wait=True)

    assert pasted, f"hotkey-end should have pasted after pipeline drained; got {pasted}"


def test_hotkey_end_finalizes_without_trailing_utterance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ADR 0089 regression: hotkey-end with NO trailing utterance still
    finalizes (the _DICTATION_WAKE sentinel wakes the blocked pipeline)."""
    import threading
    import time

    finalized = threading.Event()
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda *a, **kw: finalized.set(),
    )

    @dataclass
    class _SentinelTranscription:
        text: str
        confidence: float = 0.95
        no_speech_prob: float = 0.05

    processed = threading.Event()

    class _SentinelStub:
        def __init__(self, result: _SentinelTranscription) -> None:
            self._result = result

        def load(self) -> None: ...
        def unload(self) -> None: ...

        def transcribe(self, _audio: np.ndarray) -> _SentinelTranscription:
            result = self._result
            processed.set()
            return result

    with MockWsServer(["some dictated content"], done_text="some dictated content") as server:
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
        verb_router = VerbRouter(
            build_default_rules(), registry=registry, picker_registry=None
        )
        dictation_session = DictationSession(
            bus=bus, ws_url=server.url, end_word="done", idle_timeout_s=3.0
        )
        daemon = StreamingDaemon(
            feedback=feedback,
            recorder=None,
            transcriber=_SentinelStub(_SentinelTranscription("some dictated content")),
            dispatcher=dispatcher,
            verb_router=verb_router,
            registry=registry,
            event_bus=bus,
            dictation_session=dictation_session,
            dictation_ws_url=server.url,
            output_dir=str(tmp_path),
        )
        daemon._transcriber_ready.set()

        pipeline_thread = threading.Thread(
            target=daemon._pipeline_loop, name="vc-pipeline-sentinel-test", daemon=True
        )
        daemon._session_active = True
        pipeline_thread.start()

        dictation_session.start(daemon._load_vocab())
        audio = np.zeros(16000, dtype=np.float32)
        daemon._utt_q.put((audio, daemon._audio_gen))

        assert processed.wait(timeout=5.0), "stub transcriber never ran"
        time.sleep(0.020)
        daemon.on_dictation_toggle()

        assert finalized.wait(timeout=3.0), (
            "hotkey-end must finalize within 3 s with no trailing utterance "
            "(ADR 0089 sentinel regression gate)"
        )
        assert not dictation_session.active

        daemon._utt_q.put(None)
        pipeline_thread.join(timeout=3.0)
        daemon._dictation_executor.shutdown(wait=True)
        daemon._wav_executor.shutdown(wait=True)
