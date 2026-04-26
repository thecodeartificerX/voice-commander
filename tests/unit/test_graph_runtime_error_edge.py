"""strict=True + error-edge: runtime follows error edge instead of aborting."""
from __future__ import annotations
from typing import Any

from voice_commander.commands.graph import Edge, Graph, Node, PortRef
from voice_commander.commands.graph_runtime import GraphRuntime
from voice_commander.registry import ToolEntry, ToolRegistry


def _make_reg() -> tuple[ToolRegistry, list[str]]:
    log: list[str] = []
    reg = ToolRegistry()

    def _fail(**kw: Any) -> None:
        raise RuntimeError("boom")

    def _recover(**kw: Any) -> None:
        log.append("recovered")

    reg.register(ToolEntry(
        name="fail_tool", phrases=(), func=_fail,
        module="x", docstring=None, internal=True,
    ))
    reg.register(ToolEntry(
        name="recover_tool", phrases=(), func=_recover,
        module="x", docstring=None, internal=True,
    ))
    return reg, log


def test_strict_error_edge_fires_downstream():
    """Node fails → error edge → downstream node executes (no abort)."""
    reg, log = _make_reg()
    g = Graph(
        name="test", kind="command", description="", synonyms=(), inputs=(),
        llm_visible=False, strict=True, enabled=True, timeout_ms=5000,
        nodes=(
            Node(id="n1", ref="pipeline.fail_tool", kwargs={}),
            Node(id="n2", ref="pipeline.recover_tool", kwargs={}),
        ),
        edges=(Edge(PortRef("n1", "error"), PortRef("n2", "in")),),
        foreach_iteration_cap=50,
    )
    outcome, _ = GraphRuntime(
        registry=reg, graph_lookup=lambda n: None,
    ).run(g, {})

    # error edge fired → recover_tool ran
    assert "recovered" in log
    # overall outcome still error (a node did fail)
    assert outcome.status == "error"
