"""Tests for Dispatcher.run_plan() — multi-step LLM plan execution."""
from __future__ import annotations

from unittest.mock import patch

from voice_commander.dispatcher import Dispatcher
from voice_commander.feedback import CapturingFeedbackSink
from voice_commander.plan import Plan, ToolCall
from voice_commander.registry import ToolEntry, ToolRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_registry(*tools: ToolEntry) -> ToolRegistry:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    return reg


def _entry(name: str, func=None, settle_ms: int = 0) -> ToolEntry:
    return ToolEntry(
        name=name,
        phrases=(),
        func=func or (lambda: None),
        module="test",
        docstring=None,
        settle_ms=settle_ms,
    )


def _plan(*steps: tuple[str, dict]) -> Plan:
    """Build a Plan from (name, kwargs) pairs."""
    return Plan(
        steps=tuple(ToolCall(name=n, kwargs=kw) for n, kw in steps),
        raw_response={},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_run_plan_happy_path():
    """All three steps execute; sleep is called once per step with settle_ms > 0."""
    call_log: list[str] = []

    reg = _make_registry(
        _entry("step_a", func=lambda: call_log.append("a"), settle_ms=50),
        _entry("step_b", func=lambda: call_log.append("b"), settle_ms=100),
        _entry("step_c", func=lambda: call_log.append("c"), settle_ms=25),
    )
    sink = CapturingFeedbackSink()
    d = Dispatcher(feedback=sink)
    plan = _plan(("step_a", {}), ("step_b", {}), ("step_c", {}))

    with patch("voice_commander.dispatcher.time.sleep") as mock_sleep:
        d.run_plan("do three things", plan, reg)

    assert call_log == ["a", "b", "c"]
    # sleep called once per step (all have settle_ms > 0)
    assert mock_sleep.call_count == 3
    mock_sleep.assert_any_call(0.05)   # 50 ms
    mock_sleep.assert_any_call(0.1)    # 100 ms
    mock_sleep.assert_any_call(0.025)  # 25 ms


def test_run_plan_mid_chain_error():
    """When step 2 raises, step 3 never executes and on_error is fired."""
    call_log: list[str] = []

    def boom():
        raise RuntimeError("step_b failed")

    reg = _make_registry(
        _entry("step_a", func=lambda: call_log.append("a")),
        _entry("step_b", func=boom),
        _entry("step_c", func=lambda: call_log.append("c")),
    )
    sink = CapturingFeedbackSink()
    d = Dispatcher(feedback=sink)
    plan = _plan(("step_a", {}), ("step_b", {}), ("step_c", {}))

    d.run_plan("do three things", plan, reg)

    # Only step_a ran
    assert call_log == ["a"]
    # step_c must NOT have run
    assert "c" not in call_log

    # on_error fired with the right subsystem key
    error_calls = [c for c in sink.calls if c[0] == "on_error"]
    assert len(error_calls) == 1
    subsystem, exc = error_calls[0][1]
    assert subsystem == "plan:step:step_b"
    assert isinstance(exc, RuntimeError)

    # plan still completes (on_plan_complete fired, executed == 1)
    complete_calls = [c for c in sink.calls if c[0] == "on_plan_complete"]
    assert len(complete_calls) == 1
    _transcript, steps_executed = complete_calls[0][1]
    assert steps_executed == 1


def test_run_plan_unknown_tool():
    """A step referencing a non-existent tool fires on_error and stops the chain."""
    call_log: list[str] = []

    reg = _make_registry(
        _entry("step_a", func=lambda: call_log.append("a")),
        # "nonexistent" is intentionally absent from the registry
        _entry("step_c", func=lambda: call_log.append("c")),
    )
    sink = CapturingFeedbackSink()
    d = Dispatcher(feedback=sink)
    plan = _plan(("step_a", {}), ("nonexistent", {}), ("step_c", {}))

    d.run_plan("trigger unknown", plan, reg)

    # step_a ran, step_c did not
    assert call_log == ["a"]

    error_calls = [c for c in sink.calls if c[0] == "on_error"]
    assert len(error_calls) == 1
    subsystem, exc = error_calls[0][1]
    assert subsystem == "plan:unknown_tool:nonexistent"
    assert isinstance(exc, ValueError)
    assert "nonexistent" in str(exc)

    # Chain stopped; executed == 1
    complete_calls = [c for c in sink.calls if c[0] == "on_plan_complete"]
    assert complete_calls[0][1][1] == 1


def test_run_plan_zero_settle():
    """When settle_ms == 0, time.sleep must never be called."""
    reg = _make_registry(
        _entry("step_a", settle_ms=0),
        _entry("step_b", settle_ms=0),
    )
    sink = CapturingFeedbackSink()
    d = Dispatcher(feedback=sink)
    plan = _plan(("step_a", {}), ("step_b", {}))

    with patch("voice_commander.dispatcher.time.sleep") as mock_sleep:
        d.run_plan("no settle", plan, reg)

    mock_sleep.assert_not_called()


def test_run_plan_feedback_events():
    """on_plan_start fires first with correct args; on_plan_complete fires last."""
    reg = _make_registry(
        _entry("step_a"),
        _entry("step_b"),
    )
    sink = CapturingFeedbackSink()
    d = Dispatcher(feedback=sink)
    transcript = "execute two steps"
    plan = _plan(("step_a", {}), ("step_b", {}))

    d.run_plan(transcript, plan, reg)

    event_names = [c[0] for c in sink.calls]

    # on_plan_start must be the very first event
    assert event_names[0] == "on_plan_start"
    # on_plan_complete must be the very last event
    assert event_names[-1] == "on_plan_complete"

    # on_plan_start args: (transcript, step_count)
    start_args = sink.calls[0][1]
    assert start_args == (transcript, 2)

    # on_plan_complete args: (transcript, steps_executed)
    complete_args = sink.calls[-1][1]
    assert complete_args == (transcript, 2)
