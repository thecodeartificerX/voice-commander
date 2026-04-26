# tests/unit/test_graph_types.py
"""Unit tests for Graph value objects (no runtime, no IO)."""

from __future__ import annotations

import pytest

from voice_commander.commands.graph import (
    Edge,
    Graph,
    GraphInput,
    Node,
    PortRef,
)


def test_portref_str_round_trip():
    p = PortRef.parse("n1.ok")
    assert p.node_id == "n1"
    assert p.port == "ok"
    assert str(p) == "n1.ok"


def test_portref_input_shorthand():
    p = PortRef.parse("input.query")
    assert p.node_id == "input"
    assert p.port == "query"
    assert p.is_input_shorthand is True


def test_portref_rejects_empty_segments():
    with pytest.raises(ValueError):
        PortRef.parse(".ok")
    with pytest.raises(ValueError):
        PortRef.parse("n1.")
    with pytest.raises(ValueError):
        PortRef.parse("n1")


def test_node_freezes_kwargs_dict():
    n = Node(id="n1", ref="pipeline.focus", kwargs={"target": "comet"}, pos=(80, 120))
    assert n.id == "n1"
    assert n.ref == "pipeline.focus"
    assert n.kwargs == {"target": "comet"}
    assert n.pos == (80, 120)


def test_edge_construction():
    e = Edge(src=PortRef("n1", "ok"), dst=PortRef("n2", "in"))
    assert e.src.node_id == "n1"
    assert e.dst.port == "in"


def test_graph_construction_minimal():
    g = Graph(
        name="search_web",
        kind="command",
        description="",
        synonyms=(),
        inputs=(),
        llm_visible=True,
        strict=True,
        enabled=True,
        timeout_ms=5000,
        nodes=(),
        edges=(),
    )
    assert g.name == "search_web"
    assert g.kind == "command"
    assert g.timeout_ms == 5000


def test_graph_input_construction():
    gi = GraphInput(name="query", type="str", required=True, description="search term")
    assert gi.name == "query"
    assert gi.type == "str"
    assert gi.required is True
