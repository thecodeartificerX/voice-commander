"""Unit tests for voice_commander.daemon.StreamingDaemon.

All hardware subsystems (recorder, transcriber, matcher, dispatcher) are
replaced with MagicMocks.  Utterance ndarrays are injected directly into the
pipeline queue so no real audio or GPU work occurs.

Hardware tests (requiring a live audio + GPU stack) are marked with
@pytest.mark.hardware.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import numpy as np

from voice_commander.daemon import StreamingDaemon
from voice_commander.feedback import CapturingFeedbackSink
from voice_commander.transcriber import TranscriptionResult

# ---------------------------------------------------------------------------
# Helper: build a fully-mocked daemon
# ---------------------------------------------------------------------------


def _make_daemon(
    *, output_dir: str = "outputs"
) -> tuple[
    StreamingDaemon,
    CapturingFeedbackSink,
    MagicMock,  # recorder
    MagicMock,  # transcriber
    MagicMock,  # matcher
    MagicMock,  # dispatcher
]:
    feedback = CapturingFeedbackSink()

    recorder = MagicMock()
    recorder.is_open = False

    transcriber = MagicMock()
    matcher = MagicMock()
    dispatcher = MagicMock()

    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=recorder,
        transcriber=transcriber,
        matcher=matcher,
        dispatcher=dispatcher,
        output_dir=output_dir,
    )
    return daemon, feedback, recorder, transcriber, matcher, dispatcher


def _fake_utterance(n: int = 1600) -> np.ndarray:
    return np.zeros(n, dtype=np.float32)


def _fake_transcription_result(
    text: str = "copy",
    confidence: float = 0.9,
    no_speech_prob: float = 0.05,
) -> TranscriptionResult:
    return TranscriptionResult(
        text=text,
        confidence=confidence,
        language="en",
        duration_ms=500,
        no_speech_prob=no_speech_prob,
    )


# ---------------------------------------------------------------------------
# on_scroll_lock tests
# ---------------------------------------------------------------------------


def test_on_scroll_lock_opens_session_when_idle(tmp_path):
    daemon, feedback, recorder, *_ = _make_daemon(output_dir=str(tmp_path))
    recorder.is_open = False

    daemon.on_scroll_lock()

    recorder.open_session.assert_called_once()
    assert any(c[0] == "on_recording_start" for c in feedback.calls)


def test_on_scroll_lock_closes_session_when_open(tmp_path):
    daemon, feedback, recorder, *_ = _make_daemon(output_dir=str(tmp_path))
    recorder.is_open = True
    daemon._session_active = True

    daemon.on_scroll_lock()

    recorder.close_session.assert_called_once()
    assert any(c[0] == "on_recording_stop" for c in feedback.calls)


def test_on_scroll_lock_reports_error_when_open_session_raises(tmp_path):
    daemon, feedback, recorder, *_ = _make_daemon(output_dir=str(tmp_path))
    recorder.is_open = False
    recorder.open_session.side_effect = RuntimeError("device unavailable")

    daemon.on_scroll_lock()  # must not propagate

    assert any(c[0] == "on_error" for c in feedback.calls)


# ---------------------------------------------------------------------------
# Pipeline processing tests
# ---------------------------------------------------------------------------


def test_pipeline_processes_utterance(tmp_path):
    """Utterance placed in _utt_q flows through transcribe → match → dispatch."""
    daemon, feedback, recorder, transcriber, matcher, dispatcher = _make_daemon(
        output_dir=str(tmp_path)
    )

    result = _fake_transcription_result("copy", confidence=0.95)
    transcriber.transcribe.return_value = result

    match_result = MagicMock()
    matcher.match.return_value = match_result

    # Start pipeline thread.
    pipeline_done = threading.Event()
    original_process = daemon._process_utterance

    def _patched_process(utt):
        original_process(utt)
        pipeline_done.set()

    daemon._process_utterance = _patched_process

    thread = threading.Thread(target=daemon._pipeline_loop, daemon=True)
    thread.start()

    daemon._utt_q.put(_fake_utterance())
    triggered = pipeline_done.wait(timeout=5.0)

    # Poison pill to stop the thread.
    daemon._utt_q.put(None)
    thread.join(timeout=3.0)

    assert triggered, "pipeline did not process utterance within 5 s"
    transcriber.transcribe.assert_called_once()
    dispatcher.dispatch.assert_called_once()


def test_pipeline_handles_transcribe_error(tmp_path):
    """RuntimeError from transcriber.transcribe is caught; on_error is called;
    the pipeline thread exits cleanly after receiving the poison pill."""
    daemon, feedback, recorder, transcriber, matcher, dispatcher = _make_daemon(
        output_dir=str(tmp_path)
    )

    transcriber.transcribe.side_effect = RuntimeError("CUDA OOM")

    error_seen = threading.Event()
    original_on_error = feedback.on_error

    def _patched_on_error(subsystem, err):
        original_on_error(subsystem, err)
        error_seen.set()

    feedback.on_error = _patched_on_error

    thread = threading.Thread(target=daemon._pipeline_loop, daemon=True)
    thread.start()

    daemon._utt_q.put(_fake_utterance())

    triggered = error_seen.wait(timeout=5.0)
    assert triggered, "on_error was never called after transcribe exception"

    # Send poison pill and confirm thread exits gracefully.
    daemon._utt_q.put(None)
    thread.join(timeout=3.0)
    assert not thread.is_alive(), "pipeline thread did not exit after poison pill"

    assert any(c[0] == "on_error" for c in feedback.calls)
    dispatcher.dispatch.assert_not_called()


# ---------------------------------------------------------------------------
# Shutdown tests
# ---------------------------------------------------------------------------


def test_shutdown_is_idempotent(tmp_path):
    """Calling shutdown() twice must not raise."""
    daemon, *_ = _make_daemon(output_dir=str(tmp_path))

    # Start the pipeline thread so shutdown can join it properly.
    daemon._pipeline_thread = threading.Thread(target=daemon._pipeline_loop, daemon=True)
    daemon._pipeline_thread.start()

    daemon.shutdown()
    daemon.shutdown()  # second call must be a no-op


def test_shutdown_closes_open_session(tmp_path):
    """If a session is open when shutdown() is called, close_session() is invoked."""
    daemon, _, recorder, *_ = _make_daemon(output_dir=str(tmp_path))
    recorder.is_open = True
    daemon._session_active = True

    # Start the pipeline thread so shutdown can join it.
    daemon._pipeline_thread = threading.Thread(target=daemon._pipeline_loop, daemon=True)
    daemon._pipeline_thread.start()

    daemon.shutdown()

    recorder.close_session.assert_called_once()


# ---------------------------------------------------------------------------
# utt_q overflow test
# ---------------------------------------------------------------------------


def test_utt_q_overflow_calls_on_miss(tmp_path):
    """When _utt_q is full, _on_utterance drops the frame and calls on_miss."""
    daemon, feedback, *_ = _make_daemon(output_dir=str(tmp_path))

    # Fill the queue to its maxsize of 8.
    for _ in range(8):
        daemon._utt_q.put(_fake_utterance())

    assert daemon._utt_q.full()

    # _on_utterance with a full queue must not raise, and must call on_miss.
    daemon._on_utterance(_fake_utterance())

    assert any(c[0] == "on_miss" for c in feedback.calls), (
        "on_miss was not called when utt_q overflowed"
    )
    # The queue size must remain at maxsize — no extra item was added.
    assert daemon._utt_q.qsize() == 8


# ---------------------------------------------------------------------------
# Mute state-transition tests
# ---------------------------------------------------------------------------


def test_scroll_lock_opens_from_inactive(tmp_path):
    """State transition: inactive + Scroll Lock → active, unmuted."""
    daemon, feedback, recorder, *_ = _make_daemon(output_dir=str(tmp_path))
    recorder.is_open = False
    daemon.on_scroll_lock()
    recorder.open_session.assert_called_once()
    assert daemon._session_active is True
    assert daemon._muted is False
    assert any(c[0] == "on_recording_start" for c in feedback.calls)


def test_scroll_lock_closes_from_active_unmuted(tmp_path):
    """State transition: active+unmuted + Scroll Lock → inactive."""
    daemon, feedback, recorder, *_ = _make_daemon(output_dir=str(tmp_path))
    daemon._session_active = True
    daemon._muted = False
    daemon.on_scroll_lock()
    recorder.close_session.assert_called_once()
    assert daemon._session_active is False
    assert daemon._muted is False
    assert any(c[0] == "on_recording_stop" for c in feedback.calls)


def test_scroll_lock_closes_from_active_muted(tmp_path):
    """State transition: active+muted + Scroll Lock → inactive."""
    daemon, feedback, recorder, *_ = _make_daemon(output_dir=str(tmp_path))
    daemon._session_active = True
    daemon._muted = True
    daemon.on_scroll_lock()
    recorder.close_session.assert_not_called()  # stream already closed by mute
    assert daemon._session_active is False
    assert daemon._muted is False
    assert any(c[0] == "on_recording_stop" for c in feedback.calls)


def test_mute_noop_when_inactive(tmp_path):
    """State transition: inactive + Mute key → silent no-op."""
    daemon, feedback, recorder, *_ = _make_daemon(output_dir=str(tmp_path))
    daemon._session_active = False
    daemon.on_mute_toggle()
    recorder.open_session.assert_not_called()
    recorder.close_session.assert_not_called()
    assert feedback.calls == []  # truly silent


def test_mute_from_active_unmuted(tmp_path):
    """State transition: active+unmuted + Mute key → active+muted."""
    daemon, feedback, recorder, *_ = _make_daemon(output_dir=str(tmp_path))
    daemon._session_active = True
    daemon._muted = False
    daemon.on_mute_toggle()
    recorder.close_session.assert_called_once()
    assert daemon._session_active is True
    assert daemon._muted is True


def test_unmute_from_active_muted(tmp_path):
    """State transition: active+muted + Mute key → active+unmuted."""
    daemon, feedback, recorder, *_ = _make_daemon(output_dir=str(tmp_path))
    daemon._session_active = True
    daemon._muted = True
    daemon.on_mute_toggle()
    recorder.open_session.assert_called_once()
    assert daemon._session_active is True
    assert daemon._muted is False


def test_mute_drains_utt_q(tmp_path):
    """Muting drains all pending utterances from the queue."""
    daemon, *_ = _make_daemon(output_dir=str(tmp_path))
    daemon._session_active = True
    daemon._muted = False
    # Pre-fill queue
    daemon._utt_q.put(_fake_utterance())
    daemon._utt_q.put(_fake_utterance())
    assert daemon._utt_q.qsize() == 2
    daemon.on_mute_toggle()
    assert daemon._utt_q.qsize() == 0  # drained


def test_pipeline_mute_guard_drops_utterance(tmp_path):
    """Utterance mid-transcription when mute fires must NOT dispatch."""
    daemon, feedback, recorder, transcriber, matcher, dispatcher = _make_daemon(
        output_dir=str(tmp_path)
    )

    result = _fake_transcription_result("copy", confidence=0.95)
    transcriber.transcribe.return_value = result

    match_result = MagicMock()
    matcher.match.return_value = match_result

    # Set muted BEFORE pipeline processes the utterance.
    daemon._muted = True

    pipeline_done = threading.Event()
    original_process = daemon._process_utterance

    def _patched_process(utt):
        original_process(utt)
        pipeline_done.set()

    daemon._process_utterance = _patched_process

    thread = threading.Thread(target=daemon._pipeline_loop, daemon=True)
    thread.start()

    daemon._utt_q.put(_fake_utterance())
    triggered = pipeline_done.wait(timeout=5.0)

    daemon._utt_q.put(None)
    thread.join(timeout=3.0)

    assert triggered, "pipeline did not process utterance within 5 s"
    transcriber.transcribe.assert_called_once()  # transcription still runs
    dispatcher.dispatch.assert_not_called()  # but dispatch is blocked by mute guard
