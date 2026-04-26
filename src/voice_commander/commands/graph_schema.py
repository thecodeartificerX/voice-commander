# src/voice_commander/commands/graph_schema.py
"""Canonical Graph JSON ↔ Graph value-object conversion.

Schema version is embedded in the JSON. Unknown versions raise
GraphSchemaError; the daemon refuses to load forward-incompatible files.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from voice_commander.commands.graph import (
    Edge,
    Graph,
    GraphInput,
    GraphKind,
    Node,
    PortRef,
)

CURRENT_SCHEMA_VERSION = 1


class GraphSchemaError(ValueError):
    """Raised when canonical-graph JSON cannot be parsed."""


def parse_graph(raw: Mapping[str, Any]) -> Graph:
    """Parse a raw JSON-like mapping into a ``Graph`` value-object.

    Validates schema version, requires ``name`` and ``kind``, and extracts
    optional fields with sensible defaults (strict=True, enabled=True,
    timeout_ms=5000, etc.).

    Raises:
        GraphSchemaError: If any required field is missing or invalid, or if
            the schema version is not supported.
    """
    if not isinstance(raw, Mapping):
        raise GraphSchemaError(f"graph JSON must be an object, got {type(raw).__name__}")

    version = raw.get("schema_version")
    if version != CURRENT_SCHEMA_VERSION:
        raise GraphSchemaError(
            f"unsupported schema_version={version!r}; expected {CURRENT_SCHEMA_VERSION}"
        )

    name = _str(raw, "name")
    kind_raw = _str(raw, "kind")
    if kind_raw not in ("command", "workflow"):
        raise GraphSchemaError(f"kind must be 'command' or 'workflow', got {kind_raw!r}")
    kind: GraphKind = kind_raw  # type: ignore[assignment]

    description = str(raw.get("description", ""))
    synonyms = tuple(str(s) for s in raw.get("synonyms", []) or ())
    inputs = tuple(_parse_input(i) for i in raw.get("inputs", []) or ())
    llm_visible = bool(raw.get("llm_visible", True))
    strict = bool(raw.get("strict", True))
    enabled = bool(raw.get("enabled", True))
    timeout_ms = int(raw.get("timeout_ms", 5000))
    if timeout_ms <= 0:
        raise GraphSchemaError(f"timeout_ms must be a positive integer, got {timeout_ms}")
    foreach_iteration_cap = int(raw.get("foreach_iteration_cap", 50))
    if foreach_iteration_cap < 1:
        raise GraphSchemaError(f"foreach_iteration_cap must be >= 1, got {foreach_iteration_cap}")
    nodes = tuple(_parse_node(n) for n in raw.get("nodes", []) or ())
    edges = tuple(_parse_edge(e) for e in raw.get("edges", []) or ())

    return Graph(
        name=name,
        kind=kind,
        description=description,
        synonyms=synonyms,
        inputs=inputs,
        llm_visible=llm_visible,
        strict=strict,
        enabled=enabled,
        timeout_ms=timeout_ms,
        foreach_iteration_cap=foreach_iteration_cap,
        nodes=nodes,
        edges=edges,
    )


def serialise_graph(g: Graph) -> dict[str, Any]:
    """Convert a ``Graph`` back into a canonical JSON-serialisable dict.

    Embeds the current schema version; flattens inputs, nodes (with pos as a
    two-element list), and edges (with PortRef string representation).
    """
    return {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "name": g.name,
        "kind": g.kind,
        "description": g.description,
        "synonyms": list(g.synonyms),
        "inputs": [
            {
                "name": i.name,
                "type": i.type,
                "required": i.required,
                "description": i.description,
            }
            for i in g.inputs
        ],
        "llm_visible": g.llm_visible,
        "strict": g.strict,
        "enabled": g.enabled,
        "timeout_ms": g.timeout_ms,
        "foreach_iteration_cap": g.foreach_iteration_cap,
        "nodes": [
            {"id": n.id, "ref": n.ref, "kwargs": dict(n.kwargs), "pos": list(n.pos)}
            for n in g.nodes
        ],
        "edges": [{"from": str(e.src), "to": str(e.dst)} for e in g.edges],
    }


def _str(raw: Mapping[str, Any], key: str) -> str:
    """Extract and validate a required string field from *raw*.

    Raises:
        GraphSchemaError: If the field is missing or empty.
    """
    val = raw.get(key)
    if not isinstance(val, str) or not val:
        raise GraphSchemaError(f"required string field {key!r} missing or empty")
    return val


def _parse_input(raw: Any) -> GraphInput:
    """Parse raw mapping into a ``GraphInput``, defaulting type/required/description."""
    if not isinstance(raw, Mapping):
        raise GraphSchemaError(f"input must be an object, got {type(raw).__name__}")
    return GraphInput(
        name=_str(raw, "name"),
        type=str(raw.get("type", "str")),
        required=bool(raw.get("required", True)),
        description=str(raw.get("description", "")),
    )


def _parse_node(raw: Any) -> Node:
    """Parse a raw mapping into a ``Node``.

    Validates that ``pos`` is a two-element coordinate pair.

    Raises:
        GraphSchemaError: If the mapping is invalid or pos is malformed.
    """
    if not isinstance(raw, Mapping):
        raise GraphSchemaError(f"node must be an object, got {type(raw).__name__}")
    pos_raw = raw.get("pos", [0, 0])
    if not (isinstance(pos_raw, list) and len(pos_raw) == 2):
        raise GraphSchemaError(f"node.pos must be [x, y]; got {pos_raw!r}")
    return Node(
        id=_str(raw, "id"),
        ref=_str(raw, "ref"),
        kwargs=dict(raw.get("kwargs", {}) or {}),
        pos=(int(pos_raw[0]), int(pos_raw[1])),
    )


def _parse_edge(raw: Any) -> Edge:
    """Parse a raw mapping into an ``Edge``.

    Delegates port-ref parsing to ``PortRef.parse()``, wrapping ``ValueError``
    in ``GraphSchemaError``.
    """
    if not isinstance(raw, Mapping):
        raise GraphSchemaError(f"edge must be an object, got {type(raw).__name__}")
    src_raw = raw.get("from")
    dst_raw = raw.get("to")
    if not isinstance(src_raw, str) or not isinstance(dst_raw, str):
        raise GraphSchemaError(f"edge requires 'from' and 'to' string fields; got {raw!r}")
    try:
        return Edge(src=PortRef.parse(src_raw), dst=PortRef.parse(dst_raw))
    except ValueError as exc:
        raise GraphSchemaError(f"malformed edge {raw!r}: {exc}") from exc
