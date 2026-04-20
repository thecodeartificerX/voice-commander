"""Targeted tests to close coverage gaps in daemon.py.

Covers:
- Phase1.on_toggle error branches (recorder.start raises, recorder.stop raises)
- Phase1.run main loop (keyboard-interrupt path via mocked hotkey)
- Phase1.shutdown edge paths (hotkey none, recorder.stop raises)
- Phase2.on_toggle when recorder.stop raises while recording
- Phase2._worker_loop error branch (transcribe raises)
- Phase2.shutdown with worker still alive (join timeout path)
- Phase2.shutdown when transcriber.unload raises
- build_phase2 returns a wired Phase2Daemon
- Phase3._worker_loop full pipeline (match + dispatch, low-confidence miss)
- build_phase3 returns a wired Phase3Daemon
"""
from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from voice_commander.config import Config
from voice_commander.daemon import (
    Phase1Daemon,
    Phase2Daemon,
    Phase3Daemon,
    build_phase2,
    build_phase3,
)
from voice_commander.feedback import CapturingFeedbackSink
from voice_commander.transcriber import TranscriptionResult


# ---------------------------------------------------------------------------
# Phase1 on_toggle error branches
# ---------------------------------------------------------------------------

def test_phase1_toggle_start_raises_calls_on_error():
    """recorder.start() raises → on_error("recorder.start", ...) is reported."""
    feedback = CapturingFeedbackSink()
    recorder = MagicMock()
    recorder.is_recording = False
    recorder.start.side_effect = RuntimeError("mic broken")

    daemon = Phase1Daemon(feedback=feedback, recorder=recorder)
    daemon.on_toggle()

    errors = [c for c in feedback.calls if c[0] == "on_error"]
    assert errors, "expected on_error to be called"
    assert errors[0][1][0] == "recorder.start"
    assert isinstance(errors[0][1][1], RuntimeError)


def test_phase1_toggle_stop_raises_calls_on_error(tmp_path):
    """recorder.stop() raises → on_error("recorder.stop", ...) is reported."""
    feedback = CapturingFeedbackSink()
    recorder = MagicMock()
    recorder.is_recording = True
    recorder.stop.side_effect = RuntimeError("write failed")

    daemon = Phase1Daemon(feedback=feedback, recorder=recorder)
    daemon.on_toggle()

    errors = [c for c in feedback.calls if c[0] == "on_error"]
    assert errors, "expected on_error to be called"
    assert errors[0][1][0] == "recorder.stop"
    assert isinstance(errors[0][1][1], RuntimeError)


# ---------------------------------------------------------------------------
# Phase1.run main loop — keyboard-interrupt path
# ---------------------------------------------------------------------------

def test_phase1_run_keyboard_interrupt_shuts_down(monkeypatch):
    """KeyboardInterrupt inside the wait loop triggers shutdown cleanly."""
    feedback = CapturingFeedbackSink()
    recorder = MagicMock()
    recorder.is_recording = False

    class ImmediateInterruptHotkey:
        def __init__(self, *a, **k): pass
        def start(self): pass
        def stop(self): pass

    monkeypatch.setattr("voice_commander.daemon.HotkeyController", ImmediateInterruptHotkey)

    daemon = Phase1Daemon(feedback=feedback, recorder=recorder)

    # Patch threading.Event.wait to raise KeyboardInterrupt on first call
    original_wait = threading.Event.wait
    call_count = [0]

    def _patched_wait(self, timeout=None):
        call_count[0] += 1
        if call_count[0] == 1:
            raise KeyboardInterrupt
        return original_wait(self, timeout)

    monkeypatch.setattr(threading.Event, "wait", _patched_wait)

    daemon.run("scroll_lock")  # must return, not hang

    assert daemon._shutdown.is_set()


# ---------------------------------------------------------------------------
# Phase1.shutdown edge paths
# ---------------------------------------------------------------------------

def test_phase1_shutdown_when_no_hotkey_and_not_recording():
    """shutdown() with _hotkey=None and not recording is a no-op beyond set."""
    feedback = CapturingFeedbackSink()
    recorder = MagicMock()
    recorder.is_recording = False

    daemon = Phase1Daemon(feedback=feedback, recorder=recorder)
    # _hotkey is None by default — this exercises the `if self._hotkey is not None` branch
    daemon.shutdown()

    assert daemon._shutdown.is_set()
    recorder.stop.assert_not_called()


def test_phase1_shutdown_recorder_stop_raises_is_swallowed(tmp_path):
    """If recorder.stop() raises during shutdown, the exception is eaten."""
    feedback = CapturingFeedbackSink()
    recorder = MagicMock()
    recorder.is_recording = True
    recorder.stop.side_effect = OSError("disk full")

    daemon = Phase1Daemon(feedback=feedback, recorder=recorder)
    daemon.shutdown()  # must not raise

    assert daemon._shutdown.is_set()


# ---------------------------------------------------------------------------
# Phase2 on_toggle error branch
# ---------------------------------------------------------------------------

def test_phase2_toggle_stop_raises_calls_on_error():
    """Phase2.on_toggle: recorder.stop raises while recording → on_error."""
    feedback = CapturingFeedbackSink()
    recorder = MagicMock()
    recorder.is_recording = True
    recorder.stop.side_effect = RuntimeError("io error")
    transcriber = MagicMock()

    daemon = Phase2Daemon(feedback=feedback, recorder=recorder, transcriber=transcriber)
    daemon.on_toggle()

    errors = [c for c in feedback.calls if c[0] == "on_error"]
    assert errors, "expected on_error to be called"
    assert errors[0][1][0] == "recorder.stop"


# ---------------------------------------------------------------------------
# Phase2._worker_loop error branch
# ---------------------------------------------------------------------------

def test_phase2_worker_transcribe_error_calls_on_error(tmp_path):
    """If transcriber.transcribe() raises, on_error("transcribe", ...) is reported."""
    feedback = CapturingFeedbackSink()
    recorder = MagicMock()
    transcriber = MagicMock()
    transcriber.transcribe.side_effect = RuntimeError("CUDA OOM")

    daemon = Phase2Daemon(feedback=feedback, recorder=recorder, transcriber=transcriber)
    daemon.start_worker()

    fake_wav = tmp_path / "x.wav"
    fake_wav.write_bytes(b"")
    daemon._queue.put(fake_wav)
    daemon._queue.put(None)  # poison pill
    daemon._worker.join(timeout=2.0)

    errors = [c for c in feedback.calls if c[0] == "on_error"]
    assert errors
    assert errors[0][1][0] == "transcribe"


# ---------------------------------------------------------------------------
# Phase2.shutdown paths
# ---------------------------------------------------------------------------

def test_phase2_shutdown_worker_slow_join_logs_warning(tmp_path, caplog):
    """If the worker doesn't finish within timeout, a warning is logged."""
    import logging

    feedback = CapturingFeedbackSink()
    recorder = MagicMock()
    recorder.is_recording = False
    transcriber = MagicMock()
    transcriber.unload.return_value = None

    # Make transcriber.transcribe block indefinitely until we release
    gate = threading.Event()
    def _blocking_transcribe(path):
        gate.wait()
        return TranscriptionResult(text="hi", language="en", duration_ms=100, confidence=0.9)
    transcriber.transcribe.side_effect = _blocking_transcribe

    daemon = Phase2Daemon(feedback=feedback, recorder=recorder, transcriber=transcriber)
    daemon.start_worker()

    fake_wav = tmp_path / "x.wav"
    fake_wav.write_bytes(b"")
    daemon._queue.put(fake_wav)

    # Patch worker.join to simulate timeout (is_alive returns True after join)
    original_join = threading.Thread.join
    join_calls = [0]

    def _fast_join(self, timeout=None):
        join_calls[0] += 1
        # Don't actually join — simulate timeout by returning immediately
        # is_alive will still return True because the thread is blocking on gate
        return

    with patch.object(threading.Thread, "join", _fast_join):
        with caplog.at_level(logging.WARNING, logger="voice_commander.daemon"):
            daemon.shutdown()

    assert any("did not exit" in r.message for r in caplog.records)

    # Let the blocking transcribe finish so the thread can clean up
    gate.set()


def test_phase2_shutdown_transcriber_unload_raises_is_logged(caplog):
    """If transcriber.unload() raises, it's logged but does not propagate."""
    import logging

    feedback = CapturingFeedbackSink()
    recorder = MagicMock()
    recorder.is_recording = False
    transcriber = MagicMock()
    transcriber.unload.side_effect = RuntimeError("unload failed")

    daemon = Phase2Daemon(feedback=feedback, recorder=recorder, transcriber=transcriber)
    daemon.start_worker()

    with caplog.at_level(logging.ERROR, logger="voice_commander.daemon"):
        daemon.shutdown()  # must not raise

    assert any("Error unloading transcriber" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# build_phase2
# ---------------------------------------------------------------------------

def test_build_phase2_returns_phase2_daemon(tmp_path, monkeypatch):
    """build_phase2 wires up a Phase2Daemon using config values."""
    # Prevent Recorder, Transcriber, WindowsFeedbackSink from touching hardware
    monkeypatch.setattr("voice_commander.daemon.Recorder", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr("voice_commander.daemon.Transcriber", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr("voice_commander.daemon.WindowsFeedbackSink", MagicMock(return_value=MagicMock()))

    cfg = Config()
    daemon = build_phase2(cfg)

    assert isinstance(daemon, Phase2Daemon)


# ---------------------------------------------------------------------------
# Phase3 worker loop — full pipeline paths
# ---------------------------------------------------------------------------

def _make_phase3(feedback, recorder, transcriber, matcher, dispatcher, min_confidence=0.30):
    registry = MagicMock()
    return Phase3Daemon(
        feedback=feedback,
        recorder=recorder,
        transcriber=transcriber,
        registry=registry,
        matcher=matcher,
        dispatcher=dispatcher,
        min_confidence=min_confidence,
    )


def test_phase3_worker_dispatches_match(tmp_path):
    """High-confidence transcript → matcher.match called, dispatcher.dispatch called."""
    from voice_commander.matcher import MatchResult

    feedback = CapturingFeedbackSink()
    recorder = MagicMock()
    transcriber = MagicMock()
    transcriber.transcribe.return_value = TranscriptionResult(
        text="copy", language="en", duration_ms=200, confidence=0.95
    )

    fake_tool = MagicMock()
    fake_tool.name = "copy"
    match_result = MatchResult(tool=fake_tool, phrase="copy", score=95.0, candidates=())
    matcher = MagicMock()
    matcher.match.return_value = match_result
    dispatcher = MagicMock()

    daemon = _make_phase3(feedback, recorder, transcriber, matcher, dispatcher, min_confidence=0.30)
    daemon.start_worker()

    fake_wav = tmp_path / "x.wav"
    fake_wav.write_bytes(b"")
    daemon._queue.put(fake_wav)
    daemon._queue.put(None)
    daemon._worker.join(timeout=2.0)

    matcher.match.assert_called_once_with("copy")
    dispatcher.dispatch.assert_called_once_with("copy", match_result)


def test_phase3_worker_low_confidence_calls_on_miss(tmp_path):
    """Low-confidence transcript (below min_confidence) → on_miss, no dispatch."""
    feedback = CapturingFeedbackSink()
    recorder = MagicMock()
    transcriber = MagicMock()
    transcriber.transcribe.return_value = TranscriptionResult(
        text="mumble", language="en", duration_ms=200, confidence=0.1
    )
    matcher = MagicMock()
    dispatcher = MagicMock()

    daemon = _make_phase3(feedback, recorder, transcriber, matcher, dispatcher, min_confidence=0.30)
    daemon.start_worker()

    fake_wav = tmp_path / "x.wav"
    fake_wav.write_bytes(b"")
    daemon._queue.put(fake_wav)
    daemon._queue.put(None)
    daemon._worker.join(timeout=2.0)

    misses = [c for c in feedback.calls if c[0] == "on_miss"]
    assert misses, "expected on_miss to be called for low-confidence result"
    matcher.match.assert_not_called()
    dispatcher.dispatch.assert_not_called()


def test_phase3_worker_pipeline_error_calls_on_error(tmp_path):
    """If matcher.match raises, on_error("pipeline", ...) is reported."""
    feedback = CapturingFeedbackSink()
    recorder = MagicMock()
    transcriber = MagicMock()
    transcriber.transcribe.return_value = TranscriptionResult(
        text="copy", language="en", duration_ms=200, confidence=0.95
    )
    matcher = MagicMock()
    matcher.match.side_effect = RuntimeError("registry broken")
    dispatcher = MagicMock()

    daemon = _make_phase3(feedback, recorder, transcriber, matcher, dispatcher, min_confidence=0.30)
    daemon.start_worker()

    fake_wav = tmp_path / "x.wav"
    fake_wav.write_bytes(b"")
    daemon._queue.put(fake_wav)
    daemon._queue.put(None)
    daemon._worker.join(timeout=2.0)

    errors = [c for c in feedback.calls if c[0] == "on_error"]
    assert errors
    assert errors[0][1][0] == "pipeline"


# ---------------------------------------------------------------------------
# build_phase3
# ---------------------------------------------------------------------------

def test_build_phase3_returns_phase3_daemon(monkeypatch):
    """build_phase3 wires up a Phase3Daemon using config values."""
    monkeypatch.setattr("voice_commander.daemon.Recorder", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr("voice_commander.daemon.Transcriber", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr("voice_commander.daemon.WindowsFeedbackSink", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr("voice_commander.daemon.discover", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr("voice_commander.daemon.Matcher", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr("voice_commander.daemon.Dispatcher", MagicMock(return_value=MagicMock()))

    cfg = Config()
    daemon = build_phase3(cfg)

    assert isinstance(daemon, Phase3Daemon)
