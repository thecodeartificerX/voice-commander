# tests/unit/test_graph_schema.py
"""Unit tests for canonical Graph JSON parse + serialise."""

from __future__ import annotations

import json

import pytest

from voice_commander.commands.graph import Edge, Graph, GraphInput, Node, PortRef
from voice_commander.commands.graph_schema import (
    GraphSchemaError,
    parse_graph,
    serialise_graph,
)


def _sample_dict() -> dict:
    return {
        "schema_version": 1,
        "name": "search_web",
        "kind": "command",
        "description": "Search the web for {query}",
        "synonyms": ["search for", "google"],
        "inputs": [
            {"name": "query", "type": "str", "required": True, "description": "search term"}
        ],
        "llm_visible": True,
        "strict": True,
        "enabled": True,
        "timeout_ms": 5000,
        "nodes": [
            {"id": "n1", "ref": "pipeline.focus", "kwargs": {"target": "comet"}, "pos": [80, 120]},
            {"id": "n2", "ref": "pipeline.press", "kwargs": {"combo": "ctrl+t"}, "pos": [280, 120]},
        ],
        "edges": [
            {"from": "n1.ok", "to": "n2.in"},
        ],
    }


def test_parse_round_trip():
    g = parse_graph(_sample_dict())
    assert g.name == "search_web"
    assert g.kind == "command"
    assert len(g.nodes) == 2
    assert g.nodes[0].kwargs == {"target": "comet"}
    assert g.edges[0].src == PortRef("n1", "ok")

    out = serialise_graph(g)
    again = parse_graph(out)
    assert again == g


def test_parse_rejects_unknown_schema_version():
    raw = _sample_dict()
    raw["schema_version"] = 99
    with pytest.raises(GraphSchemaError) as exc:
        parse_graph(raw)
    assert "schema_version" in str(exc.value)


def test_parse_rejects_unknown_kind():
    raw = _sample_dict()
    raw["kind"] = "macro"
    with pytest.raises(GraphSchemaError):
        parse_graph(raw)


def test_parse_rejects_malformed_edge():
    raw = _sample_dict()
    raw["edges"] = [{"from": "n1.ok"}]  # missing 'to'
    with pytest.raises(GraphSchemaError):
        parse_graph(raw)


def test_parse_tolerates_missing_optionals():
    raw = _sample_dict()
    raw.pop("description")
    raw.pop("synonyms")
    raw.pop("timeout_ms")
    g = parse_graph(raw)
    assert g.description == ""
    assert g.synonyms == ()
    assert g.timeout_ms == 5000  # default


def test_serialise_is_json_compatible():
    g = parse_graph(_sample_dict())
    out = serialise_graph(g)
    # Round-trip through json to confirm no unsupported types.
    json.dumps(out)
