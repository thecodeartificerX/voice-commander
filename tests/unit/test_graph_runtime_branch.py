from voice_commander.commands.graph import Edge, Graph, Node, PortRef
from voice_commander.commands.graph_runtime import GraphRuntime
from voice_commander.registry import ToolEntry, ToolRegistry


def _make_branch_graph(cond: bool) -> Graph:
    return Graph(
        name="test", kind="command", description="", synonyms=(), inputs=(),
        llm_visible=False, strict=True, enabled=True, timeout_ms=5000,
        nodes=(
            Node(id="b1", ref="control.branch", kwargs={"cond": cond}),
            Node(id="y",  ref="pipeline.yes",   kwargs={}),
            Node(id="n",  ref="pipeline.no",    kwargs={}),
        ),
        edges=(
            Edge(PortRef("b1", "true"),  PortRef("y", "in")),
            Edge(PortRef("b1", "false"), PortRef("n", "in")),
        ),
        foreach_iteration_cap=50,
    )


def test_branch_routes_to_true_when_cond_true():
    log: list[str] = []
    reg = ToolRegistry()
    for name in ("yes", "no"):
        def _f(_n=name, **kw):
            log.append(_n)
        reg.register(ToolEntry(name=name, phrases=(), func=_f, module="x", docstring=None, internal=True))

    outcome, _ = GraphRuntime(registry=reg, graph_lookup=lambda n: None).run(_make_branch_graph(True), {})
    assert outcome.status == "ok"
    assert log == ["yes"]


def test_branch_routes_to_false_when_cond_false():
    log: list[str] = []
    reg = ToolRegistry()
    for name in ("yes", "no"):
        def _f(_n=name, **kw):
            log.append(_n)
        reg.register(ToolEntry(name=name, phrases=(), func=_f, module="x", docstring=None, internal=True))

    outcome, _ = GraphRuntime(registry=reg, graph_lookup=lambda n: None).run(_make_branch_graph(False), {})
    assert outcome.status == "ok"
    assert log == ["no"]
