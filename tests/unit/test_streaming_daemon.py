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

# ---------------------------------------------------------------------------
# Helper: build a fully-mocked daemon
# ---------------------------------------------------------------------------


def _make_daemon(
    *,
    output_dir: str = "outputs",
    event_bus: EventBus | None = None,
    speak_fuzzy_threshold: int = 95,
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

    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=recorder,
        transcriber=transcriber,
        llm_router=llm_router,
        dispatcher=dispatcher,
        registry=MagicMock(),
        output_dir=output_dir,
        event_bus=event_bus,
        speak_fuzzy_threshold=speak_fuzzy_threshold,
    )
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
# Speak-mode state-transition tests (ADR 0072, supersedes ADR 0025 mute tests)
#
# State transition table (single source of truth in ADR 0072):
# Row 1: Scroll Lock, inactive → active, _speak_mode=F
# Row 2: Scroll Lock, active+normal → inactive, _speak_mode=F
# Row 3: Scroll Lock, active+speak-mode → inactive (synth Right Ctrl first)
# Row 4: Right Ctrl, inactive → no-op
# Row 5: Right Ctrl, active+normal → active+speak-mode
# Row 6: Right Ctrl, active+speak-mode → active+normal
# Row 7: (covered in pipeline tests) utterance "speak" exits speak-mode
# ---------------------------------------------------------------------------


def test_scroll_lock_opens_from_inactive(tmp_path):
    """Row 1: inactive + Scroll Lock → active, _speak_mode=False."""
    daemon, feedback, recorder, *_ = _make_daemon(output_dir=str(tmp_path))
    recorder.is_open = False
    daemon.on_scroll_lock()
    recorder.open_session.assert_called_once()
    assert daemon._session_active is True
    assert daemon._speak_mode is False
    assert any(c[0] == "on_recording_start" for c in feedback.calls)


def test_scroll_lock_closes_from_active_normal(tmp_path):
    """Row 2: active+normal + Scroll Lock → inactive, _speak_mode=False."""
    daemon, feedback, recorder, *_ = _make_daemon(output_dir=str(tmp_path))
    daemon._session_active = True
    daemon._speak_mode = False
    daemon.on_scroll_lock()
    recorder.close_session.assert_called_once()
    assert daemon._session_active is False
    assert daemon._speak_mode is False
    assert any(c[0] == "on_recording_stop" for c in feedback.calls)


def test_scroll_lock_closes_from_active_speak_mode(tmp_path):
    """Row 3: active+speak-mode + Scroll Lock → synth Right Ctrl + close session."""
    daemon, feedback, recorder, *_ = _make_daemon(output_dir=str(tmp_path))
    daemon._session_active = True
    daemon._speak_mode = True

    # Mock the speak tool in the registry.
    speak_func = MagicMock()
    speak_entry = MagicMock()
    speak_entry.func = speak_func
    daemon._registry.by_name.return_value = speak_entry

    daemon.on_scroll_lock()

    # Speak tool must have been called to synth Right Ctrl.
    speak_func.assert_called_once()
    # Stream must be closed.
    recorder.close_session.assert_called_once()
    assert daemon._session_active is False
    assert daemon._speak_mode is False
    assert any(c[0] == "on_recording_stop" for c in feedback.calls)


def test_speak_toggle_noop_when_inactive(tmp_path):
    """Row 4: inactive + Right Ctrl → silent no-op."""
    daemon, feedback, recorder, *_ = _make_daemon(output_dir=str(tmp_path))
    daemon._session_active = False
    daemon.on_speak_toggle()
    recorder.open_session.assert_not_called()
    recorder.close_session.assert_not_called()
    assert feedback.calls == []  # truly silent
    assert daemon._speak_mode is False


def test_speak_toggle_enters_speak_mode(tmp_path):
    """Row 5: active+normal + Right Ctrl → active+speak-mode."""
    daemon, feedback, recorder, *_ = _make_daemon(output_dir=str(tmp_path))
    daemon._session_active = True
    daemon._speak_mode = False
    daemon.on_speak_toggle()
    # Stream must NOT be touched (stays open).
    recorder.close_session.assert_not_called()
    recorder.open_session.assert_not_called()
    assert daemon._session_active is True
    assert daemon._speak_mode is True


def test_speak_toggle_exits_speak_mode(tmp_path):
    """Row 6: active+speak-mode + Right Ctrl → active+normal."""
    daemon, feedback, recorder, *_ = _make_daemon(output_dir=str(tmp_path))
    daemon._session_active = True
    daemon._speak_mode = True
    daemon.on_speak_toggle()
    # Stream must NOT be touched.
    recorder.close_session.assert_not_called()
    recorder.open_session.assert_not_called()
    assert daemon._session_active is True
    assert daemon._speak_mode is False


def test_speak_toggle_stream_stays_open(tmp_path):
    """ADR 0072: the audio stream is never touched by on_speak_toggle."""
    daemon, _, recorder, *_ = _make_daemon(output_dir=str(tmp_path))
    daemon._session_active = True
    daemon._speak_mode = False

    # Enter speak-mode
    daemon.on_speak_toggle()
    # Exit speak-mode
    daemon.on_speak_toggle()

    recorder.close_session.assert_not_called()
    recorder.open_session.assert_not_called()


# ---------------------------------------------------------------------------
# Pipeline speak-mode branch tests (Row 7 variants)
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


def test_pipeline_speak_branch_fuzzy_match_calls_speak_tool(tmp_path):
    """In speak-mode, a fuzzy-matching 'speak' transcript calls the speak tool."""
    daemon, _, recorder, transcriber, llm_router, dispatcher = _make_daemon(
        output_dir=str(tmp_path), speak_fuzzy_threshold=95
    )
    daemon._speak_mode = True

    result = _fake_transcription_result("speak", confidence=0.95)
    transcriber.transcribe.return_value = result

    speak_func = MagicMock()
    speak_entry = MagicMock()
    speak_entry.func = speak_func
    daemon._registry.by_name.return_value = speak_entry

    _run_process_utterance(daemon, tmp_path)

    speak_func.assert_called_once()
    llm_router.route.assert_not_called()
    dispatcher.run_plan.assert_not_called()


def test_pipeline_speak_branch_non_match_drops_silently(tmp_path):
    """In speak-mode, a non-matching transcript is silently dropped — no LLM call."""
    daemon, feedback, recorder, transcriber, llm_router, dispatcher = _make_daemon(
        output_dir=str(tmp_path), speak_fuzzy_threshold=95
    )
    daemon._speak_mode = True

    result = _fake_transcription_result("hello world", confidence=0.95)
    transcriber.transcribe.return_value = result

    speak_entry = MagicMock()
    daemon._registry.by_name.return_value = speak_entry

    _run_process_utterance(daemon, tmp_path)

    speak_entry.func.assert_not_called()
    llm_router.route.assert_not_called()
    dispatcher.run_plan.assert_not_called()


def test_pipeline_speak_branch_multi_word_drops_silently(tmp_path):
    """In speak-mode, 'speak louder' (multi-word) is silently dropped even if 'speak' is in it."""
    daemon, _, recorder, transcriber, llm_router, dispatcher = _make_daemon(
        output_dir=str(tmp_path), speak_fuzzy_threshold=95
    )
    daemon._speak_mode = True

    result = _fake_transcription_result("speak louder", confidence=0.95)
    transcriber.transcribe.return_value = result

    speak_entry = MagicMock()
    daemon._registry.by_name.return_value = speak_entry

    _run_process_utterance(daemon, tmp_path)

    speak_entry.func.assert_not_called()
    llm_router.route.assert_not_called()


def test_pipeline_speak_branch_skips_confidence_gate(tmp_path):
    """In speak-mode, confidence gate is bypassed — low-confidence 'speak' still triggers."""
    daemon, _, recorder, transcriber, llm_router, dispatcher = _make_daemon(
        output_dir=str(tmp_path), speak_fuzzy_threshold=95
    )
    daemon._speak_mode = True

    # confidence=0.05 is below the default min_confidence=0.30
    result = _fake_transcription_result("speak", confidence=0.05)
    transcriber.transcribe.return_value = result

    speak_func = MagicMock()
    speak_entry = MagicMock()
    speak_entry.func = speak_func
    daemon._registry.by_name.return_value = speak_entry

    _run_process_utterance(daemon, tmp_path)

    speak_func.assert_called_once()
    llm_router.route.assert_not_called()


# ---------------------------------------------------------------------------
# plan_outcome SSE event tests (miss paths)
# ---------------------------------------------------------------------------


def test_process_utterance_publishes_miss_on_route_none(tmp_path):
    """When LLM router returns None, plan_outcome status=miss is published."""
    bus = EventBus()
    daemon, feedback, recorder, transcriber, llm_router, dispatcher = _make_daemon(
        output_dir=str(tmp_path), event_bus=bus
    )

    result = _fake_transcription_result("gobbledygook", confidence=0.95)
    transcriber.transcribe.return_value = result
    llm_router.route.return_value = None

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
    """When _registry is None after routing, plan_outcome status=error is published
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

    daemon._process_utterance(_fake_utterance())

    dispatcher.run_plan.assert_not_called()
    assert any(name == "on_miss" for name, _ in feedback.calls), (
        f"Expected an on_miss call on the feedback sink; got {feedback.calls}"
    )
