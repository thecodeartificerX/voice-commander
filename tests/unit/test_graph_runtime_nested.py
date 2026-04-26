from voice_commander.commands.graph import Edge, Graph, Node, PortRef
from voice_commander.commands.graph_runtime import GraphRuntime
from voice_commander.registry import ToolEntry, ToolRegistry


def test_cross_graph_nested_invocation():
    fired: list[str] = []

    def _press(combo: str):
        fired.append(combo)

    reg = ToolRegistry()
    reg.register(ToolEntry(
        name="press", phrases=(), func=_press, module="x", docstring=None, internal=True,
    ))

    child = Graph(
        name="helper", kind="command", description="", synonyms=(), inputs=(),
        llm_visible=False, strict=True, enabled=True, timeout_ms=5000,
        nodes=(Node(id="n1", ref="pipeline.press", kwargs={"combo": "ctrl+a"}),),
        edges=(),
        foreach_iteration_cap=50,
    )

    parent = Graph(
        name="parent", kind="command", description="", synonyms=(), inputs=(),
        llm_visible=False, strict=True, enabled=True, timeout_ms=5000,
        nodes=(
            Node(id="c1", ref="command.helper", kwargs={}),
            Node(id="n2", ref="pipeline.press", kwargs={"combo": "ctrl+c"}),
        ),
        edges=(
            Edge(PortRef("c1", "ok"), PortRef("n2", "in")),
        ),
        foreach_iteration_cap=50,
    )

    def lookup(name: str):
        return child if name == "helper" else None
    runtime = GraphRuntime(registry=reg, graph_lookup=lookup)
    outcome, _ = runtime.run(parent, {})
    assert outcome.status == "ok"
    assert fired == ["ctrl+a", "ctrl+c"]
