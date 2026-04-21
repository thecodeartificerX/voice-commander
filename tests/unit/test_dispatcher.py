import logging
from unittest.mock import patch

from voice_commander.dispatcher import Dispatcher
from voice_commander.feedback import CapturingFeedbackSink
from voice_commander.plan import Plan, ToolCall
from voice_commander.registry import ToolEntry, ToolRegistry


def _make_registry(*entries: ToolEntry) -> ToolRegistry:
    reg = ToolRegistry()
    for entry in entries:
        reg.register(entry)
    return reg


def test_run_plan_happy_path_invokes_tool():
    """A plan with a known tool should invoke it and fire on_plan_complete."""
    fired: list[int] = []
    entry = ToolEntry("copy", ("copy",), lambda: fired.append(1), "m", None)
    registry = _make_registry(entry)
    sink = CapturingFeedbackSink()
    d = Dispatcher(feedback=sink)
    plan = Plan(steps=(ToolCall(name="copy", kwargs={}),), raw_response={})

    d.run_plan("copy please", plan, registry)

    assert fired == [1], "Tool function was not called"
    assert any(c[0] == "on_plan_start" for c in sink.calls), "on_plan_start not fired"
    assert any(c[0] == "on_plan_complete" for c in sink.calls), "on_plan_complete not fired"
    # Verify steps_executed == 1
    complete_call = next(c for c in sink.calls if c[0] == "on_plan_complete")
    assert complete_call[1][1] == 1, f"Expected 1 step executed, got {complete_call[1][1]}"


def test_run_plan_unknown_tool_fires_error():
    """A plan referencing a tool not in the registry fires on_error and stops."""
    registry = _make_registry()  # empty
    sink = CapturingFeedbackSink()
    d = Dispatcher(feedback=sink)
    plan = Plan(steps=(ToolCall(name="ghost", kwargs={}),), raw_response={})

    d.run_plan("ghost command", plan, registry)

    assert any(c[0] == "on_error" for c in sink.calls), "on_error not fired for unknown tool"
    # Steps executed must be 0 — the bad tool aborted before executing anything.
    complete_call = next(c for c in sink.calls if c[0] == "on_plan_complete")
    assert complete_call[1][1] == 0, f"Expected 0 steps executed, got {complete_call[1][1]}"


def test_run_plan_tool_error_fires_error_and_does_not_raise():
    """A tool that raises must fire on_error and must not propagate the exception."""
    def boom() -> None:
        raise RuntimeError("tool exploded")

    entry = ToolEntry("bang", ("bang",), boom, "m", None)
    registry = _make_registry(entry)
    sink = CapturingFeedbackSink()
    d = Dispatcher(feedback=sink)
    plan = Plan(steps=(ToolCall(name="bang", kwargs={}),), raw_response={})

    d.run_plan("bang", plan, registry)  # must not raise

    assert any(c[0] == "on_error" for c in sink.calls), "on_error not fired when tool raises"
    complete_call = next(c for c in sink.calls if c[0] == "on_plan_complete")
    assert complete_call[1][1] == 0, (
        f"Expected 0 steps executed after error, got {complete_call[1][1]}"
    )


def test_run_plan_sleeps_settle_ms():
    """A tool with settle_ms=250 triggers exactly one time.sleep(0.25)."""
    fired: list[int] = []
    entry = ToolEntry(
        name="slow_tool",
        phrases=("slow",),
        func=lambda: fired.append(1),
        module="m",
        docstring=None,
        settle_ms=250,
    )
    registry = _make_registry(entry)
    sink = CapturingFeedbackSink()
    d = Dispatcher(feedback=sink)
    plan = Plan(steps=(ToolCall(name="slow_tool", kwargs={}),), raw_response={})

    with patch("voice_commander.dispatcher.time.sleep") as mock_sleep:
        d.run_plan("slow please", plan, registry)

    assert fired == [1], "Tool function was not called"
    assert mock_sleep.call_count == 1, (
        f"Expected time.sleep to be called exactly once, got {mock_sleep.call_count}"
    )
    mock_sleep.assert_called_once_with(0.25)


def test_run_plan_halts_on_mid_chain_error():
    """Step 2 raises RuntimeError → step 3 is not executed; on_error fires once
    for step index 1 (the second step), on_plan_complete fires with executed=1."""
    log: list[str] = []

    def boom() -> None:
        raise RuntimeError("step 2 exploded")

    entry_a = ToolEntry("first", (), lambda: log.append("first"), "m", None)
    entry_b = ToolEntry("second", (), boom, "m", None)
    entry_c = ToolEntry("third", (), lambda: log.append("third"), "m", None)
    registry = _make_registry(entry_a, entry_b, entry_c)
    sink = CapturingFeedbackSink()
    d = Dispatcher(feedback=sink)
    plan = Plan(
        steps=(
            ToolCall(name="first", kwargs={}),
            ToolCall(name="second", kwargs={}),
            ToolCall(name="third", kwargs={}),
        ),
        raw_response={},
    )

    # Must not raise — dispatcher catches exceptions from tool funcs.
    d.run_plan("three step chain", plan, registry)

    # (a) first tool executed, (c) third tool did NOT execute
    assert log == ["first"], f"Expected only 'first' to execute, got {log}"

    # (d) on_error fired exactly once, identifying the offending step by name
    error_calls = [c for c in sink.calls if c[0] == "on_error"]
    assert len(error_calls) == 1, (
        f"Expected exactly one on_error call, got {len(error_calls)}"
    )
    subsystem, exc = error_calls[0][1]
    # Dispatcher tags errors as "plan:step:<tool_name>". The second step's tool
    # is named "second" → subsystem reflects step index 1 (the second step).
    assert subsystem == "plan:step:second", (
        f"Expected on_error subsystem to point to step 1 ('second'), got {subsystem!r}"
    )
    # (b) the second tool was actually invoked (and raised)
    assert isinstance(exc, RuntimeError)
    assert "step 2 exploded" in str(exc)

    # (e) on_plan_complete fired with executed=1
    complete_calls = [c for c in sink.calls if c[0] == "on_plan_complete"]
    assert len(complete_calls) == 1, "on_plan_complete should fire exactly once"
    transcript, steps_executed = complete_calls[0][1]
    assert transcript == "three step chain"
    assert steps_executed == 1, (
        f"Expected steps_executed == 1 (only 'first' succeeded), got {steps_executed}"
    )


def test_run_plan_multi_step_all_succeed():
    """A multi-step plan executes all steps in order."""
    log: list[str] = []
    entry_a = ToolEntry("alpha", (), lambda: log.append("alpha"), "m", None)
    entry_b = ToolEntry("beta", (), lambda: log.append("beta"), "m", None)
    registry = _make_registry(entry_a, entry_b)
    sink = CapturingFeedbackSink()
    d = Dispatcher(feedback=sink)
    plan = Plan(
        steps=(
            ToolCall(name="alpha", kwargs={}),
            ToolCall(name="beta", kwargs={}),
        ),
        raw_response={},
    )

    d.run_plan("alpha then beta", plan, registry)

    assert log == ["alpha", "beta"], f"Expected ordered execution, got {log}"
    complete_call = next(c for c in sink.calls if c[0] == "on_plan_complete")
    assert complete_call[1][1] == 2, f"Expected 2 steps executed, got {complete_call[1][1]}"


def test_run_plan_logs_per_step(caplog):
    """Dispatcher emits an INFO line per step like 'plan step 1/3: focus(target=...)'."""
    log: list[str] = []
    entry_f = ToolEntry(
        "focus", (), lambda target=None: log.append(f"focus:{target}"), "m", None,
    )
    entry_p = ToolEntry(
        "press", (), lambda combo=None: log.append(f"press:{combo}"), "m", None,
    )
    entry_t = ToolEntry(
        "type", (), lambda text=None: log.append(f"type:{text}"), "m", None,
    )
    registry = _make_registry(entry_f, entry_p, entry_t)
    sink = CapturingFeedbackSink()
    d = Dispatcher(feedback=sink)
    plan = Plan(
        steps=(
            ToolCall(name="focus", kwargs={"target": "notepad"}),
            ToolCall(name="press", kwargs={"combo": "ctrl+a"}),
            ToolCall(name="type", kwargs={"text": "hi"}),
        ),
        raw_response={},
    )

    with caplog.at_level(logging.INFO, logger="voice_commander.dispatcher"):
        d.run_plan("chain", plan, registry)

    text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "plan step 1/3: focus" in text, f"Missing step-1 log line; got:\n{text}"
    assert "plan step 2/3: press" in text, f"Missing step-2 log line; got:\n{text}"
    assert "plan step 3/3: type" in text, f"Missing step-3 log line; got:\n{text}"
