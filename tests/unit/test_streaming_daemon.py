"""Unit tests for voice_commander.daemon.StreamingDaemon.

All hardware subsystems (recorder, transcriber, llm_router, dispatcher) are
replaced with MagicMocks.  Utterance ndarrays are injected directly into the
pipeline queue so no real audio or GPU work occurs.

Hardware tests (requiring a live audio + GPU stack) are marked with
@pytest.mark.hardware.
"""

from __future__ import annotations

import sys
import threading
from unittest.mock import MagicMock

import numpy as np

# ---------------------------------------------------------------------------
# Stub heavy deps before voice_commander.daemon is imported so collection
# succeeds in environments without silero_vad / torch / sounddevice.
# ---------------------------------------------------------------------------

for _mod in ("silero_vad", "torch", "sounddevice", "soxr"):
    sys.modules.setdefault(_mod, MagicMock())

from voice_commander.daemon import StreamingDaemon  # noqa: E402
from voice_commander.event_bus import EventBus  # noqa: E402
from voice_commander.feedback import CapturingFeedbackSink  # noqa: E402
from voice_commander.llm_router import LLMRouter  # noqa: E402
from voice_commander.plan import Plan, ToolCall  # noqa: E402
from voice_commander.transcriber import TranscriptionResult  # noqa: E402
from voice_commander.verb_router import VerbRouter  # noqa: E402

# ---------------------------------------------------------------------------
# Helper: build a fully-mocked daemon
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
    MagicMock,  # llm_router
    MagicMock,  # dispatcher
]:
    feedback = CapturingFeedbackSink()

    recorder = MagicMock()
    recorder.is_open = False

    transcriber = MagicMock()
    llm_router = MagicMock(spec=LLMRouter)
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
        llm_router=llm_router,
        dispatcher=dispatcher,
        verb_router=verb_router,
        registry=MagicMock(),
        output_dir=output_dir,
        event_bus=event_bus,
    )
    # Mock transcribers are always "ready" — simulate a completed background load.
    daemon._transcriber_ready.set()
    return daemon, feedback, recorder, transcriber, llm_router, dispatcher


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
    """Utterance placed in _utt_q flows through transcribe → route → run_plan."""
    daemon, feedback, recorder, transcriber, llm_router, dispatcher = _make_daemon(
        output_dir=str(tmp_path)
    )

    result = _fake_transcription_result("copy", confidence=0.95)
    transcriber.transcribe.return_value = result

    plan = Plan(steps=(ToolCall(name="press", kwargs={"combo": "ctrl+c"}),), raw_response={})
    llm_router.route.return_value = plan

    # Start pipeline thread.
    pipeline_done = threading.Event()
    original_process = daemon._process_utterance

    def _patched_process(utt):
        original_process(utt)
        pipeline_done.set()

    daemon._process_utterance = _patched_process

    thread = threading.Thread(target=daemon._pipeline_loop, daemon=True)
    thread.start()

    try:
        daemon._utt_q.put(_fake_utterance())
        triggered = pipeline_done.wait(timeout=5.0)
        assert triggered, "pipeline did not process utterance within 5 s"
    finally:
        daemon._utt_q.put(None)
        thread.join(timeout=3.0)

    assert not thread.is_alive(), "pipeline thread did not exit after poison pill"
    transcriber.transcribe.assert_called_once()
    dispatcher.run_plan.assert_called_once()


def test_pipeline_handles_transcribe_error(tmp_path):
    """RuntimeError from transcriber.transcribe is caught; on_error is called;
    the pipeline thread exits cleanly after receiving the poison pill."""
    daemon, feedback, recorder, transcriber, llm_router, dispatcher = _make_daemon(
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

    try:
        daemon._utt_q.put(_fake_utterance())
        triggered = error_seen.wait(timeout=5.0)
        assert triggered, "on_error was never called after transcribe exception"
    finally:
        daemon._utt_q.put(None)
        thread.join(timeout=3.0)

    assert not thread.is_alive(), "pipeline thread did not exit after poison pill"
    assert any(c[0] == "on_error" for c in feedback.calls)
    dispatcher.run_plan.assert_not_called()


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
# plan_outcome SSE event tests (miss paths)
# ---------------------------------------------------------------------------


def test_process_utterance_publishes_miss_on_route_none(tmp_path):
    """When router returns None in Merlin mode, plan_outcome status=miss is published."""
    bus = EventBus()
    daemon, feedback, recorder, transcriber, llm_router, dispatcher = _make_daemon(
        output_dir=str(tmp_path), event_bus=bus
    )

    result = _fake_transcription_result("gobbledygook", confidence=0.95)
    transcriber.transcribe.return_value = result
    llm_router.route.return_value = None

    # Force LLM path so the miss-on-None path is exercised.
    daemon._merlin_mode = True

    _run_process_utterance(daemon, tmp_path)

    outcomes = [e for e in bus.replay_after(0) if e.type == "plan_outcome"]
    assert len(outcomes) == 1
    payload = outcomes[0].data
    assert payload["status"] == "miss"
    assert payload["transcript"] == "gobbledygook"
    assert payload["steps"] == []
    assert payload["failed_step_index"] is None
    assert payload["error_msg"] is None
    assert payload["duration_ms"] >= 0
    dispatcher.run_plan.assert_not_called()


def test_process_utterance_publishes_miss_on_confidence_gate(tmp_path):
    """When confidence is below threshold, plan_outcome status=miss is published."""
    bus = EventBus()
    daemon, feedback, recorder, transcriber, llm_router, dispatcher = _make_daemon(
        output_dir=str(tmp_path), event_bus=bus
    )

    # confidence below default min_confidence of 0.30
    result = _fake_transcription_result("something", confidence=0.10)
    transcriber.transcribe.return_value = result

    _run_process_utterance(daemon, tmp_path)

    outcomes = [e for e in bus.replay_after(0) if e.type == "plan_outcome"]
    assert len(outcomes) == 1
    payload = outcomes[0].data
    assert payload["status"] == "miss"
    assert payload["transcript"] == "something"
    assert payload["steps"] == []
    dispatcher.run_plan.assert_not_called()


def test_process_utterance_no_miss_event_on_word_count_gate(tmp_path):
    """Word-count gate is infra noise — no plan_outcome published when it fires."""
    bus = EventBus()
    daemon, feedback, recorder, transcriber, llm_router, dispatcher = _make_daemon(
        output_dir=str(tmp_path), event_bus=bus
    )
    # min_word_count defaults to 1; empty string has 0 words
    result = _fake_transcription_result("", confidence=0.95)
    transcriber.transcribe.return_value = result

    _run_process_utterance(daemon, tmp_path)

    outcomes = [e for e in bus.replay_after(0) if e.type == "plan_outcome"]
    assert outcomes == [], "word-count gate must not emit plan_outcome"


def test_process_utterance_no_miss_event_on_no_speech_gate(tmp_path):
    """no_speech_prob gate is infra noise — no plan_outcome published when it fires."""
    bus = EventBus()
    daemon, feedback, recorder, transcriber, llm_router, dispatcher = _make_daemon(
        output_dir=str(tmp_path), event_bus=bus
    )
    # no_speech_prob above default max of 0.6
    result = _fake_transcription_result("noise", confidence=0.95, no_speech_prob=0.9)
    transcriber.transcribe.return_value = result

    _run_process_utterance(daemon, tmp_path)

    outcomes = [e for e in bus.replay_after(0) if e.type == "plan_outcome"]
    assert outcomes == [], "no_speech_prob gate must not emit plan_outcome"


def test_process_utterance_publishes_error_when_registry_none(tmp_path):
    """When _registry is None after LLM routing, plan_outcome status=error is published
    and dispatcher.run_plan is never called."""
    bus = EventBus()
    daemon, feedback, recorder, transcriber, llm_router, dispatcher = _make_daemon(
        output_dir=str(tmp_path), event_bus=bus
    )
    daemon._registry = None  # force the error path

    result = _fake_transcription_result("open spotify", confidence=0.95)
    transcriber.transcribe.return_value = result
    plan = Plan(steps=(ToolCall(name="open", kwargs={"target": "spotify"}),), raw_response={})
    llm_router.route.return_value = plan

    # Force LLM path so the original test intent is preserved.
    daemon._merlin_mode = True

    _run_process_utterance(daemon, tmp_path)

    outcomes = [e for e in bus.replay_after(0) if e.type == "plan_outcome"]
    assert len(outcomes) == 1
    payload = outcomes[0].data
    assert payload["status"] == "error"
    assert payload["error_msg"] == "registry not initialized"
    assert payload["transcript"] == "open spotify"
    assert payload["failed_step_index"] is None
    assert payload["duration_ms"] >= 0
    dispatcher.run_plan.assert_not_called()


def test_process_utterance_calls_on_miss_when_plan_is_none(tmp_path):
    """Fast-path None → feedback.on_miss once; no retry, no agentic."""
    daemon, feedback, recorder, transcriber, llm_router, dispatcher = _make_daemon(
        output_dir=str(tmp_path)
    )

    result = _fake_transcription_result("hello friend", confidence=0.95)
    transcriber.transcribe.return_value = result
    llm_router.route.return_value = None  # no matching command/workflow

    # Force LLM path so the original test intent is preserved.
    daemon._merlin_mode = True

    daemon._process_utterance(_fake_utterance())

    llm_router.route.assert_called_once()
    dispatcher.run_plan.assert_not_called()
    assert any(name == "on_miss" for name, _ in feedback.calls), (
        f"Expected an on_miss call on the feedback sink; got {feedback.calls}"
    )


# ---------------------------------------------------------------------------
# Merlin-gated verb-router tests (ADR 0074)
# ---------------------------------------------------------------------------


def test_normal_mode_uses_verb_router(tmp_path):
    """In normal mode (_merlin_mode=False) the verb router is consulted."""
    daemon, feedback, recorder, transcriber, llm_router, dispatcher = _make_daemon(
        output_dir=str(tmp_path)
    )

    result = _fake_transcription_result("copy", confidence=0.95)
    transcriber.transcribe.return_value = result

    daemon._process_utterance(_fake_utterance())

    assert daemon._merlin_mode is False
    llm_router.route.assert_not_called()
    dispatcher.run_plan.assert_called_once()

    args = dispatcher.run_plan.call_args
    assert args[0][1].raw_response["router"] == "verb"


def test_merlin_toggle_turns_llm_mode_on_and_off(tmp_path):
    """Saying 'Merlin' toggles _merlin_mode; next utterance uses LLM router."""
    daemon, feedback, recorder, transcriber, llm_router, dispatcher = _make_daemon(
        output_dir=str(tmp_path)
    )

    # Toggle on
    result = _fake_transcription_result("Merlin", confidence=0.95)
    transcriber.transcribe.return_value = result

    daemon._process_utterance(_fake_utterance())
    assert daemon._merlin_mode is True
    llm_router.route.assert_not_called()
    dispatcher.run_plan.assert_not_called()
    assert not any(name == "on_miss" for name, _ in feedback.calls)

    # Utterance while in Merlin mode → LLM router
    daemon, feedback, recorder, transcriber, llm_router, dispatcher = _make_daemon(
        output_dir=str(tmp_path)
    )
    daemon._merlin_mode = True
    llm_router.route.return_value = Plan(
        steps=(ToolCall(name="open", kwargs={"target": "spotify"}),),
        raw_response={},
    )

    result = _fake_transcription_result("open spotify", confidence=0.95)
    transcriber.transcribe.return_value = result

    daemon._process_utterance(_fake_utterance())
    llm_router.route.assert_called_once_with("open spotify")
    dispatcher.run_plan.assert_called_once()

    # Toggle off
    result = _fake_transcription_result("merlin", confidence=0.95)
    transcriber.transcribe.return_value = result
    daemon._process_utterance(_fake_utterance())
    assert daemon._merlin_mode is False


def test_session_reset_clears_merlin_mode(tmp_path):
    """Closing a session resets _merlin_mode to False."""
    daemon, feedback, recorder, *_ = _make_daemon(output_dir=str(tmp_path))
    daemon._session_active = True
    daemon._merlin_mode = True
    recorder.is_open = True

    daemon.on_scroll_lock()

    assert daemon._merlin_mode is False
    assert daemon._session_active is False

    # Re-opening must also reset
    daemon._session_active = False
    daemon._merlin_mode = True
    recorder.is_open = False

    daemon.on_scroll_lock()
    assert daemon._merlin_mode is False
    assert daemon._session_active is True


def test_merlin_mode_routes_to_llm_router(tmp_path):
    """When _merlin_mode=True, LLM router is used and verb router is not."""
    daemon, feedback, recorder, transcriber, llm_router, dispatcher = _make_daemon(
        output_dir=str(tmp_path)
    )
    daemon._merlin_mode = True

    llm_router.route.return_value = Plan(
        steps=(ToolCall(name="open", kwargs={"target": "spotify"}),),
        raw_response={},
    )

    result = _fake_transcription_result("open spotify", confidence=0.95)
    transcriber.transcribe.return_value = result

    daemon._process_utterance(_fake_utterance())

    llm_router.route.assert_called_once_with("open spotify")
    dispatcher.run_plan.assert_called_once()


def test_merlin_toggle_does_not_fire_miss(tmp_path):
    """The 'Merlin' toggle utterance must not trigger on_miss or plan_outcome."""
    bus = EventBus()
    daemon, feedback, recorder, transcriber, llm_router, dispatcher = _make_daemon(
        output_dir=str(tmp_path), event_bus=bus
    )

    result = _fake_transcription_result("Merlin", confidence=0.95)
    transcriber.transcribe.return_value = result

    daemon._process_utterance(_fake_utterance())

    assert not any(name == "on_miss" for name, _ in feedback.calls)
    llm_router.route.assert_not_called()
    dispatcher.run_plan.assert_not_called()
    outcomes = [e for e in bus.replay_after(0) if e.type == "plan_outcome"]
    assert outcomes == []


# ---------------------------------------------------------------------------
# Helper for running _process_utterance via pipeline thread
# ---------------------------------------------------------------------------


def _run_process_utterance(daemon: StreamingDaemon, tmp_path) -> None:
    """Run _process_utterance synchronously via the pipeline thread."""
    done = threading.Event()
    original = daemon._process_utterance

    def _patched(utt):
        original(utt)
        done.set()

    daemon._process_utterance = _patched

    thread = threading.Thread(target=daemon._pipeline_loop, daemon=True)
    thread.start()
    try:
        daemon._utt_q.put(_fake_utterance())
        assert done.wait(timeout=5.0), "pipeline did not process utterance within 5 s"
    finally:
        daemon._utt_q.put(None)
        thread.join(timeout=3.0)

    assert not thread.is_alive(), "pipeline thread did not exit after poison pill"
