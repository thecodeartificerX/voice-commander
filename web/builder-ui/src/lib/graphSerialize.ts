import type { Node, Edge } from 'reactflow'
import type { Graph, GraphNode, GraphEdge, EdgeKind } from '@/types/graph'

/**
 * normalizeArgs — port of the dict/array kwarg shim from builder.js (ADR 0069).
 * Canonical graph JSON stores kwargs as Record<string, unknown>.
 * Some pipeline primitives use positional arrays; this normalises both to dict form.
 */
export function normalizeArgs(
  args: unknown[] | Record<string, unknown> | null | undefined,
  argSchema: Array<{ name: string }>,
): Record<string, unknown> {
  if (!args) return {}
  if (Array.isArray(args)) {
    const result: Record<string, unknown> = {}
    args.forEach((val, i) => {
      const key = argSchema[i]?.name ?? `arg${i}`
      result[key] = val
    })
    return result
  }
  return args
}

/** Default port names — match backend conventions. */
const DEFAULT_SOURCE_PORT = 'ok'
const DEFAULT_TARGET_PORT = 'in'

/** Source ports that classify the edge as a data wire (visual styling hook). */
const DATA_PORT: EdgeKind = 'data'

/** Map canonical GraphNode → React Flow Node */
export function toFlowNode(gn: GraphNode): Node {
  return {
    id: gn.id,
    type: refToNodeType(gn.ref),
    position: { x: gn.pos[0], y: gn.pos[1] },
    data: {
      ref: gn.ref,
      kwargs: gn.kwargs ?? {},
    },
  }
}

/**
 * Split a PortRef ("node.port") into [node, port]. Mirrors the backend
 * `PortRef.parse` which uses `str.partition('.')` — first dot wins, the rest
 * of the string is the port. Falls back to a sensible default when the input
 * is malformed (no dot).
 */
function parsePortRef(ref: string, defaultPort: string): [string, string] {
  const dot = ref.indexOf('.')
  if (dot < 0) return [ref, defaultPort]
  return [ref.slice(0, dot), ref.slice(dot + 1)]
}

/** Build a deterministic React Flow edge id from the canonical PortRefs. */
function makeEdgeId(from: string, to: string): string {
  return `${from}__${to}`
}

/** Map canonical GraphEdge → React Flow Edge */
export function toFlowEdge(ge: GraphEdge): Edge {
  const [source, sourceHandle] = parsePortRef(ge.from, DEFAULT_SOURCE_PORT)
  const [target, targetHandle] = parsePortRef(ge.to, DEFAULT_TARGET_PORT)
  const isData = sourceHandle === DATA_PORT
  const edge: Edge = {
    id: makeEdgeId(ge.from, ge.to),
    source,
    target,
    sourceHandle,
    targetHandle,
    type: isData ? 'data' : 'control',
    data: {
      // `kind` is preserved on edge data purely for canvas styling /
      // edge-component logic; it is NOT round-tripped as a separate canonical
      // field — the source PortRef port is the source of truth.
      kind: sourceHandle as EdgeKind,
    },
  }
  return edge
}

/** Map React Flow Node → canonical GraphNode */
export function fromFlowNode(n: Node): GraphNode {
  const data = n.data as { ref?: string; kwargs?: Record<string, unknown> } | undefined
  return {
    id: n.id,
    ref: data?.ref ?? '',
    kwargs: data?.kwargs ?? {},
    // Backend `_parse_node` does `int(pos_raw[0])` — round here so floats
    // emitted by React Flow during drag don't trip the `int()` coercion.
    pos: [Math.round(n.position.x), Math.round(n.position.y)],
  }
}

/** Map React Flow Edge → canonical GraphEdge */
export function fromFlowEdge(e: Edge): GraphEdge {
  const sourcePort = e.sourceHandle ?? DEFAULT_SOURCE_PORT
  const targetPort = e.targetHandle ?? DEFAULT_TARGET_PORT
  return {
    from: `${e.source}.${sourcePort}`,
    to: `${e.target}.${targetPort}`,
  }
}

/** Deserialize a canonical Graph JSON into React Flow nodes + edges */
export function deserializeGraph(graph: Graph): { nodes: Node[]; edges: Edge[] } {
  return {
    nodes: graph.nodes.map(toFlowNode),
    edges: graph.edges.map(toFlowEdge),
  }
}

/** Serialize React Flow nodes + edges back into canonical Graph JSON */
export function serializeGraph(
  meta: Omit<Graph, 'nodes' | 'edges'>,
  nodes: Node[],
  edges: Edge[],
): Graph {
  return {
    ...meta,
    nodes: nodes.map(fromFlowNode),
    edges: edges.map(fromFlowEdge),
  }
}

export function refToNodeType(ref: string): string {
  if (ref === 'control.branch') return 'branch'
  if (ref === 'control.foreach') return 'foreach'
  if (ref.startsWith('perception.')) return 'perception'
  if (ref.startsWith('command.')) return 'command'
  if (ref.startsWith('workflow.')) return 'workflow'
  return 'tool'
}
