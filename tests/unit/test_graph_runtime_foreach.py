from voice_commander.commands.graph import Edge, Graph, GraphInput, Node, PortRef
from voice_commander.commands.graph_runtime import GraphRuntime
from voice_commander.registry import ToolEntry, ToolRegistry


def test_foreach_iterates_body():
    recorded: list = []

    def _record(item):
        recorded.append(item)

    reg = ToolRegistry()
    reg.register(ToolEntry(name="record", phrases=(), func=_record, module="x", docstring=None, internal=True))

    g = Graph(
        name="test", kind="command", description="", synonyms=(), inputs=(),
        llm_visible=False, strict=True, enabled=True, timeout_ms=5000,
        nodes=(
            Node(id="f1", ref="control.foreach", kwargs={"list": ["a", "b", "c"]}),
            Node(id="r1", ref="pipeline.record", kwargs={}),
        ),
        edges=(
            Edge(PortRef("f1", "item"), PortRef("r1", "in")),
            Edge(PortRef("f1", "item"), PortRef("r1", "item")),
        ),
        foreach_iteration_cap=50,
    )

    runtime = GraphRuntime(registry=reg, graph_lookup=lambda n: None)
    outcome, _ = runtime.run(g, {})
    assert outcome.status == "ok"
    assert recorded == ["a", "b", "c"]


def test_foreach_respects_cap():
    recorded: list = []

    def _record(item):
        recorded.append(item)

    reg = ToolRegistry()
    reg.register(ToolEntry(name="record", phrases=(), func=_record, module="x", docstring=None, internal=True))

    g = Graph(
        name="test", kind="command", description="", synonyms=(), inputs=(),
        llm_visible=False, strict=True, enabled=True, timeout_ms=5000,
        nodes=(
            Node(id="f1", ref="control.foreach", kwargs={"list": ["a", "b", "c", "d"]}),
            Node(id="r1", ref="pipeline.record", kwargs={}),
        ),
        edges=(
            Edge(PortRef("f1", "item"), PortRef("r1", "in")),
            Edge(PortRef("f1", "item"), PortRef("r1", "item")),
        ),
        foreach_iteration_cap=2,
    )

    runtime = GraphRuntime(registry=reg, graph_lookup=lambda n: None)
    outcome, _ = runtime.run(g, {})
    assert outcome.status == "ok"
    assert recorded == ["a", "b"]
