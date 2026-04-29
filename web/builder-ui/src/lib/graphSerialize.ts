import type { Node, Edge } from 'reactflow'
import type { Graph, GraphNode, GraphEdge } from '@/types/graph'

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

/** Map canonical GraphNode → React Flow Node */
export function toFlowNode(gn: GraphNode): Node {
  return {
    id: gn.id,
    type: refToNodeType(gn.ref),
    position: gn.position ?? { x: 0, y: 0 },
    data: {
      ref: gn.ref,
      kwargs: gn.kwargs ?? {},
    },
  }
}

/** Default targetHandle for control-flow edges. */
const DEFAULT_TARGET_HANDLE = 'in'

/** Map canonical GraphEdge → React Flow Edge */
export function toFlowEdge(ge: GraphEdge): Edge {
  // ReactFlow needs a non-null `sourceHandle` to anchor the edge to the right
  // port. We backfill from `kind` so a control edge `kind: 'true'` lands on
  // the `true` source handle. The original (possibly absent) sourceHandle is
  // preserved on the edge data so `fromFlowEdge` can restore the canonical
  // JSON exactly.
  const backfilled = ge.sourceHandle ?? ge.kind
  const targetBackfilled = ge.targetHandle ?? DEFAULT_TARGET_HANDLE
  const edge: Edge = {
    id: ge.id,
    source: ge.source,
    target: ge.target,
    sourceHandle: backfilled,
    targetHandle: targetBackfilled,
    type: ge.kind === 'data' ? 'data' : 'control',
    data: {
      kind: ge.kind,
      data_field: ge.data_field,
      // Track what the canonical JSON actually contained so we can round-trip
      // without injecting fields that weren't there originally.
      _hasSourceHandle: ge.sourceHandle != null,
      _hasTargetHandle: ge.targetHandle != null,
    },
  }
  return edge
}

/** Map React Flow Node → canonical GraphNode */
export function fromFlowNode(n: Node): GraphNode {
  return {
    id: n.id,
    ref: (n.data as { ref: string }).ref,
    kwargs: (n.data as { kwargs?: Record<string, unknown> }).kwargs ?? {},
    position: n.position,
  }
}

/** Map React Flow Edge → canonical GraphEdge */
export function fromFlowEdge(e: Edge): GraphEdge {
  const data = e.data as
    | {
        kind?: string
        data_field?: string
        _hasSourceHandle?: boolean
        _hasTargetHandle?: boolean
      }
    | undefined
  const kind = (data?.kind ?? 'ok') as GraphEdge['kind']
  const edge: GraphEdge = {
    id: e.id,
    source: e.source,
    target: e.target,
    kind,
  }
  // Only persist sourceHandle when it differs from `kind` (the implicit
  // backfill in toFlowEdge); when they match, omit the field so JSON stays
  // minimal and round-trip is faithful.
  const hadSourceHandle = data?._hasSourceHandle === true
  if (hadSourceHandle && e.sourceHandle != null && e.sourceHandle !== kind) {
    edge.sourceHandle = e.sourceHandle
  }
  // Same logic for targetHandle vs the default ('in').
  const hadTargetHandle = data?._hasTargetHandle === true
  if (
    hadTargetHandle &&
    e.targetHandle != null &&
    e.targetHandle !== DEFAULT_TARGET_HANDLE
  ) {
    edge.targetHandle = e.targetHandle
  }
  if (data?.data_field != null) edge.data_field = data.data_field
  return edge
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

function refToNodeType(ref: string): string {
  if (ref === 'control.branch') return 'branch'
  if (ref === 'control.foreach') return 'foreach'
  if (ref.startsWith('perception.')) return 'perception'
  if (ref.startsWith('command.')) return 'command'
  if (ref.startsWith('workflow.')) return 'workflow'
  return 'tool'
}
