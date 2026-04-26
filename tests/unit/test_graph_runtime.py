"""Tests for GraphRuntime: cross-graph recursion cap, missing-tool warning,
and _record_returns type-mismatch warning."""

from __future__ import annotations

import logging

import pytest

from voice_commander.commands.graph import Edge, Graph, Node, PortRef
from voice_commander.commands.graph_runtime import GraphRuntime, _MAX_CALL_DEPTH
from voice_commander.registry import ToolEntry, ToolRegistry


def _empty_graph(name: str, cross_ref: str | None = None) -> Graph:
    """Build a minimal graph that optionally calls another graph by cross-graph ref."""
    nodes: tuple[Node, ...]
    if cross_ref:
        nodes = (Node(id="n1", ref=cross_ref, kwargs={}, pos=(0, 0)),)
    else:
        nodes = ()
    return Graph(
        name=name,
        kind="command",
        description="",
        synonyms=(),
        inputs=(),
        llm_visible=False,
        strict=True,
        enabled=True,
        timeout_ms=5000,
        foreach_iteration_cap=50,
        nodes=nodes,
        edges=(),
    )


# ---------------------------------------------------------------------------
# L1: Cross-graph recursion depth cap
# ---------------------------------------------------------------------------


def test_cross_graph_recursion_depth_cap():
    """Mutually recursive graphs must not stack-overflow; error after _MAX_CALL_DEPTH."""
    # graph_a calls command.b, graph_b calls command.a → mutual recursion
    graph_a = _empty_graph("a", cross_ref="command.b")
    graph_b = _empty_graph("b", cross_ref="command.a")

    def _lookup(name: str) -> Graph | None:
        return {"a": graph_a, "b": graph_b}.get(name)

    runtime = GraphRuntime(registry=ToolRegistry(), graph_lookup=_lookup)
    outcome, _ = runtime.run(graph_a, {})

    assert outcome.status == "error"
    assert outcome.error_msg is not None
    assert "call depth" in outcome.error_msg


def test_recursion_depth_cap_value():
    """_MAX_CALL_DEPTH constant must equal 16 as specified."""
    assert _MAX_CALL_DEPTH == 16


def test_external_caller_omits_call_depth():
    """Callers that omit _call_depth must get default 0 (backward compat)."""
    g = _empty_graph("simple")
    runtime = GraphRuntime(registry=ToolRegistry(), graph_lookup=lambda n: None)
    outcome, _ = runtime.run(g, {})
    assert outcome.status == "ok"


# ---------------------------------------------------------------------------
# L13: Missing pipeline tool logs logger.warning
# ---------------------------------------------------------------------------


def test_missing_tool_logs_warning(caplog):
    """Unknown pipeline ref in main loop must log a warning."""
    g = Graph(
        name="test",
        kind="command",
        description="",
        synonyms=(),
        inputs=(),
        llm_visible=False,
        strict=False,
        enabled=True,
        timeout_ms=5000,
        foreach_iteration_cap=50,
        nodes=(Node(id="n1", ref="pipeline.nonexistent_tool", kwargs={}, pos=(0, 0)),),
        edges=(),
    )
    runtime = GraphRuntime(registry=ToolRegistry(), graph_lookup=lambda n: None)

    with caplog.at_level(logging.WARNING, logger="voice_commander.commands.graph_runtime"):
        outcome, _ = runtime.run(g, {})

    assert outcome.status == "error"
    assert any("nonexistent_tool" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# L2: _record_returns type mismatch logs logger.warning
# ---------------------------------------------------------------------------


def test_record_returns_type_mismatch_logs_warning(caplog):
    """Multi-port tool returning wrong type must log a warning."""

    def _bad_multi() -> str:
        # Returns a str, but tool declares 2 output ports → mismatch
        return "not a tuple"

    reg = ToolRegistry()
    reg.register(
        ToolEntry(
            name="bad_multi",
            phrases=(),
            func=_bad_multi,
            module="x",
            docstring=None,
            internal=True,
            returns_meta={"port_a": "str", "port_b": "str"},
        )
    )

    g = Graph(
        name="test",
        kind="command",
        description="",
        synonyms=(),
        inputs=(),
        llm_visible=False,
        strict=False,
        enabled=True,
        timeout_ms=5000,
        foreach_iteration_cap=50,
        nodes=(Node(id="n1", ref="pipeline.bad_multi", kwargs={}, pos=(0, 0)),),
        edges=(),
    )

    runtime = GraphRuntime(registry=reg, graph_lookup=lambda n: None)

    with caplog.at_level(logging.WARNING, logger="voice_commander.commands.graph_runtime"):
        outcome, _ = runtime.run(g, {})

    assert outcome.status == "ok"  # non-strict, tool ran OK; only return unpacking failed
    warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("multi-port values not recorded" in m for m in warning_msgs)


def test_record_returns_correct_tuple_no_warning(caplog):
    """Multi-port tool returning correctly-sized tuple must NOT log a warning."""

    def _good_multi():
        return ("x", "y")

    reg = ToolRegistry()
    reg.register(
        ToolEntry(
            name="good_multi",
            phrases=(),
            func=_good_multi,
            module="x",
            docstring=None,
            internal=True,
            returns_meta={"port_a": "str", "port_b": "str"},
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
        foreach_iteration_cap=50,
        nodes=(Node(id="n1", ref="pipeline.good_multi", kwargs={}, pos=(0, 0)),),
        edges=(),
    )

    runtime = GraphRuntime(registry=reg, graph_lookup=lambda n: None)

    with caplog.at_level(logging.WARNING, logger="voice_commander.commands.graph_runtime"):
        outcome, _ = runtime.run(g, {})

    assert outcome.status == "ok"
    warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert not any("multi-port values not recorded" in m for m in warning_msgs)
