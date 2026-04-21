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
