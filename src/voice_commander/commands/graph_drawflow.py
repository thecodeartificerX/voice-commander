"""Drawflow ↔ canonical Graph adapter.

Drawflow's editor.export() format uses integer node IDs and 1-indexed
port names (input_1, output_2, etc.). The canonical schema uses string
node IDs and semantic port names (ok, error, in, true, false, item,
after, plus data-port names from returns_meta).

This adapter is the ONLY place the Drawflow port-naming convention leaks
into the codebase. The canonical Graph is always the source of truth.

Round-trip guarantee: from_drawflow(to_drawflow(g)) must reproduce the
same canonical graph (node kwargs, edge src/dst ports, positions).
"""

from __future__ import annotations

from typing import Any

from voice_commander.commands.graph import (
    Edge,
    Graph,
    GraphInput,
    GraphKind,
    Node,
    PortRef,
)

# --- Port ordering tables ---
# For each node ref prefix, define the ordered output ports (output_1, output_2, ...)
# and input ports (input_1, input_2, ...). Unknown ports fall at the end.

_CONTROL_OUTPUT_PORTS: dict[str, list[str]] = {
    "control.branch": ["true", "false"],
    "control.foreach": ["item", "after"],
}

_CONTROL_INPUT_PORTS: dict[str, list[str]] = {
    "control.branch": ["in", "cond"],
    "control.foreach": ["in", "list"],
}

# Default for pipeline/command/workflow/value nodes
_DEFAULT_OUTPUT_PORTS = ["ok", "error"]
_DEFAULT_INPUT_PORTS = ["in"]


def _output_ports_for(ref: str, kwargs: dict[str, Any]) -> list[str]:
    """Return ordered output port names for a node ref."""
    if ref in _CONTROL_OUTPUT_PORTS:
        return list(_CONTROL_OUTPUT_PORTS[ref])
    if ref == "value.constant":
        return ["value"]
    if ref == "value.input":
        return []  # populated dynamically from graph.inputs
    if ref == "value.output":
        return ["value"]
    # pipeline / command / workflow
    return list(_DEFAULT_OUTPUT_PORTS)


def _input_ports_for(ref: str, kwargs: dict[str, Any]) -> list[str]:
    """Return ordered input port names for a node ref."""
    if ref in _CONTROL_INPUT_PORTS:
        return list(_CONTROL_INPUT_PORTS[ref])
    if ref.startswith("value."):
        return ["in", "value"]
    # pipeline / command / workflow
    return list(_DEFAULT_INPUT_PORTS)


def to_drawflow(graph: Graph) -> dict[str, Any]:
    """Convert canonical Graph to Drawflow export JSON.

    Two-pass algorithm:
    - Pass 1: discover all actual output/input ports per node by walking edges,
      including dynamic data ports that extend the static defaults.
    - Pass 2: build df_nodes with the complete port lists so that ``_out_ports``
      and ``_in_ports`` stored in the JSON capture every semantic port name,
      enabling lossless roundtrip through ``from_drawflow``.
    """
    # Assign integer IDs (1-based, stable by node order)
    node_to_int: dict[str, int] = {n.id: i + 1 for i, n in enumerate(graph.nodes)}

    # Pass 1 — discover all ports actually used per node (preserving order).
    node_out_ports: dict[str, list[str]] = {}
    node_in_ports: dict[str, list[str]] = {}
    for node in graph.nodes:
        node_out_ports[node.id] = list(_output_ports_for(node.ref, dict(node.kwargs)))
        node_in_ports[node.id] = list(_input_ports_for(node.ref, dict(node.kwargs)))

    for edge in graph.edges:
        src_id = edge.src.node_id
        dst_id = edge.dst.node_id
        if src_id not in node_to_int or dst_id not in node_to_int:
            continue  # skip input/output sentinel refs
        src_port = edge.src.port
        dst_port = edge.dst.port
        if src_port not in node_out_ports[src_id]:
            node_out_ports[src_id].append(src_port)
        if dst_port not in node_in_ports[dst_id]:
            node_in_ports[dst_id].append(dst_port)

    # Build connection dicts using the complete port lists from Pass 1.
    src_connections: dict[tuple[str, str], list[dict[str, str]]] = {}
    dst_connections: dict[tuple[str, str], list[dict[str, str]]] = {}

    for edge in graph.edges:
        src_id = edge.src.node_id
        src_port = edge.src.port
        dst_id = edge.dst.node_id
        dst_port = edge.dst.port

        if src_id not in node_to_int or dst_id not in node_to_int:
            continue  # skip input/output sentinel refs

        src_port_idx = node_out_ports[src_id].index(src_port) + 1
        dst_port_idx = node_in_ports[dst_id].index(dst_port) + 1

        src_key = (src_id, f"output_{src_port_idx}")
        dst_key = (dst_id, f"input_{dst_port_idx}")

        src_connections.setdefault(src_key, []).append(
            {
                "node": str(node_to_int[dst_id]),
                "output": f"input_{dst_port_idx}",
            }
        )
        dst_connections.setdefault(dst_key, []).append(
            {
                "node": str(node_to_int[src_id]),
                "input": f"output_{src_port_idx}",
            }
        )

    # Pass 2 — assemble df_nodes with complete, semantically-named port lists.
    df_nodes: dict[str, Any] = {}
    for node in graph.nodes:
        node_int = node_to_int[node.id]
        out_ports = node_out_ports[node.id]
        in_ports = node_in_ports[node.id]

        inputs_df: dict[str, Any] = {}
        for i, _ in enumerate(in_ports):
            key = f"input_{i + 1}"
            inputs_df[key] = {"connections": dst_connections.get((node.id, key), [])}

        outputs_df: dict[str, Any] = {}
        for i, _ in enumerate(out_ports):
            key = f"output_{i + 1}"
            outputs_df[key] = {"connections": src_connections.get((node.id, key), [])}

        df_nodes[str(node_int)] = {
            "id": node_int,
            "name": node.ref,
            "data": dict(node.kwargs),
            "class": f"node-{node.ref.split('.')[0]}",
            "html": node.ref,
            "typenode": False,
            "inputs": inputs_df,
            "outputs": outputs_df,
            "pos_x": node.pos[0],
            "pos_y": node.pos[1],
            # Store canonical id for round-trip
            "_canonical_id": node.id,
            # Store complete port index maps (including dynamic data ports) for round-trip
            "_out_ports": out_ports,
            "_in_ports": in_ports,
        }

    return {"drawflow": {"Home": {"data": df_nodes}}}


def from_drawflow(
    df: dict[str, Any],
    *,
    name: str,
    kind: GraphKind,
    description: str,
    synonyms: tuple[str, ...],
    inputs: tuple[GraphInput, ...],
    llm_visible: bool,
    strict: bool,
    enabled: bool,
    timeout_ms: int,
    foreach_iteration_cap: int,
) -> Graph:
    """Convert Drawflow export JSON back to canonical Graph."""
    data = df["drawflow"]["Home"]["data"]

    # Reconstruct nodes
    nodes: list[Node] = []
    # Map from Drawflow int-id string → canonical id
    int_to_canonical: dict[str, str] = {}

    for node_int_str, df_node in sorted(data.items(), key=lambda kv: int(kv[0])):
        canonical_id = df_node.get("_canonical_id") or f"n{node_int_str}"
        int_to_canonical[node_int_str] = canonical_id

        nodes.append(
            Node(
                id=canonical_id,
                ref=df_node["name"],
                kwargs=dict(df_node.get("data", {})),
                pos=(int(df_node.get("pos_x", 0)), int(df_node.get("pos_y", 0))),
            )
        )

    # Reconstruct edges from output connections
    edges: list[Edge] = []
    seen_edges: set[tuple[str, str, str, str]] = set()

    for node_int_str, df_node in data.items():
        src_canonical = int_to_canonical[node_int_str]
        out_ports: list[str] = df_node.get("_out_ports", ["ok", "error"])

        for out_key, out_val in df_node.get("outputs", {}).items():
            # out_key = "output_1", "output_2", ...
            out_idx = int(out_key.split("_")[1]) - 1
            src_port = out_ports[out_idx] if out_idx < len(out_ports) else out_key

            for conn in out_val.get("connections", []):
                dst_node_int = conn["node"]
                if dst_node_int not in int_to_canonical:
                    continue
                dst_canonical = int_to_canonical[dst_node_int]
                dst_in_port_key = conn["output"]  # Drawflow calls it "output" on the receiving end

                # Get in_ports for dst node
                dst_df_node = data[dst_node_int]
                dst_in_ports: list[str] = dst_df_node.get("_in_ports", ["in"])
                dst_idx = int(dst_in_port_key.split("_")[1]) - 1
                if dst_idx < len(dst_in_ports):  # noqa: SIM108
                    dst_port = dst_in_ports[dst_idx]
                else:
                    dst_port = dst_in_port_key

                edge_key = (src_canonical, src_port, dst_canonical, dst_port)
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    edges.append(
                        Edge(
                            src=PortRef(src_canonical, src_port),
                            dst=PortRef(dst_canonical, dst_port),
                        )
                    )

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
        nodes=tuple(nodes),
        edges=tuple(edges),
    )
