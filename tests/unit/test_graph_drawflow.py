"""Tests for Drawflow adapter round-trips."""
from __future__ import annotations

import pytest

from voice_commander.commands.graph import Edge, Graph, GraphInput, Node, PortRef
from voice_commander.commands.graph_drawflow import from_drawflow, to_drawflow


def _minimal_graph() -> Graph:
    return Graph(
        name="test", kind="command", description="Search", synonyms=("search",),
        inputs=(GraphInput(name="query", type="str", required=True, description=""),),
        llm_visible=True, strict=True, enabled=True, timeout_ms=5000,
        foreach_iteration_cap=50,
        nodes=(
            Node(id="n1", ref="pipeline.focus", kwargs={"target": "comet"}, pos=(80, 120)),
            Node(id="n2", ref="pipeline.press", kwargs={"combo": "ctrl+t"}, pos=(280, 120)),
        ),
        edges=(
            Edge(PortRef("n1", "ok"), PortRef("n2", "in")),
        ),
    )


def test_to_drawflow_produces_valid_structure():
    g = _minimal_graph()
    df = to_drawflow(g)
    assert "drawflow" in df
    assert "Home" in df["drawflow"]
    data = df["drawflow"]["Home"]["data"]
    assert len(data) == 2


def test_node_data_preserved():
    g = _minimal_graph()
    df = to_drawflow(g)
    data = df["drawflow"]["Home"]["data"]
    # Find node with ref=pipeline.focus
    n1_df = next(v for v in data.values() if v["name"] == "pipeline.focus")
    assert n1_df["data"] == {"target": "comet"}
    assert n1_df["pos_x"] == 80
    assert n1_df["pos_y"] == 120


def test_edge_encoded_as_connection():
    g = _minimal_graph()
    df = to_drawflow(g)
    data = df["drawflow"]["Home"]["data"]
    # n1's output_1 (ok port) should have a connection to n2's input_1 (in port)
    n1_df = next(v for v in data.values() if v["name"] == "pipeline.focus")
    connections = n1_df["outputs"]["output_1"]["connections"]
    assert len(connections) == 1


def test_round_trip_preserves_graph():
    g = _minimal_graph()
    df = to_drawflow(g)
    recovered = from_drawflow(df, name=g.name, kind=g.kind,
                               description=g.description, synonyms=g.synonyms,
                               inputs=g.inputs, llm_visible=g.llm_visible,
                               strict=g.strict, enabled=g.enabled,
                               timeout_ms=g.timeout_ms,
                               foreach_iteration_cap=g.foreach_iteration_cap)
    assert recovered.name == g.name
    assert len(recovered.nodes) == len(g.nodes)
    assert len(recovered.edges) == len(g.edges)
    # Verify edge directions preserved
    assert recovered.edges[0].src.port == "ok"
    assert recovered.edges[0].dst.port == "in"


def test_branch_node_round_trip():
    g = Graph(
        name="branch_test", kind="command", description="", synonyms=(),
        inputs=(), llm_visible=True, strict=True, enabled=True, timeout_ms=5000,
        foreach_iteration_cap=50,
        nodes=(
            Node(id="b1", ref="control.branch", kwargs={"cond": True}, pos=(0, 0)),
            Node(id="y",  ref="pipeline.yes",   kwargs={}, pos=(200, 0)),
            Node(id="n",  ref="pipeline.no",    kwargs={}, pos=(200, 100)),
        ),
        edges=(
            Edge(PortRef("b1", "true"),  PortRef("y", "in")),
            Edge(PortRef("b1", "false"), PortRef("n", "in")),
        ),
    )
    df = to_drawflow(g)
    recovered = from_drawflow(df, name=g.name, kind=g.kind,
                               description=g.description, synonyms=g.synonyms,
                               inputs=g.inputs, llm_visible=g.llm_visible,
                               strict=g.strict, enabled=g.enabled,
                               timeout_ms=g.timeout_ms,
                               foreach_iteration_cap=g.foreach_iteration_cap)
    assert len(recovered.nodes) == 3
    assert len(recovered.edges) == 2
    # Verify branch edges preserved
    edge_ports = {(e.src.port, e.dst.port) for e in recovered.edges}
    assert ("true", "in") in edge_ports
    assert ("false", "in") in edge_ports
