"""Typed-data edge propagates focus hwnd to downstream node."""

from voice_commander.commands.graph import Edge, Graph, Node, PortRef
from voice_commander.commands.graph_runtime import GraphRuntime
from voice_commander.registry import ToolEntry, ToolRegistry


def test_typed_output_flows_to_downstream_kwarg():
    seen: list[dict] = []

    def _focus(target: str):
        return 0xCAFE

    def _consume(hwnd: int):
        seen.append({"hwnd": hwnd})

    reg = ToolRegistry()
    reg.register(
        ToolEntry(
            name="focus",
            phrases=(),
            func=_focus,
            module="x",
            docstring=None,
            internal=True,
            returns_meta={"hwnd": {"type": "integer", "description": "x"}},
        )
    )
    reg.register(
        ToolEntry(
            name="consume_hwnd",
            phrases=(),
            func=_consume,
            module="x",
            docstring=None,
            internal=True,
        )
    )

    g = Graph(
        name="test",
        kind="command",
        description="",
        synonyms=(),
        inputs=(),
        llm_visible=False,
        strict=True,
        enabled=True,
        timeout_ms=5000,
        nodes=(
            Node(id="a", ref="pipeline.focus", kwargs={"target": "x"}),
            Node(id="b", ref="pipeline.consume_hwnd", kwargs={}),
        ),
        edges=(
            Edge(PortRef("a", "ok"), PortRef("b", "in")),
            Edge(PortRef("a", "hwnd"), PortRef("b", "hwnd")),
        ),
        foreach_iteration_cap=50,
    )

    runtime = GraphRuntime(registry=reg, graph_lookup=lambda n: None)
    outcome, _ret = runtime.run(g, inputs={})
    assert outcome.status == "ok"
    assert seen == [{"hwnd": 0xCAFE}]


def test_multi_port_returns_unpacked():
    """A tool with 2 returns_meta ports receives a tuple and both get parked."""
    results: dict = {}

    def _cursor_pos():
        return (100, 200)

    def _consume(x: int, y: int):
        results["x"] = x
        results["y"] = y

    reg = ToolRegistry()
    reg.register(
        ToolEntry(
            name="cursor_pos",
            phrases=(),
            func=_cursor_pos,
            module="x",
            docstring=None,
            internal=True,
            returns_meta={
                "x": {"type": "integer", "description": ""},
                "y": {"type": "integer", "description": ""},
            },
        )
    )
    reg.register(
        ToolEntry(
            name="consume",
            phrases=(),
            func=_consume,
            module="x",
            docstring=None,
            internal=True,
        )
    )

    g = Graph(
        name="test",
        kind="command",
        description="",
        synonyms=(),
        inputs=(),
        llm_visible=False,
        strict=True,
        enabled=True,
        timeout_ms=5000,
        nodes=(
            Node(id="c", ref="pipeline.cursor_pos", kwargs={}),
            Node(id="d", ref="pipeline.consume", kwargs={}),
        ),
        edges=(
            Edge(PortRef("c", "ok"), PortRef("d", "in")),
            Edge(PortRef("c", "x"), PortRef("d", "x")),
            Edge(PortRef("c", "y"), PortRef("d", "y")),
        ),
        foreach_iteration_cap=50,
    )

    runtime = GraphRuntime(registry=reg, graph_lookup=lambda n: None)
    outcome, _ = runtime.run(g, inputs={})
    assert outcome.status == "ok"
    assert results == {"x": 100, "y": 200}
