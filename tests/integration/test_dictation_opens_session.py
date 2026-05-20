"""Integration tests for ADR 0090 -- Right Ctrl opens its own voice session.

Mirrors tests/integration/test_dictation_pipeline.py and
tests/integration/test_dictation_cancel.py -- same scaffolding
(_StubTranscriber, _Transcription, _make_daemon).

Only the OS clipboard (paste_via_clipboard) is monkeypatched.
Everything else -- VerbRouter, DictationSession, StreamingDaemon,
_process_utterance, _finalize_dictation, _finalize_pending_dictation_end,
_dictation_executor -- is the real production code.

Each test explicitly drives _process_utterance() rather than the full pipeline
loop for determinism. Tests that verify executor submission order use a mock
executor and inspect call order directly.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

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
    *,
    recorder: Any = None,
) -> "tuple[StreamingDaemon, DictationSession, CapturingFeedbackSink, EventBus]":
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
        recorder=recorder,
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


def _drain_events(q: Any) -> list[str]:
    events = []
    while not q.empty():
        events.append(q.get_nowait().type)
    return events


def test_ctrl_open_done_closes_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ctrl-open -> speak -> done -> recorder.close_session called, session_stopped published."""
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    with MockWsServer(done_text="hello world") as server:
        recorder = MagicMock()
        daemon, dictation_session, feedback, bus = _make_daemon(
            transcripts=[
                _Transcription("hello world"),
                _Transcription("done"),
            ],
            tmp_path=tmp_path,
            ws_url=server.url,
            recorder=recorder,
        )
        event_q = bus.subscribe()

        recorder.open_session.return_value = None
        daemon.on_dictation_toggle()

        assert daemon._session_active is True
        assert daemon._session_opened_by_dictation is True
        assert dictation_session.active is True

        audio = np.zeros(16000, dtype=np.float32)
        daemon._process_utterance(audio)  # "hello world" -> streamed
        daemon._process_utterance(audio)  # "done" -> end word

        daemon._dictation_executor.shutdown(wait=True)
        daemon._wav_executor.shutdown(wait=True)

    recorder.close_session.assert_called()
    assert daemon._session_active is False
    assert daemon._session_opened_by_dictation is False

    events = _drain_events(event_q)
    assert "session_stopped" in events, f"session_stopped not in events: {events}"
    assert pasted, f"expected a paste; got {pasted}"


def test_ctrl_open_empty_buffer_ctrl_end_closes_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ctrl-open -> immediate Ctrl-end (no speech) -> recorder.close_session called."""
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    with MockWsServer() as server:
        recorder = MagicMock()
        daemon, dictation_session, feedback, bus = _make_daemon(
            transcripts=[],
            tmp_path=tmp_path,
            ws_url=server.url,
            recorder=recorder,
        )
        event_q = bus.subscribe()

        daemon.on_dictation_toggle()
        assert daemon._session_opened_by_dictation is True

        daemon._finalize_pending_dictation_end()

        daemon._dictation_executor.shutdown(wait=True)
        daemon._wav_executor.shutdown(wait=True)

    recorder.close_session.assert_called()
    assert daemon._session_active is False
    assert pasted == [], "nothing should be pasted for an empty-buffer Ctrl-close"

    events = _drain_events(event_q)
    assert "session_stopped" in events, f"session_stopped not in events: {events}"


def test_ctrl_open_spoken_cancel_closes_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ctrl-open -> cancel -> no paste, session_stopped published."""
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    with MockWsServer(done_text="some content") as server:
        recorder = MagicMock()
        daemon, dictation_session, feedback, bus = _make_daemon(
            transcripts=[
                _Transcription("some content"),
                _Transcription("cancel"),
            ],
            tmp_path=tmp_path,
            ws_url=server.url,
            recorder=recorder,
        )
        event_q = bus.subscribe()

        daemon.on_dictation_toggle()
        assert daemon._session_opened_by_dictation is True

        audio = np.zeros(16000, dtype=np.float32)
        daemon._process_utterance(audio)  # "some content" -> streamed
        daemon._process_utterance(audio)  # "cancel" -> spoken cancel path

        daemon._dictation_executor.shutdown(wait=True)
        daemon._wav_executor.shutdown(wait=True)

    assert pasted == [], "no paste on spoken cancel"
    assert dictation_session.active is False
    assert daemon._session_active is False

    events = _drain_events(event_q)
    assert "session_stopped" in events, f"session_stopped not in events: {events}"
    cancel_events = [e for e in events if e == "dictation.end"]
    assert cancel_events, "dictation.end must be published on spoken cancel"


def test_scroll_lock_session_ctrl_dictation_done_session_stays_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression: Scroll Lock session + Ctrl dictation + done must NOT close session."""
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    with MockWsServer(done_text="some text") as server:
        recorder = MagicMock()
        daemon, dictation_session, feedback, bus = _make_daemon(
            transcripts=[
                _Transcription("some text"),
                _Transcription("done"),
            ],
            tmp_path=tmp_path,
            ws_url=server.url,
            recorder=recorder,
        )
        event_q = bus.subscribe()

        daemon.on_scroll_lock()
        assert daemon._session_active is True
        assert daemon._session_opened_by_dictation is False

        dictation_session.start(daemon._load_vocab())
        assert dictation_session.active is True

        recorder.close_session.reset_mock()

        audio = np.zeros(16000, dtype=np.float32)
        daemon._process_utterance(audio)  # "some text" -> streamed
        daemon._process_utterance(audio)  # "done" -> end word

        daemon._dictation_executor.shutdown(wait=True)
        daemon._wav_executor.shutdown(wait=True)

    assert daemon._session_active is True, (
        "Regression: session must stay open when Scroll Lock opened it and "
        "dictation ends via end-word"
    )
    assert daemon._session_opened_by_dictation is False
    recorder.close_session.assert_not_called()

    events = _drain_events(event_q)
    assert "session_stopped" not in events, (
        f"Regression: session_stopped must not be published when "
        f"Scroll Lock session is open; events: {events}"
    )


def test_close_before_finalize_submission_order_end_word(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_end_owned_session_if_needed submitted before _finalize_dictation (ADR 0090)."""
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: None,
    )

    with MockWsServer(done_text="hello") as server:
        recorder = MagicMock()
        daemon, dictation_session, feedback, bus = _make_daemon(
            transcripts=[
                _Transcription("hello"),
                _Transcription("done"),
            ],
            tmp_path=tmp_path,
            ws_url=server.url,
            recorder=recorder,
        )

        submission_order: list[str] = []
        original_executor = daemon._dictation_executor
        original_submit = original_executor.submit

        def _tracking_submit(fn: Any, *args: Any) -> Any:
            submission_order.append(fn.__name__)
            return original_submit(fn, *args)

        daemon._dictation_executor.submit = _tracking_submit  # type: ignore[method-assign]

        daemon.on_dictation_toggle()

        audio = np.zeros(16000, dtype=np.float32)
        daemon._process_utterance(audio)  # "hello" -> streamed
        daemon._process_utterance(audio)  # "done" -> triggers end-word path

        original_executor.shutdown(wait=True)
        daemon._wav_executor.shutdown(wait=True)

    assert "_end_owned_session_if_needed" in submission_order, (
        f"_end_owned_session_if_needed was not submitted; order: {submission_order}"
    )
    if "_finalize_dictation" in submission_order:
        close_idx = submission_order.index("_end_owned_session_if_needed")
        finalize_idx = submission_order.index("_finalize_dictation")
        assert close_idx < finalize_idx, (
            f"close-before-finalize violated; submission order: {submission_order}"
        )


def test_close_submitted_unconditionally_hotkey_end_empty_buffer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_end_owned_session_if_needed submitted even with empty buffer."""
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: None,
    )

    with MockWsServer() as server:
        recorder = MagicMock()
        daemon, dictation_session, feedback, bus = _make_daemon(
            transcripts=[],
            tmp_path=tmp_path,
            ws_url=server.url,
            recorder=recorder,
        )

        submission_order: list[str] = []
        original_executor = daemon._dictation_executor
        original_submit = original_executor.submit

        def _tracking_submit(fn: Any, *args: Any) -> Any:
            submission_order.append(fn.__name__)
            return original_submit(fn, *args)

        daemon._dictation_executor.submit = _tracking_submit  # type: ignore[method-assign]

        daemon.on_dictation_toggle()
        daemon._finalize_pending_dictation_end()

        original_executor.shutdown(wait=True)
        daemon._wav_executor.shutdown(wait=True)

    assert "_end_owned_session_if_needed" in submission_order, (
        f"_end_owned_session_if_needed must be submitted even with empty buffer; "
        f"order: {submission_order}"
    )
    assert "_finalize_dictation" in submission_order, (
        f"_finalize_dictation must be submitted in the streaming path; "
        f"order: {submission_order}"
    )
    recorder.close_session.assert_called()


def test_dictation_endpoint_failure_emits_dictation_error_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A WS connect failure surfaces as dictation.error + miss chime."""
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: None,
    )

    recorder = MagicMock()
    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[
            _Transcription("hello world"),
            _Transcription("done"),
        ],
        tmp_path=tmp_path,
        ws_url="ws://localhost:1",
        recorder=recorder,
    )
    event_q = bus.subscribe()

    daemon.on_dictation_toggle()
    audio = np.zeros(16000, dtype=np.float32)
    daemon._process_utterance(audio)
    daemon._process_utterance(audio)

    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    events = _drain_events(event_q)
    assert "dictation.error" in events, (
        f"dictation.error must be published on endpoint failure; events: {events}"
    )
    assert any(c[0] == "on_miss" for c in feedback.calls), (
        "on_miss must be called after endpoint failure"
    )


def test_shutdown_with_active_dictation_cancels_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """shutdown() must cancel DictationSession before executor.shutdown(wait=False)."""
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: None,
    )

    with MockWsServer() as server:
        recorder = MagicMock()
        daemon, dictation_session, feedback, bus = _make_daemon(
            transcripts=[],
            tmp_path=tmp_path,
            ws_url=server.url,
            recorder=recorder,
        )

        daemon._pipeline_thread = threading.Thread(
            target=daemon._pipeline_loop, daemon=True
        )
        daemon._pipeline_thread.start()

        dictation_session.start(daemon._load_vocab())
        assert dictation_session.active is True

        daemon.shutdown()

    assert dictation_session.active is False, (
        "shutdown() must call dictation_session.cancel() directly"
    )
