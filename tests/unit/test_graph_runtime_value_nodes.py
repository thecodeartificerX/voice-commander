from voice_commander.commands.graph import Edge, Graph, GraphInput, Node, PortRef
from voice_commander.commands.graph_runtime import GraphRuntime
from voice_commander.registry import ToolEntry, ToolRegistry


def test_value_constant_flows_to_downstream():
    received: list = []

    def _consume(value):
        received.append(value)

    reg = ToolRegistry()
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
            Node(id="c1", ref="value.constant", kwargs={"value": 42}),
            Node(id="n1", ref="pipeline.consume", kwargs={}),
        ),
        edges=(Edge(PortRef("c1", "value"), PortRef("n1", "value")),),
        foreach_iteration_cap=50,
    )

    runtime = GraphRuntime(registry=reg, graph_lookup=lambda n: None)
    outcome, _ = runtime.run(g, {})
    assert outcome.status == "ok"
    assert received == [42]


def test_value_input_flows_graph_input_to_downstream():
    received: list = []

    def _consume(query):
        received.append(query)

    reg = ToolRegistry()
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
        inputs=(GraphInput(name="query", type="str", required=True, description=""),),
        llm_visible=False,
        strict=True,
        enabled=True,
        timeout_ms=5000,
        nodes=(
            Node(id="in", ref="value.input", kwargs={}),
            Node(id="n1", ref="pipeline.consume", kwargs={}),
        ),
        edges=(Edge(PortRef("in", "query"), PortRef("n1", "query")),),
        foreach_iteration_cap=50,
    )

    runtime = GraphRuntime(registry=reg, graph_lookup=lambda n: None)
    outcome, _ = runtime.run(g, inputs={"query": "cats"})
    assert outcome.status == "ok"
    assert received == ["cats"]


def test_value_output_sets_graph_return():
    reg = ToolRegistry()

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
            Node(id="c1", ref="value.constant", kwargs={"value": "hello"}),
            Node(id="out", ref="value.output", kwargs={}),
        ),
        edges=(Edge(PortRef("c1", "value"), PortRef("out", "value")),),
        foreach_iteration_cap=50,
    )

    runtime = GraphRuntime(registry=reg, graph_lookup=lambda n: None)
    outcome, ret = runtime.run(g, {})
    assert outcome.status == "ok"
    assert ret == "hello"
