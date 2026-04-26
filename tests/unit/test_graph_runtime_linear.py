from __future__ import annotations

from typing import Any

from voice_commander.commands.graph import Edge, Graph, Node, PortRef
from voice_commander.commands.graph_runtime import GraphRuntime
from voice_commander.registry import ToolEntry, ToolRegistry


def _make_registry(calls: list[tuple[str, dict[str, Any]]]) -> ToolRegistry:
    reg = ToolRegistry()
    for name in ("focus", "press", "type"):
        def _fn(_name=name, **kwargs):
            calls.append((_name, kwargs))
            return None
        reg.register(
            ToolEntry(
                name=name,
                phrases=(),
                func=_fn,
                module="x",
                docstring=None,
                internal=True,
            )
        )
    return reg


def test_linear_graph_runs_in_order():
    calls: list[tuple[str, dict[str, Any]]] = []
    reg = _make_registry(calls)

    g = Graph(
        name="test", kind="command", description="", synonyms=(), inputs=(),
        llm_visible=False, strict=True, enabled=True, timeout_ms=5000,
        nodes=(
            Node(id="n1", ref="pipeline.focus", kwargs={"target": "x"}),
            Node(id="n2", ref="pipeline.press", kwargs={"combo": "ctrl+t"}),
            Node(id="n3", ref="pipeline.type",  kwargs={"text": "hi"}),
        ),
        edges=(
            Edge(PortRef("n1", "ok"), PortRef("n2", "in")),
            Edge(PortRef("n2", "ok"), PortRef("n3", "in")),
        ),
        foreach_iteration_cap=50,
    )

    runtime = GraphRuntime(registry=reg, graph_lookup=lambda name: None)
    outcome, ret = runtime.run(g, inputs={})

    assert outcome.status == "ok"
    assert ret is None
    assert [c[0] for c in calls] == ["focus", "press", "type"]
    assert calls[0][1] == {"target": "x"}
    assert calls[1][1] == {"combo": "ctrl+t"}
