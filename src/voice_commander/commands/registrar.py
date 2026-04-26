"""Synthesise ToolEntry objects for every graph in a GraphStore.

Each graph becomes a ToolEntry whose ``func`` is a closure: invoking the
closure (with kwargs supplied by the LLM) hands off to GraphRuntime.run.
The OpenAI tool schema is derived from the graph's typed ``inputs[]``.

Public entry points:

- :func:`register_graphs` — idempotent; safe to call on each hot-reload.
- :func:`reload_all` — convenience wrapper used by the daemon startup path
  and by the web UI after a save.

All functions acquire the daemon's ``reload_lock`` externally — registrar
code itself is not thread-safe against concurrent discover() runs.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from voice_commander.commands.graph import Graph
from voice_commander.commands.graph_runtime import GraphRuntime
from voice_commander.commands.store import GraphStore
from voice_commander.registry import ToolEntry, ToolRegistry

logger = logging.getLogger(__name__)

_TYPE_MAP: dict[str, str] = {
    "str": "string",
    "string": "string",
    "int": "integer",
    "integer": "integer",
    "bool": "boolean",
    "boolean": "boolean",
    "float": "number",
    "number": "number",
}


def register_graphs(
    registry: ToolRegistry,
    store: GraphStore,
    *,
    runtime_factory: Callable[[ToolRegistry, Callable[[str], Graph | None]], GraphRuntime]
    | None = None,
    peer_graphs: dict[str, Graph] | None = None,
) -> list[str]:
    """Register every enabled graph in *store* as a ToolEntry.

    Drops existing entries whose origin matches ``store.kind``, then
    registers all enabled graphs from the store.

    .. note::

       The ``GraphRuntime`` built here resolves graph-name references
       **only** within the store's own graphs (plus *peer_graphs* when
       supplied).  In contrast, :func:`reload_all` always builds a
       single runtime spanning both command and workflow stores.

       If a graph references peers from the other store (e.g. a workflow
       that calls a command), pass those peers via *peer_graphs* so the
       runtime can resolve them.  Without *peer_graphs*, cross-store
       references return ``None`` at lookup time.

    Returns the list of graph names that were registered.
    """
    origin = store.kind
    _drop_origin(registry, origin)

    graphs = store.load_all()

    def _lookup(name: str) -> Graph | None:
        return graphs.get(name) or (peer_graphs or {}).get(name)

    factory = runtime_factory or (lambda r, lookup: GraphRuntime(r, lookup))
    runtime = factory(registry, _lookup)

    names: list[str] = []
    for g in graphs.values():
        if not g.enabled:
            continue
        internal = not g.llm_visible
        params_schema = _build_schema(g)
        entry = _build_entry(g, runtime, origin, internal, params_schema)
        registry.register(entry)
        names.append(g.name)
    logger.info("Registered %d %s graphs: %s", len(names), origin, names)
    return names


def reload_all(
    registry: ToolRegistry,
    command_store: GraphStore,
    workflow_store: GraphStore,
    *,
    runtime_factory: Callable[[ToolRegistry, Callable[[str], Graph | None]], GraphRuntime]
    | None = None,
) -> tuple[list[str], list[str]]:
    """Reload both command and workflow stores into a shared runtime.

    Unlike :func:`register_graphs` (which scopes its runtime lookup to a
    single store), this function builds **one** ``GraphRuntime`` whose
    lookup spans both stores — so cross-store graph references resolve
    correctly.

    Returns ``(command_names, workflow_names)``.
    """
    cmd_graphs = command_store.load_all()
    wf_graphs = workflow_store.load_all()

    def _lookup(name: str) -> Graph | None:
        return cmd_graphs.get(name) or wf_graphs.get(name)

    factory = runtime_factory or (lambda r, lookup: GraphRuntime(r, lookup))
    runtime = factory(registry, _lookup)

    # Drop both origins before re-registering
    _drop_origin(registry, "command")
    _drop_origin(registry, "workflow")

    cmd_names: list[str] = []
    for g in cmd_graphs.values():
        if not g.enabled:
            continue
        entry = _build_entry(g, runtime, "command", not g.llm_visible, _build_schema(g))
        registry.register(entry)
        cmd_names.append(g.name)

    wf_names: list[str] = []
    for g in wf_graphs.values():
        if not g.enabled:
            continue
        entry = _build_entry(g, runtime, "workflow", not g.llm_visible, _build_schema(g))
        registry.register(entry)
        wf_names.append(g.name)

    return cmd_names, wf_names


def _build_entry(
    g: Graph,
    runtime: GraphRuntime,
    origin: str,
    internal: bool,
    params_schema: dict[str, Any],
) -> ToolEntry:
    return ToolEntry(
        name=g.name,
        phrases=tuple(g.synonyms),
        func=_make_func(g, runtime),
        module="voice_commander.commands",
        docstring=g.description or None,
        description=g.description,
        category=origin,
        enabled=True,
        params_schema=params_schema,
        settle_ms=0,
        llm_only=True,
        internal=internal,
        origin=origin,  # type: ignore[arg-type]
    )


def _make_func(g: Graph, runtime: GraphRuntime) -> Any:
    def _run(**call_kwargs: Any) -> None:
        outcome, _ret = runtime.run(g, call_kwargs)
        if outcome.status == "error":
            raise RuntimeError(outcome.error_msg or "graph execution failed")

    _run.__name__ = f"graph__{g.name}"
    _run.__doc__ = g.description or None
    return _run


def _build_schema(g: Graph) -> dict[str, Any]:
    props: dict[str, Any] = {}
    required: list[str] = []
    for inp in g.inputs:
        json_type = _TYPE_MAP.get(inp.type.lower(), "string")
        props[inp.name] = {"type": json_type, "description": inp.description or inp.name}
        if inp.required:
            required.append(inp.name)
    description = g.description
    if g.synonyms:
        description = f"{description}\nPhrases: {', '.join(g.synonyms)}"
    return {
        "type": "function",
        "function": {
            "name": g.name,
            "description": description.strip(),
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


def _drop_origin(registry: ToolRegistry, origin: str) -> None:
    """Remove all ToolEntry instances with the given origin from the registry."""
    for entry in list(registry.by_origin(origin)):  # type: ignore[arg-type]
        registry.remove(entry.name)
