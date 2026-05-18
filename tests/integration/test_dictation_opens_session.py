"""Integration tests for ADR 0090 — Right Ctrl opens its own voice session.

Mirrors tests/integration/test_dictation_pipeline.py and
tests/integration/test_dictation_cancel.py — same scaffolding
(_StubTranscriber, _Transcription, _make_daemon).

Only the network (post_audio) and OS clipboard (paste_via_clipboard) are
monkeypatched.  Everything else — VerbRouter, DictationSession, StreamingDaemon,
_process_utterance, _finalize_dictation, _finalize_pending_dictation_end,
_dictation_executor — is the real production code.

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

if TYPE_CHECKING:
    from voice_commander.daemon import StreamingDaemon


# ---------------------------------------------------------------------------
# Shared scaffolding (mirrors test_dictation_pipeline.py exactly)
# ---------------------------------------------------------------------------


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
    *,
    recorder: Any = None,
) -> tuple[StreamingDaemon, DictationSession, CapturingFeedbackSink, EventBus]:
    """Build a stripped-down daemon for dictation-opens-session testing.

    recorder: pass a MagicMock to test session open/close; None for pure
    pipeline tests (dictation routing without real audio).
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
        recorder=recorder,
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
# Helper: collect all SSE event types from the bus queue
# ---------------------------------------------------------------------------


def _drain_events(q: Any) -> list[str]:
    events = []
    while not q.empty():
        events.append(q.get_nowait().type)
    return events


# ---------------------------------------------------------------------------
# Test 1: Ctrl-open → "done" → finalize + session closes
# ---------------------------------------------------------------------------


def test_ctrl_open_done_closes_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ctrl-open → speak → 'done' → recorder.close_session called, session_stopped published."""
    posted: list[bytes] = []
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.remote.post_audio",
        lambda wav_bytes, endpoint, **kw: (posted.append(wav_bytes), "DICTATED")[1],
    )
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    recorder = MagicMock()
    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[
            _Transcription("hello world"),
            _Transcription("done"),
        ],
        tmp_path=tmp_path,
        recorder=recorder,
    )
    event_q = bus.subscribe()

    # Simulate Ctrl press: open session, set flag, start dictation
    recorder.open_session.return_value = None
    daemon.on_dictation_toggle()

    assert daemon._session_active is True
    assert daemon._session_opened_by_dictation is True
    assert dictation_session.active is True

    audio = np.zeros(16000, dtype=np.float32)
    daemon._process_utterance(audio)  # "hello world" → buffered
    daemon._process_utterance(audio)  # "done" → end word

    # Wait for executor tasks to complete
    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    # recorder.close_session must have been called (session was Ctrl-opened)
    recorder.close_session.assert_called()
    assert daemon._session_active is False
    assert daemon._session_opened_by_dictation is False

    # session_stopped must appear in SSE events
    events = _drain_events(event_q)
    assert "session_stopped" in events, f"session_stopped not in events: {events}"
    # Dictation result was pasted
    assert pasted == ["DICTATED"]


# ---------------------------------------------------------------------------
# Test 2: Ctrl-open → Ctrl-end with empty buffer → session closes
# ---------------------------------------------------------------------------


def test_ctrl_open_empty_buffer_ctrl_end_closes_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ctrl-open → immediate Ctrl-end (no speech) → recorder.close_session called."""
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.remote.post_audio",
        lambda wav_bytes, endpoint, **kw: "X",
    )
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    recorder = MagicMock()
    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[],
        tmp_path=tmp_path,
        recorder=recorder,
    )
    event_q = bus.subscribe()

    # Ctrl-open
    daemon.on_dictation_toggle()
    assert daemon._session_opened_by_dictation is True

    # Simulate hotkey-end path directly (no utterances spoken)
    daemon._finalize_pending_dictation_end()

    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    # Session must be closed even with an empty buffer
    recorder.close_session.assert_called()
    assert daemon._session_active is False
    assert pasted == [], "nothing should be pasted for an empty-buffer Ctrl-close"

    events = _drain_events(event_q)
    assert "session_stopped" in events, f"session_stopped not in events: {events}"


# ---------------------------------------------------------------------------
# Test 3: Ctrl-open → spoken cancel → abort + session closes
# ---------------------------------------------------------------------------


def test_ctrl_open_spoken_cancel_closes_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ctrl-open → 'cancel' → no paste, no POST, session_stopped published."""
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

    recorder = MagicMock()
    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[
            _Transcription("some content"),
            _Transcription("cancel"),
        ],
        tmp_path=tmp_path,
        recorder=recorder,
    )
    event_q = bus.subscribe()

    daemon.on_dictation_toggle()
    assert daemon._session_opened_by_dictation is True

    audio = np.zeros(16000, dtype=np.float32)
    daemon._process_utterance(audio)  # "some content" → buffered
    daemon._process_utterance(audio)  # "cancel" → spoken cancel path

    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    assert posted == [], "no POST on spoken cancel"
    assert pasted == [], "no paste on spoken cancel"
    assert dictation_session.active is False
    assert daemon._session_active is False

    events = _drain_events(event_q)
    assert "session_stopped" in events, f"session_stopped not in events: {events}"
    cancel_events = [e for e in events if e == "dictation.end"]
    assert cancel_events, "dictation.end must be published on spoken cancel"


# ---------------------------------------------------------------------------
# Test 4: Scroll Lock session + Ctrl dictation + "done" → session STAYS open
# (regression guard — strengthened per REV 9: actually calls on_scroll_lock)
# ---------------------------------------------------------------------------


def test_scroll_lock_session_ctrl_dictation_done_session_stays_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression: Scroll Lock session + Ctrl dictation + 'done' must NOT close session.

    REV 9: Uses on_scroll_lock() to open the session (not direct state injection)
    so the test exercises the real code path.  Asserts _session_active stays True
    and recorder.close_session was NOT called by the dictation-end path.
    """
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.remote.post_audio",
        lambda wav_bytes, endpoint, **kw: "TYPED",
    )
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    recorder = MagicMock()
    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[
            _Transcription("some text"),
            _Transcription("done"),
        ],
        tmp_path=tmp_path,
        recorder=recorder,
    )
    event_q = bus.subscribe()

    # Open via Scroll Lock (NOT Ctrl) — flag must stay False
    daemon.on_scroll_lock()
    assert daemon._session_active is True
    assert daemon._session_opened_by_dictation is False

    # Start dictation as sub-state (Ctrl within active Scroll Lock session)
    dictation_session.start()
    assert dictation_session.active is True

    # Note: reset close_session call count AFTER on_scroll_lock (which calls open_session)
    recorder.close_session.reset_mock()

    audio = np.zeros(16000, dtype=np.float32)
    daemon._process_utterance(audio)  # "some text" → buffered
    daemon._process_utterance(audio)  # "done" → end word

    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    # Session must REMAIN open — it was opened by Scroll Lock, not by Ctrl
    assert daemon._session_active is True, (
        "Regression: session must stay open when Scroll Lock opened it and "
        "dictation ends via end-word"
    )
    assert daemon._session_opened_by_dictation is False
    # recorder.close_session must NOT have been called by the dictation-end path
    recorder.close_session.assert_not_called()

    # session_stopped must NOT appear
    events = _drain_events(event_q)
    assert "session_stopped" not in events, (
        f"Regression: session_stopped must not be published when "
        f"Scroll Lock session is open; events: {events}"
    )


# ---------------------------------------------------------------------------
# Test 5: Close-before-finalize submission order (end-word path)
# ---------------------------------------------------------------------------


def test_close_before_finalize_submission_order_end_word(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """In the end-word path, _end_owned_session_if_needed is submitted to
    _dictation_executor BEFORE _finalize_dictation (ADR 0090 close-first ordering)."""
    monkeypatch.setattr(
        "voice_commander.dictation.remote.post_audio",
        lambda wav_bytes, endpoint, **kw: "TEXT",
    )
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: None,
    )

    recorder = MagicMock()
    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[
            _Transcription("hello"),
            _Transcription("done"),
        ],
        tmp_path=tmp_path,
        recorder=recorder,
    )

    # Replace executor with a mock to capture submission order.
    # Save the bound method BEFORE patching — patching .submit on the executor
    # object makes original_executor.submit recursive, so we keep a stable
    # reference to the original callable.
    submission_order: list[str] = []
    original_executor = daemon._dictation_executor
    original_submit = original_executor.submit  # bound method, stable reference

    def _tracking_submit(fn: Any, *args: Any) -> Any:
        submission_order.append(fn.__name__)
        return original_submit(fn, *args)

    daemon._dictation_executor.submit = _tracking_submit  # type: ignore[method-assign]

    daemon.on_dictation_toggle()  # Ctrl-open: sets flag, starts dictation

    audio = np.zeros(16000, dtype=np.float32)
    daemon._process_utterance(audio)  # "hello" → buffered
    daemon._process_utterance(audio)  # "done" → triggers end-word path

    original_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    # _end_owned_session_if_needed must be first, _finalize_dictation must be second
    assert "_end_owned_session_if_needed" in submission_order, (
        f"_end_owned_session_if_needed was not submitted; order: {submission_order}"
    )
    if "_finalize_dictation" in submission_order:
        close_idx = submission_order.index("_end_owned_session_if_needed")
        finalize_idx = submission_order.index("_finalize_dictation")
        assert close_idx < finalize_idx, (
            f"close-before-finalize violated; submission order: {submission_order}"
        )


# ---------------------------------------------------------------------------
# Test 6: Close-before-finalize submission order (hotkey-end, empty buffer)
# ---------------------------------------------------------------------------


def test_close_submitted_unconditionally_hotkey_end_empty_buffer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_end_owned_session_if_needed must be submitted even when take_and_finish()
    returns None (empty buffer / Ctrl-open + immediate Ctrl-close)."""
    monkeypatch.setattr(
        "voice_commander.dictation.remote.post_audio",
        lambda wav_bytes, endpoint, **kw: "X",
    )
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: None,
    )

    recorder = MagicMock()
    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[],
        tmp_path=tmp_path,
        recorder=recorder,
    )

    # Save the bound method BEFORE patching to avoid self-referential recursion.
    submission_order: list[str] = []
    original_executor = daemon._dictation_executor
    original_submit = original_executor.submit  # bound method, stable reference

    def _tracking_submit(fn: Any, *args: Any) -> Any:
        submission_order.append(fn.__name__)
        return original_submit(fn, *args)

    daemon._dictation_executor.submit = _tracking_submit  # type: ignore[method-assign]

    daemon.on_dictation_toggle()  # Ctrl-open
    # No utterances — trigger hotkey-end directly (empty buffer case)
    daemon._finalize_pending_dictation_end()

    original_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    assert "_end_owned_session_if_needed" in submission_order, (
        f"_end_owned_session_if_needed must be submitted even with empty buffer; "
        f"order: {submission_order}"
    )
    # _finalize_dictation must NOT be submitted when audio is None
    assert "_finalize_dictation" not in submission_order, (
        f"_finalize_dictation must not be submitted for empty buffer; "
        f"order: {submission_order}"
    )
    # But session must still close
    recorder.close_session.assert_called()


# ---------------------------------------------------------------------------
# Test 7: Timeout propagation → DictationRemoteError → dictation.error event
#
# REV 1 fix: the stub raises remote.DictationRemoteError (the type that
# _finalize_dictation actually catches), NOT httpx.TimeoutException.
# The unit test in test_dictation_remote.py verifies that post_audio wraps
# httpx.TimeoutException as DictationRemoteError; this integration test only
# verifies that _finalize_dictation handles DictationRemoteError correctly.
# ---------------------------------------------------------------------------


def test_dictation_remote_error_emits_dictation_error_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A DictationRemoteError from post_audio surfaces as dictation.error + miss chime;
    the executor worker is unblocked after the error.

    REV 1: The stub raises DictationRemoteError directly — the type _finalize_dictation
    actually catches.  Testing that httpx.TimeoutException wraps to DictationRemoteError
    is the job of test_dictation_remote.py::test_timeout_exception_wraps_as_remote_error.
    """
    from voice_commander.dictation.remote import DictationRemoteError

    def _error_post(wav_bytes: bytes, endpoint: str, **kw: Any) -> str:
        raise DictationRemoteError("read timeout after 30 s")

    monkeypatch.setattr("voice_commander.dictation.remote.post_audio", _error_post)
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
        recorder=recorder,
    )
    event_q = bus.subscribe()

    daemon.on_dictation_toggle()
    audio = np.zeros(16000, dtype=np.float32)
    daemon._process_utterance(audio)   # "hello world" → buffered
    daemon._process_utterance(audio)   # "done" → end word

    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    # dictation.error event must be published
    events = _drain_events(event_q)
    assert "dictation.error" in events, (
        f"dictation.error must be published on DictationRemoteError; events: {events}"
    )
    # Miss chime must be played
    assert any(c[0] == "on_miss" for c in feedback.calls), (
        "on_miss must be called after DictationRemoteError"
    )
    # Executor must be free — verified implicitly by shutdown(wait=True) returning above


# ---------------------------------------------------------------------------
# Test 8: shutdown() with active dictation cancels it directly
# (REV 2: this test is here, not in Task 2, because shutdown() is modified in Task 5)
# ---------------------------------------------------------------------------


def test_shutdown_with_active_dictation_cancels_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """shutdown() must cancel DictationSession directly BEFORE executor.shutdown(wait=False).

    REV 2: Moved here (Task 4 red phase) because the production code change is in Task 5.
    REV 3: The cancel call goes immediately after the session-close block and BEFORE
    _dictation_executor.shutdown(wait=False) — so any queued tasks are not needed.
    """
    monkeypatch.setattr(
        "voice_commander.dictation.remote.post_audio",
        lambda *a, **kw: "X",
    )
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: None,
    )

    recorder = MagicMock()
    daemon, dictation_session, feedback, bus = _make_daemon(
        transcripts=[],
        tmp_path=tmp_path,
        recorder=recorder,
    )

    # Start pipeline so shutdown can join it
    daemon._pipeline_thread = threading.Thread(
        target=daemon._pipeline_loop, daemon=True
    )
    daemon._pipeline_thread.start()

    dictation_session.start()
    assert dictation_session.active is True

    daemon.shutdown()

    assert dictation_session.active is False, (
        "shutdown() must call dictation_session.cancel() directly — "
        "before executor.shutdown(wait=False) so it is not abandoned"
    )
