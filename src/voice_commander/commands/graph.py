# src/voice_commander/commands/graph.py
"""Canonical Graph value objects for the node-graph builder.

This module holds only data shapes. The DAG execution runtime lives in the
same package but in a separate module so unit tests can exercise the value
objects without pulling in the registry, dispatcher, or any IO dependency.

Reference grammar (see spec section 4.3):

- ``pipeline.<name>`` — primitive registered via @tool
- ``command.<name>`` — graph in commands.json
- ``workflow.<name>`` — graph in workflows.json
- ``control.branch`` / ``control.foreach`` — built-in control nodes
- ``value.constant`` / ``value.input`` / ``value.output`` — built-in value nodes
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

GraphKind = Literal["command", "workflow"]


@dataclass(frozen=True)
class PortRef:
    """Reference to a port on a node, the singleton input slot, or the output sink."""

    node_id: str  # concrete node id, or "input" for shorthand source, or "output" for sink
    port: str

    def __post_init__(self) -> None:
        if not self.node_id or not self.port:
            raise ValueError(f"PortRef segments cannot be empty: {self.node_id!r}.{self.port!r}")

    @classmethod
    def parse(cls, raw: str) -> PortRef:
        """Parse ``"<node_id>.<port>"`` into a PortRef. Raises ValueError on malformed input."""
        if "." not in raw:
            raise ValueError(f"PortRef must be of the form '<node>.<port>'; got {raw!r}")
        head, _, tail = raw.partition(".")
        return cls(node_id=head, port=tail)

    def __str__(self) -> str:
        return f"{self.node_id}.{self.port}"

    @property
    def is_input_shorthand(self) -> bool:
        """True when PortRef uses ``input.<name>`` shorthand for the Input node."""
        return self.node_id == "input"


@dataclass(frozen=True)
class Edge:
    src: PortRef
    dst: PortRef


@dataclass(frozen=True)
class Node:
    id: str
    ref: str
    kwargs: Mapping[str, Any] = field(default_factory=dict)
    pos: tuple[int, int] = (0, 0)


@dataclass(frozen=True)
class GraphInput:
    name: str
    type: str  # "str" | "int" | "bool" | "float"
    required: bool = True
    description: str = ""


@dataclass(frozen=True)
class Graph:
    name: str
    kind: GraphKind
    description: str
    synonyms: tuple[str, ...]
    inputs: tuple[GraphInput, ...]
    llm_visible: bool
    strict: bool
    enabled: bool
    timeout_ms: int
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
    foreach_iteration_cap: int = 50
