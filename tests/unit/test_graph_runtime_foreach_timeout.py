import time

from voice_commander.commands.graph import Edge, Graph, Node, PortRef
from voice_commander.commands.graph_runtime import GraphRuntime
from voice_commander.registry import ToolEntry, ToolRegistry


def test_foreach_body_respects_timeout():
    """Foreach with slow body node should trigger timeout, not run forever."""
    reg = ToolRegistry()

    def _slow(item):
        time.sleep(0.05)  # 50ms per item

    reg.register(
        ToolEntry(
            name="slow",
            phrases=(),
            func=_slow,
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
        timeout_ms=80,  # 80ms — allows ~1-2 iterations of 50ms each
        nodes=(
            Node(id="f1", ref="control.foreach", kwargs={"list": ["a", "b", "c", "d", "e"]}),
            Node(id="s1", ref="pipeline.slow", kwargs={}),
        ),
        edges=(
            Edge(PortRef("f1", "item"), PortRef("s1", "in")),
            Edge(PortRef("f1", "item"), PortRef("s1", "item")),
        ),
        foreach_iteration_cap=50,
    )

    outcome, _ = GraphRuntime(registry=reg, graph_lookup=lambda n: None).run(g, {})
    assert outcome.status == "error"
    assert "timeout" in (outcome.error_msg or "")
