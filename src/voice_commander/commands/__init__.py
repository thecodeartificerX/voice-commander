"""User-defined commands + workflows: graph data model, persistence, and registrar.

Overview
--------
A *command* is a named node graph that invokes a pipeline of tool calls (e.g.
``search_web`` → focus browser, open new tab, type query, press enter). A
*workflow* is the same shape but typically parametrised with user-supplied
inputs and intended for multi-step automation.

Both kinds share the same canonical :class:`Graph` schema and are persisted by
:class:`~voice_commander.commands.store.GraphStore`. The store is locked to a
single ``kind``; commands live in ``commands.json`` and workflows in
``workflows.json``.

The LLM sees **only** commands + workflows — never raw primitives. Match →
instant dispatch of a pre-baked plan. No match → miss chime. No retry,
no agentic loop.

Storage
-------
Commands live in ``commands.json`` at the project root. Workflows live in
``workflows.json``. First-run seeding copies the bundled
``commands.default.json`` / ``workflows.default.json`` into place if no
user-owned file is present.

Module layout
-------------
- :mod:`voice_commander.commands.graph` — frozen dataclasses (Graph, Node, Edge, …).
- :mod:`voice_commander.commands.graph_schema` — JSON ↔ Graph conversion.
- :mod:`voice_commander.commands.store` — GraphStore + GraphStoreError.
- :mod:`voice_commander.commands.template` — ``{placeholder}`` substitution.
- :mod:`voice_commander.commands.registrar` — synthesise ``ToolEntry``
  objects + hot-reload hook.
"""

from voice_commander.commands.graph import (
    Edge,
    Graph,
    GraphInput,
    GraphKind,
    Node,
    PortRef,
)
from voice_commander.commands.store import (
    GraphStore,
    GraphStoreError,
    seed_if_missing,
)

__all__ = [
    "Edge",
    "Graph",
    "GraphInput",
    "GraphKind",
    "GraphStore",
    "GraphStoreError",
    "Node",
    "PortRef",
    "seed_if_missing",
]
