"""Topological sort + cycle detection for Graph nodes.

Edges between control or data ports both contribute to the dependency order
the same way: dst node depends on src node. Self-loops and cross-component
cycles raise CycleError with the offending node ids surfaced.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable

from voice_commander.commands.graph import Edge, Node


class CycleError(ValueError):
    """Raised when a Graph contains a cycle."""

    def __init__(self, cycle_node_ids: list[str]) -> None:
        super().__init__(f"cycle through nodes: {cycle_node_ids}")
        self.cycle_node_ids = cycle_node_ids


def topo_sort(nodes: Iterable[Node], edges: Iterable[Edge]) -> list[Node]:
    nodes_list = list(nodes)
    by_id = {n.id: n for n in nodes_list}
    in_degree: dict[str, int] = defaultdict(int)
    successors: dict[str, list[str]] = defaultdict(list)

    for n in nodes_list:
        in_degree.setdefault(n.id, 0)

    seen_pairs: set[tuple[str, str]] = set()
    for e in edges:
        src_id = e.src.node_id
        dst_id = e.dst.node_id
        if src_id in {"input", "output"} or dst_id in {"input", "output"}:
            continue
        if src_id == dst_id:
            raise CycleError([src_id])
        pair = (src_id, dst_id)
        if pair not in seen_pairs:
            seen_pairs.add(pair)
            successors[src_id].append(dst_id)
            in_degree[dst_id] += 1

    queue: deque[str] = deque(n.id for n in nodes_list if in_degree[n.id] == 0)
    out: list[Node] = []
    while queue:
        nid = queue.popleft()
        out.append(by_id[nid])
        for succ in successors[nid]:
            in_degree[succ] -= 1
            if in_degree[succ] == 0:
                queue.append(succ)

    if len(out) != len(nodes_list):
        out_ids = {n.id for n in out}
        remaining = [n.id for n in nodes_list if n.id not in out_ids]
        raise CycleError(remaining)
    return out
