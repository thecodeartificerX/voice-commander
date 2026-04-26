"""Integration test: Graph stored → registered → dispatched → GraphRuntime executes nodes.

Chain under test:
  GraphStore.save_one(graph)
    → register_graphs(registry, store)
      → ToolEntry added to registry
        → Dispatcher.run_plan(plan, registry)
          → ToolEntry.func(**kwargs)
            → GraphRuntime.run(graph, inputs)
              → pipeline tool called for each node

No CUDA / audio / LM Studio required.  All pyautogui / Win32 side-effects are
monkeypatched so the test runs on any machine.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from voice_commander.commands.graph import Edge, Graph, Node, PortRef
from voice_commander.commands.registrar import register_graphs
from voice_commander.commands.store import GraphStore
from voice_commander.dispatcher import Dispatcher
from voice_commander.feedback import NullFeedbackSink
from voice_commander.plan import Plan, ToolCall
from voice_commander.registry import ToolEntry, ToolRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_press_entry(spy: MagicMock) -> ToolEntry:
    """A primitive 'press' ToolEntry whose func records calls to *spy*."""
    return ToolEntry(
        name="press",
        phrases=(),
        func=spy,
        module="test",
        docstring=None,
        enabled=True,
        llm_only=False,
        internal=True,
        origin="primitive",
    )


def _two_node_graph() -> Graph:
    """Graph: press(ctrl+t) → press(enter), chained via ok→in control edge."""
    return Graph(
        name="open_new_tab",
        kind="command",
        description="Open a new tab then confirm",
        synonyms=("open new tab",),
        inputs=(),
        llm_visible=True,
        strict=True,
        enabled=True,
        timeout_ms=5000,
        foreach_iteration_cap=50,
        nodes=(
            Node("n1", "pipeline.press", {"combo": "ctrl+t"}),
            Node("n2", "pipeline.press", {"combo": "enter"}),
        ),
        edges=(
            Edge(
                src=PortRef(node_id="n1", port="ok"),
                dst=PortRef(node_id="n2", port="in"),
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_graph_dispatched_through_runtime(tmp_path: Path) -> None:
    """Full pipeline: save → register_graphs → run_plan → both press calls fired."""
    # 1. Seed the GraphStore
    store = GraphStore(tmp_path / "commands.json", kind="command")
    graph = _two_node_graph()
    store.save_one(graph)

    # 2. Build a registry with a mock 'press' primitive
    registry = ToolRegistry()
    press_spy = MagicMock(name="press_func", return_value=None)
    registry.register(_mock_press_entry(press_spy))

    # 3. register_graphs synthesises a ToolEntry for 'open_new_tab' in the registry
    names = register_graphs(registry, store)
    assert "open_new_tab" in names
    entry = registry.by_name("open_new_tab")
    assert entry is not None, "register_graphs did not add 'open_new_tab' to registry"
    assert entry.origin == "command"

    # 4. Build a Dispatcher (no EventBus needed for this test)
    dispatcher = Dispatcher(feedback=NullFeedbackSink())

    # 5. Build a Plan referencing the graph by name (no kwargs — graph has no inputs)
    plan = Plan(
        steps=(ToolCall(name="open_new_tab", kwargs={}),),
        raw_response={},
    )

    # 6. Dispatch — GraphRuntime.run is called under the hood
    outcome = dispatcher.run_plan("test-session", plan, registry)

    # 7. Verify outcome
    assert outcome.status == "ok", f"Expected ok but got: {outcome.error_msg}"

    # 8. Verify the press primitive was called twice (n1 then n2)
    assert press_spy.call_count == 2, (
        f"Expected press to be called twice; got {press_spy.call_count} calls"
    )

    call_combos = [c.kwargs.get("combo") for c in press_spy.call_args_list]
    assert call_combos == ["ctrl+t", "enter"], (
        f"Expected combos ['ctrl+t', 'enter']; got {call_combos}"
    )


@pytest.mark.integration
def test_graph_runtime_error_propagates_to_plan_outcome(tmp_path: Path) -> None:
    """When GraphRuntime.run encounters an error the ToolEntry raises RuntimeError.

    Dispatcher records the failure in PlanOutcome (status='error').
    """
    store = GraphStore(tmp_path / "commands.json", kind="command")
    graph = _two_node_graph()
    store.save_one(graph)

    registry = ToolRegistry()
    # Register a 'press' that explodes on the first call
    broken_press = MagicMock(side_effect=RuntimeError("pyautogui unavailable"))
    registry.register(_mock_press_entry(broken_press))

    register_graphs(registry, store)

    dispatcher = Dispatcher(feedback=NullFeedbackSink())
    plan = Plan(steps=(ToolCall(name="open_new_tab", kwargs={}),), raw_response={})

    outcome = dispatcher.run_plan("err-session", plan, registry)

    # The graph runs strict=True so it stops at the first failing node
    assert outcome.status == "error"
    assert outcome.failed_step_index == 0


@pytest.mark.integration
def test_disabled_graph_not_registered(tmp_path: Path) -> None:
    """A graph with enabled=False must not appear in the registry after register_graphs."""
    from dataclasses import replace

    store = GraphStore(tmp_path / "commands.json", kind="command")
    graph = replace(_two_node_graph(), enabled=False)
    store.save_one(graph)

    registry = ToolRegistry()
    press_spy = MagicMock(return_value=None)
    registry.register(_mock_press_entry(press_spy))

    names = register_graphs(registry, store)
    assert "open_new_tab" not in names
    assert registry.by_name("open_new_tab") is None


@pytest.mark.integration
def test_register_graphs_idempotent(tmp_path: Path) -> None:
    """Calling register_graphs twice drops the first registration before re-adding."""
    store = GraphStore(tmp_path / "commands.json", kind="command")
    store.save_one(_two_node_graph())

    registry = ToolRegistry()
    registry.register(_mock_press_entry(MagicMock(return_value=None)))

    register_graphs(registry, store)
    # A second call must not raise DuplicateToolError
    register_graphs(registry, store)

    assert registry.by_name("open_new_tab") is not None
