"""Save-time + startup validator for Graph definitions.

Each rule is a single function that returns a list[ValidationError]. The
top-level ``validate()`` runs all rules and concatenates the results.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from voice_commander.commands.graph import Graph
from voice_commander.commands.graph_topo import CycleError, topo_sort

FOREACH_GLOBAL_CEILING = 1000

BUILTIN_REFS = frozenset(
    {
        "control.branch",
        "control.foreach",
        "value.constant",
        "value.input",
        "value.output",
    }
)


class ValidationSeverity(Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class ValidationError:
    severity: ValidationSeverity
    message: str
    node_id: str | None = None
    port: str | None = None


def validate(
    graph: Graph,
    *,
    registry: Any,
    peers: dict[str, Graph],
) -> list[ValidationError]:
    """Run all rules. Returns list of ValidationError (may be empty)."""
    out: list[ValidationError] = []
    out.extend(_rule_intra_cycle(graph))
    out.extend(_rule_unknown_ref(graph, registry, peers))
    out.extend(_rule_orphan_required(graph, registry))
    out.extend(_rule_foreach_cap(graph))
    out.extend(_rule_branch_unreachable(graph))
    out.extend(_rule_cross_graph_cycle(graph, peers))
    out.extend(_rule_name_collision(graph, peers))
    return out


def _rule_intra_cycle(graph: Graph) -> list[ValidationError]:
    """Reject graphs containing cycles — ``topo_sort`` must succeed."""
    try:
        topo_sort(graph.nodes, graph.edges)
    except CycleError as exc:
        return [
            ValidationError(
                severity=ValidationSeverity.ERROR,
                message=f"cycle through nodes: {exc.cycle_node_ids}",
            )
        ]
    return []


def _rule_unknown_ref(
    graph: Graph,
    registry: Any,
    peers: dict[str, Graph],
) -> list[ValidationError]:
    """Reject nodes whose ref is not resolvable.

    Checks pipeline.* refs against the registry, command.*/workflow.* refs
    against peers, and rejects any other unknown prefixes.
    """
    out: list[ValidationError] = []
    for node in graph.nodes:
        ref = node.ref
        if ref in BUILTIN_REFS:
            continue
        if ref.startswith("pipeline."):
            tool_name = ref.removeprefix("pipeline.")
            if registry is not None and registry.by_name(tool_name) is None:
                out.append(
                    ValidationError(
                        severity=ValidationSeverity.ERROR,
                        message=f"unknown pipeline ref: {ref!r}",
                        node_id=node.id,
                    )
                )
        elif ref.startswith("command.") or ref.startswith("workflow."):
            child_name = ref.split(".", 1)[1]
            if child_name not in peers:
                out.append(
                    ValidationError(
                        severity=ValidationSeverity.ERROR,
                        message=f"unknown cross-graph ref: {ref!r} (not found in peers)",
                        node_id=node.id,
                    )
                )
        else:
            out.append(
                ValidationError(
                    severity=ValidationSeverity.ERROR,
                    message=f"unrecognised node ref: {ref!r}",
                    node_id=node.id,
                )
            )
    return out


def _rule_orphan_required(graph: Graph, registry: Any) -> list[ValidationError]:
    """Reject pipeline nodes whose required args lack both a kwarg and a data wire."""
    if registry is None:
        return []
    out: list[ValidationError] = []
    edges_by_dst: set[tuple[str, str]] = {(e.dst.node_id, e.dst.port) for e in graph.edges}
    for node in graph.nodes:
        if not node.ref.startswith("pipeline."):
            continue
        tool_name = node.ref.removeprefix("pipeline.")
        entry = registry.by_name(tool_name)
        if entry is None:
            continue
        args_meta = getattr(entry, "args_meta", None) or {}
        for arg_name, meta in args_meta.items():
            required = meta.required if hasattr(meta, "required") else meta.get("required", False)
            if isinstance(required, str):
                required = required.lower() == "true"
            if not required:
                continue
            if arg_name in node.kwargs:
                continue
            if (node.id, arg_name) in edges_by_dst:
                continue
            out.append(
                ValidationError(
                    severity=ValidationSeverity.ERROR,
                    message=f"required input {arg_name!r} is unbound (no kwarg, no edge)",
                    node_id=node.id,
                    port=arg_name,
                )
            )
    return out


def _rule_foreach_cap(graph: Graph) -> list[ValidationError]:
    """Reject foreach iteration caps exceeding the global ceiling (1 000).

    Checks both node-level ``cap`` kwargs and the graph-level
    ``foreach_iteration_cap`` field.
    """
    out: list[ValidationError] = []
    for node in graph.nodes:
        if node.ref != "control.foreach":
            continue
        cap = node.kwargs.get("cap")
        if isinstance(cap, int) and cap > FOREACH_GLOBAL_CEILING:
            out.append(
                ValidationError(
                    severity=ValidationSeverity.ERROR,
                    message=f"foreach cap={cap} exceeds global ceiling {FOREACH_GLOBAL_CEILING}",
                    node_id=node.id,
                )
            )
    # Also check the graph-level foreach_iteration_cap
    if graph.foreach_iteration_cap > FOREACH_GLOBAL_CEILING:
        out.append(
            ValidationError(
                severity=ValidationSeverity.ERROR,
                message=(
                    f"foreach cap={graph.foreach_iteration_cap}"
                    f" exceeds global ceiling {FOREACH_GLOBAL_CEILING}"
                ),
                node_id="f1",
            )
        )
    return out


def _rule_branch_unreachable(graph: Graph) -> list[ValidationError]:
    """Warn when a branch node is unreachable or has no downstream wiring.

    Emits WARNINGs (not ERRORs) for two cases:
    - ``cond`` input not wired — the branch node is unreachable.
    - ``cond`` wired but neither ``true`` nor ``false`` outputs connected — dead path.
    """
    out: list[ValidationError] = []
    for node in graph.nodes:
        if node.ref != "control.branch":
            continue
        cond_wired = any(e.dst.node_id == node.id and e.dst.port == "cond" for e in graph.edges)
        true_wired = any(e.src.node_id == node.id and e.src.port == "true" for e in graph.edges)
        false_wired = any(e.src.node_id == node.id and e.src.port == "false" for e in graph.edges)
        if not cond_wired:
            out.append(
                ValidationError(
                    severity=ValidationSeverity.WARNING,
                    message="branch has no condition wired — node is unreachable",
                    node_id=node.id,
                )
            )
        elif not true_wired and not false_wired:
            out.append(
                ValidationError(
                    severity=ValidationSeverity.WARNING,
                    message=(
                        "branch has cond wired but neither true nor false outputs are connected"
                        " (unreachable)"
                    ),
                    node_id=node.id,
                )
            )
    return out


def _rule_cross_graph_cycle(graph: Graph, peers: dict[str, Graph]) -> list[ValidationError]:
    """DFS to detect cycles through cross-graph node references."""
    visited: set[str] = set()
    all_graphs = {graph.name: graph, **peers}
    cycle_path: list[str] = []

    def _refs(g: Graph) -> list[str]:
        out: list[str] = []
        for n in g.nodes:
            if n.ref.startswith("command.") or n.ref.startswith("workflow."):
                out.append(n.ref.split(".", 1)[1])
        return out

    def _dfs(name: str, path: list[str]) -> bool:
        if name in path:
            cycle_path.extend(path[path.index(name) :] + [name])
            return True
        if name in visited or name not in all_graphs:
            return False
        for ref in _refs(all_graphs[name]):
            if _dfs(ref, path + [name]):
                return True
        visited.add(name)
        return False

    if _dfs(graph.name, []):
        return [
            ValidationError(
                severity=ValidationSeverity.ERROR,
                message=f"cycle through cross-graph references: {' -> '.join(cycle_path)}",
            )
        ]
    return []


def _rule_name_collision(graph: Graph, peers: dict[str, Graph]) -> list[ValidationError]:
    """Reject graphs whose name collides with an existing peer graph."""
    if graph.name in peers:
        return [
            ValidationError(
                severity=ValidationSeverity.ERROR,
                message=f"name collision: graph {graph.name!r} already exists in peers",
            )
        ]
    return []
