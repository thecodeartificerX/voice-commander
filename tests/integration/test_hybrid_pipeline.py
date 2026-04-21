"""Integration tests: hybrid routing logic in StreamingDaemon._process_utterance.

These tests wire a minimal StreamingDaemon with a mocked Transcriber so that
routing decisions (hot-path vs LLM escalation vs fallback miss) can be driven
by controlled TranscriptionResult values without touching CUDA or a real LLM.

Three routing paths exist in _process_utterance:

1. Hot path  — rapidfuzz match above threshold → Dispatcher.dispatch()
2. LLM path  — no fuzzy match + llm_router present → run_plan() or on_miss()
3. Fallback  — no fuzzy match + no llm_router → Dispatcher.dispatch() (miss)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from voice_commander.daemon import StreamingDaemon
from voice_commander.dispatcher import Dispatcher
from voice_commander.feedback import CapturingFeedbackSink
from voice_commander.matcher import Matcher
from voice_commander.plan import Plan, ToolCall
from voice_commander.registry import ToolEntry, ToolRegistry
from voice_commander.transcriber import TranscriptionResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_result(text: str, confidence: float = 0.9) -> TranscriptionResult:
    """Build a TranscriptionResult that passes all gates by default."""
    return TranscriptionResult(
        text=text,
        language="en",
        duration_ms=500,
        confidence=confidence,
        no_speech_prob=0.05,
    )


def _make_registry(*names: str) -> tuple[ToolRegistry, dict[str, list[object]]]:
    """Return a registry with one tool per name and a fired-calls tracker."""
    registry = ToolRegistry()
    calls: dict[str, list[object]] = {n: [] for n in names}
    for name in names:
        n = name  # capture for closure
        registry.register(
            ToolEntry(
                name=n,
                phrases=(n,),
                func=lambda _n=n: calls[_n].append(1),
                module="test",
                docstring=None,
            )
        )
    return registry, calls


def _make_daemon(
    registry: ToolRegistry,
    llm_router: object | None = None,
    min_confidence: float = 0.30,
) -> tuple[StreamingDaemon, CapturingFeedbackSink, MagicMock]:
    """Build a StreamingDaemon with a mocked transcriber for unit-level testing.

    Returns (daemon, feedback_sink, mock_transcriber).
    The output_dir is set to a valid temp location via tmp_path; callers must
    patch _write_utterance_async to avoid filesystem side-effects.
    """
    feedback = CapturingFeedbackSink()
    matcher = Matcher(registry, threshold=85.0)
    dispatcher = Dispatcher(feedback)
    transcriber = MagicMock()
    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=None,
        transcriber=transcriber,
        matcher=matcher,
        dispatcher=dispatcher,
        registry=registry,
        llm_router=llm_router,
        min_confidence=min_confidence,
        output_dir="outputs",
    )
    return daemon, feedback, transcriber


def _run(daemon: StreamingDaemon, result: TranscriptionResult) -> None:
    """Drive _process_utterance with a synthetic utterance, suppressing WAV I/O."""
    utterance = np.zeros(16_000, dtype=np.float32)
    daemon._transcriber.transcribe.return_value = result
    with patch.object(daemon, "_write_utterance_async"):
        daemon._process_utterance(utterance)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestHybridPipeline:
    """Routing logic coverage for StreamingDaemon._process_utterance."""

    # ------------------------------------------------------------------
    # Hot path
    # ------------------------------------------------------------------

    def test_hot_path_direct_dispatch(self) -> None:
        """High-confidence fuzzy match → tool fires, LLM never consulted."""
        registry, calls = _make_registry("copy")
        llm = MagicMock()
        daemon, feedback, _ = _make_daemon(registry, llm_router=llm)

        _run(daemon, _make_result("copy"))

        assert calls["copy"] == [1], "Tool should have been called once"
        llm.route.assert_not_called()

    def test_hot_path_on_match_event_emitted(self) -> None:
        """Hot-path dispatch must emit on_match feedback."""
        registry, _ = _make_registry("copy")
        daemon, feedback, _ = _make_daemon(registry)

        _run(daemon, _make_result("copy"))

        event_names = [name for name, _ in feedback.calls]
        assert "on_match" in event_names

    def test_hot_path_does_not_emit_miss(self) -> None:
        """Successful hot-path match must not emit on_miss."""
        registry, _ = _make_registry("copy")
        daemon, feedback, _ = _make_daemon(registry)

        _run(daemon, _make_result("copy"))

        event_names = [name for name, _ in feedback.calls]
        assert "on_miss" not in event_names

    # ------------------------------------------------------------------
    # LLM escalation path
    # ------------------------------------------------------------------

    def test_llm_escalation_on_no_fuzzy_match(self) -> None:
        """Below-threshold transcript + LLM router present → LLM is consulted."""
        registry, calls = _make_registry("copy")
        plan = Plan(steps=(ToolCall(name="copy", kwargs={}),), raw_response={})
        llm = MagicMock()
        llm.route.return_value = plan
        daemon, _, _ = _make_daemon(registry, llm_router=llm)

        _run(daemon, _make_result("something completely unrelated xyz"))

        llm.route.assert_called_once()

    def test_llm_escalation_runs_plan(self) -> None:
        """LLM returns a valid plan → the tool in the plan executes."""
        registry, calls = _make_registry("copy")
        plan = Plan(steps=(ToolCall(name="copy", kwargs={}),), raw_response={})
        llm = MagicMock()
        llm.route.return_value = plan
        daemon, feedback, _ = _make_daemon(registry, llm_router=llm)

        _run(daemon, _make_result("something completely unrelated xyz"))

        assert calls["copy"] == [1], "Plan step should have executed the tool"
        event_names = [name for name, _ in feedback.calls]
        assert "on_plan_start" in event_names
        assert "on_plan_complete" in event_names

    def test_llm_escalation_none_triggers_miss(self) -> None:
        """LLM returns None (cannot route) → on_miss feedback fires."""
        registry, _ = _make_registry("copy")
        llm = MagicMock()
        llm.route.return_value = None
        daemon, feedback, _ = _make_daemon(registry, llm_router=llm)

        _run(daemon, _make_result("random gibberish xyz"))

        event_names = [name for name, _ in feedback.calls]
        assert "on_miss" in event_names

    def test_llm_escalation_none_does_not_run_plan(self) -> None:
        """LLM returns None → no plan events emitted."""
        registry, calls = _make_registry("copy")
        llm = MagicMock()
        llm.route.return_value = None
        daemon, feedback, _ = _make_daemon(registry, llm_router=llm)

        _run(daemon, _make_result("random gibberish xyz"))

        event_names = [name for name, _ in feedback.calls]
        assert "on_plan_start" not in event_names
        assert calls["copy"] == []

    # ------------------------------------------------------------------
    # Fallback path (no LLM router)
    # ------------------------------------------------------------------

    def test_fallback_miss_when_no_llm_router(self) -> None:
        """No fuzzy match + no LLM router → dispatcher.dispatch() reports miss."""
        registry, calls = _make_registry("copy")
        daemon, feedback, _ = _make_daemon(registry, llm_router=None)

        _run(daemon, _make_result("something completely unrelated xyz"))

        event_names = [name for name, _ in feedback.calls]
        assert "on_miss" in event_names
        assert calls["copy"] == []

    def test_fallback_does_not_invoke_llm(self) -> None:
        """No LLM router configured → no llm.route() call possible (no-op guard)."""
        registry, _ = _make_registry("copy")
        # No llm_router — just verify no AttributeError and miss is reported.
        daemon, feedback, _ = _make_daemon(registry, llm_router=None)

        _run(daemon, _make_result("xyz abc def"))

        event_names = [name for name, _ in feedback.calls]
        assert "on_miss" in event_names

    # ------------------------------------------------------------------
    # Gate: low confidence drops before routing
    # ------------------------------------------------------------------

    def test_low_confidence_skips_routing(self) -> None:
        """Transcription confidence below threshold → on_miss, no tool fire."""
        registry, calls = _make_registry("copy")
        llm = MagicMock()
        daemon, feedback, _ = _make_daemon(registry, llm_router=llm, min_confidence=0.30)

        _run(daemon, _make_result("copy", confidence=0.10))

        # Low confidence fires on_miss before reaching the matcher
        event_names = [name for name, _ in feedback.calls]
        assert "on_miss" in event_names
        llm.route.assert_not_called()
        assert calls["copy"] == []

    # ------------------------------------------------------------------
    # Multi-step plan
    # ------------------------------------------------------------------

    def test_llm_multi_step_plan_executes_all_steps(self) -> None:
        """A plan with two steps fires both tools in order."""
        registry, calls = _make_registry("copy", "paste")
        plan = Plan(
            steps=(
                ToolCall(name="copy", kwargs={}),
                ToolCall(name="paste", kwargs={}),
            ),
            raw_response={},
        )
        llm = MagicMock()
        llm.route.return_value = plan
        daemon, feedback, _ = _make_daemon(registry, llm_router=llm)

        _run(daemon, _make_result("execute the multi command sequence now"))

        # Both tools should fire
        assert calls["copy"] == [1]
        assert calls["paste"] == [1]

    def test_llm_plan_complete_reports_executed_count(self) -> None:
        """on_plan_complete receives the correct executed-step count."""
        registry, _ = _make_registry("copy")
        plan = Plan(steps=(ToolCall(name="copy", kwargs={}),), raw_response={})
        llm = MagicMock()
        llm.route.return_value = plan
        daemon, feedback, _ = _make_daemon(registry, llm_router=llm)

        _run(daemon, _make_result("something unrelated xyz"))

        plan_complete_calls = [args for name, args in feedback.calls if name == "on_plan_complete"]
        assert plan_complete_calls, "on_plan_complete must be emitted"
        _transcript, steps_executed = plan_complete_calls[0]
        assert steps_executed == 1
