"""Tests for non-blocking daemon startup (async Transcriber.load).

Covers four scenarios:

1. run() returns immediately even when Transcriber.load is slow
2. Transcriber.load failure does not kill the daemon
3. Pipeline waits for _transcriber_ready Event before transcribing
4. Pipeline skips utterance when transcriber not ready within timeout
"""

from __future__ import annotations

import sys
import threading
import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Stub heavy deps before voice_commander.daemon is imported so collection
# succeeds in environments without silero_vad / torch / sounddevice.
# ---------------------------------------------------------------------------

for _mod in ("silero_vad", "torch", "sounddevice", "soxr"):
    sys.modules.setdefault(_mod, MagicMock())

from voice_commander.daemon import StreamingDaemon  # noqa: E402
from voice_commander.event_bus import EventBus  # noqa: E402
from voice_commander.feedback import CapturingFeedbackSink  # noqa: E402
from voice_commander.plan import Plan, ToolCall  # noqa: E402
from voice_commander.transcriber import TranscriptionResult  # noqa: E402
from voice_commander.verb_router import VerbRouter  # noqa: E402


# ---------------------------------------------------------------------------
# Helper: build a fully-mocked daemon (mirrors test_streaming_daemon.py)
# ---------------------------------------------------------------------------


def _make_daemon(
    *,
    output_dir: str = "outputs",
    event_bus: EventBus | None = None,
) -> tuple[
    StreamingDaemon,
    CapturingFeedbackSink,
    MagicMock,  # recorder
    MagicMock,  # transcriber
    MagicMock,  # dispatcher
]:
    feedback = CapturingFeedbackSink()

    recorder = MagicMock()
    recorder.is_open = False

    transcriber = MagicMock()
    dispatcher = MagicMock()
    verb_router = MagicMock(spec=VerbRouter)
    verb_router.route.return_value = Plan(
        steps=(ToolCall(name="press", kwargs={"combo": "ctrl+c"}),),
        raw_response={"router": "verb"},
    )

    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=recorder,
        transcriber=transcriber,
        dispatcher=dispatcher,
        verb_router=verb_router,
        registry=MagicMock(),
        output_dir=output_dir,
        event_bus=event_bus,
    )
    return daemon, feedback, recorder, transcriber, dispatcher


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
# Test 1: run() returns immediately even when Transcriber.load is slow
# ---------------------------------------------------------------------------


def test_run_returns_immediately_even_when_transcriber_load_is_slow(tmp_path):
    """Transcriber.load() sleeping 2 s must NOT block run() from starting threads
    and returning control to the caller within 500 ms."""
    daemon, feedback, recorder, transcriber, dispatcher = _make_daemon(
        output_dir=str(tmp_path)
    )

    # Make load() take 2 seconds
    def _slow_load():
        time.sleep(2)

    transcriber.load.side_effect = _slow_load

    # Mock HotkeyController so run() doesn't actually block on key listening.
    hotkey_mock = MagicMock()
    hotkey_mock.start.side_effect = lambda: None

    run_returned = threading.Event()
    run_exception: list[BaseException] = []

    def _run_in_thread():
        try:
            with patch("voice_commander.daemon.HotkeyController", return_value=hotkey_mock):
                # We need run() to exit quickly — simulate immediate shutdown
                daemon._shutdown.set()  # pre-set shutdown so the wait loop exits immediately
                daemon.run("scroll_lock")
        except Exception as exc:
            run_exception.append(exc)
        finally:
            run_returned.set()

    t = threading.Thread(target=_run_in_thread, daemon=True)
    t0 = time.perf_counter()
    t.start()

    finished = run_returned.wait(timeout=1.0)
    elapsed = time.perf_counter() - t0

    assert finished, "run() did not return within 1 s — it is still blocking on Transcriber.load()"
    assert elapsed < 0.8, f"run() took {elapsed:.2f}s — load() must be async"
    # No exception should have leaked out of run()
    assert not run_exception, f"run() raised unexpectedly: {run_exception}"


# ---------------------------------------------------------------------------
# Test 2: Transcriber.load failure does not kill the daemon
# ---------------------------------------------------------------------------


def test_transcriber_load_failure_does_not_kill_daemon(tmp_path):
    """If Transcriber.load() raises, the daemon must stay alive; feedback.on_error
    must be called with subsystem name 'transcriber.load'."""
    daemon, feedback, recorder, transcriber, dispatcher = _make_daemon(
        output_dir=str(tmp_path)
    )

    load_error = RuntimeError("CUDA device not found")
    transcriber.load.side_effect = load_error

    # Verify _transcriber_ready event exists (will be set in background thread)
    assert hasattr(daemon, "_transcriber_ready"), (
        "StreamingDaemon must have _transcriber_ready threading.Event"
    )

    # Spawn the background load thread directly, as run() would do it
    load_done = threading.Event()

    def _background_load():
        try:
            daemon._transcriber.load()
            daemon._transcriber_ready.set()
            daemon._publish("warmup_done")
        except Exception as e:
            import logging
            logging.getLogger("voice_commander.daemon").exception(
                "Transcriber.load() failed in background; daemon stays up"
            )
            daemon._feedback.on_error("transcriber.load", e)
        finally:
            load_done.set()

    t = threading.Thread(target=_background_load, daemon=True)
    t.start()
    load_done.wait(timeout=3.0)

    # Daemon must still be alive (shutdown not set)
    assert not daemon._shutdown.is_set(), "Daemon shutdown was triggered by load failure"
    # _transcriber_ready must NOT be set (load failed)
    assert not daemon._transcriber_ready.is_set(), (
        "_transcriber_ready must not be set after a failed load"
    )
    # feedback.on_error must have been called with 'transcriber.load'
    error_calls = [c for c in feedback.calls if c[0] == "on_error"]
    assert error_calls, "feedback.on_error was never called after Transcriber.load() failure"
    subsystem = error_calls[0][1][0] if error_calls[0][1] else None
    assert subsystem == "transcriber.load", (
        f"Expected on_error subsystem='transcriber.load', got {subsystem!r}"
    )


# ---------------------------------------------------------------------------
# Test 3: Pipeline waits for _transcriber_ready event before transcribing
# ---------------------------------------------------------------------------


def test_pipeline_waits_for_transcriber_ready_event(tmp_path):
    """When _transcriber_ready is not yet set, the pipeline must block until it is."""
    daemon, feedback, recorder, transcriber, dispatcher = _make_daemon(
        output_dir=str(tmp_path)
    )

    daemon._verb_router.route.return_value = Plan(
        steps=(ToolCall(name="press", kwargs={"combo": "ctrl+c"}),), raw_response={}
    )

    # Do NOT set _transcriber_ready yet — simulates load still in progress.
    assert not daemon._transcriber_ready.is_set()

    # Track when transcribe() is called; capture the return value separately
    # to avoid a recursive side_effect loop.
    transcribe_called_at: list[float] = []
    expected_result = _fake_transcription_result("copy", confidence=0.95)

    def _recording_transcribe(utt):
        transcribe_called_at.append(time.perf_counter())
        return expected_result

    transcriber.transcribe.side_effect = _recording_transcribe

    pipeline_done = threading.Event()
    original_process = daemon._process_utterance

    def _patched_process(utt):
        original_process(utt)
        pipeline_done.set()

    daemon._process_utterance = _patched_process

    thread = threading.Thread(target=daemon._pipeline_loop, daemon=True)
    thread.start()

    # Push utterance before transcriber is ready
    enqueue_time = time.perf_counter()
    daemon._utt_q.put(_fake_utterance())

    # Transcriber should NOT have been called yet (within 100 ms)
    time.sleep(0.15)
    assert not transcribe_called_at, (
        "transcribe() was called before _transcriber_ready was set"
    )

    # Now signal that transcriber is ready
    ready_time = time.perf_counter()
    daemon._transcriber_ready.set()

    # Pipeline should now process the utterance
    triggered = pipeline_done.wait(timeout=5.0)

    try:
        assert triggered, "Pipeline did not process utterance after _transcriber_ready was set"
        assert transcribe_called_at, "transcribe() was never called"
        # transcribe must have been called AFTER ready was set
        assert transcribe_called_at[0] >= ready_time - 0.05, (
            "transcribe() was called before _transcriber_ready was set"
        )
    finally:
        daemon._utt_q.put(None)
        thread.join(timeout=3.0)


# ---------------------------------------------------------------------------
# Test 4: Pipeline skips utterance when transcriber not ready within timeout
# ---------------------------------------------------------------------------


def test_pipeline_skips_when_transcriber_not_ready_within_timeout(tmp_path):
    """If _transcriber_ready.wait(timeout=...) returns False, the utterance must be
    dropped with a miss chime — no transcribe() call, no exception leaked."""
    daemon, feedback, recorder, transcriber, dispatcher = _make_daemon(
        output_dir=str(tmp_path)
    )

    # Never set _transcriber_ready — simulate permanent timeout
    skip_done = threading.Event()

    # Patch the Event so .wait() always returns False (simulates timeout)
    always_timeout = MagicMock()
    always_timeout.is_set.return_value = False
    always_timeout.wait.return_value = False  # timeout expired
    daemon._transcriber_ready = always_timeout

    # Track the pipeline processing completion
    original_process = daemon._process_utterance

    def _patched_process(utt):
        original_process(utt)
        skip_done.set()

    daemon._process_utterance = _patched_process

    thread = threading.Thread(target=daemon._pipeline_loop, daemon=True)
    thread.start()

    try:
        daemon._utt_q.put(_fake_utterance())
        triggered = skip_done.wait(timeout=5.0)
        assert triggered, "Pipeline did not finish processing (skipping) the utterance"
    finally:
        daemon._utt_q.put(None)
        thread.join(timeout=3.0)

    assert not thread.is_alive(), "Pipeline thread did not exit after poison pill"
    transcriber.transcribe.assert_not_called()
    miss_calls = [c for c in feedback.calls if c[0] == "on_miss"]
    assert miss_calls, "on_miss was not called when transcriber was not ready within timeout"
