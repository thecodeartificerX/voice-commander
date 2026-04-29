import { describe, it, expect } from 'vitest'
import {
  deserializeGraph,
  serializeGraph,
  fromFlowEdge,
  toFlowEdge,
  fromFlowNode,
  toFlowNode,
} from '@/lib/graphSerialize'
import type { Graph, GraphEdge, GraphNode } from '@/types/graph'

function meta(): Omit<Graph, 'nodes' | 'edges'> {
  return {
    schema_version: 1,
    name: 'test',
    kind: 'command',
    description: '',
    enabled: true,
    llm_visible: true,
    inputs: [],
  }
}

const CONTROL_GRAPH: Graph = {
  ...meta(),
  nodes: [
    { id: 'a', ref: 'shell.notify', kwargs: { msg: 'hi' }, pos: [0, 0] },
    { id: 'b', ref: 'shell.notify', kwargs: {}, pos: [100, 0] },
  ],
  edges: [
    { from: 'a.ok', to: 'b.in' },
    { from: 'a.error', to: 'b.in' },
  ],
}

const DATA_GRAPH: Graph = {
  ...meta(),
  nodes: [
    { id: 'a', ref: 'perception.clipboard', kwargs: {}, pos: [0, 0] },
    { id: 'b', ref: 'shell.notify', kwargs: {}, pos: [100, 0] },
  ],
  edges: [{ from: 'a.data', to: 'b.msg' }],
}

const MIXED_GRAPH: Graph = {
  ...meta(),
  nodes: [
    { id: 'a', ref: 'control.branch', kwargs: {}, pos: [0, 0] },
    { id: 'b', ref: 'shell.notify', kwargs: {}, pos: [100, 0] },
    { id: 'c', ref: 'shell.notify', kwargs: {}, pos: [100, 100] },
  ],
  edges: [
    { from: 'a.true', to: 'b.in' },
    { from: 'a.false', to: 'c.in' },
    { from: 'b.ok', to: 'c.in' },
    { from: 'a.data', to: 'b.cond' },
  ],
}

function roundTrip(g: Graph): Graph {
  const { nodes, edges } = deserializeGraph(g)
  const { nodes: gn, edges: ge } = serializeGraph(g, nodes, edges)
  return { ...g, nodes: gn, edges: ge }
}

describe('graphSerialize — F-M2 round-trip', () => {
  it('control-only graph round-trips deeply', () => {
    expect(roundTrip(CONTROL_GRAPH)).toEqual(CONTROL_GRAPH)
  })

  it('data-only graph round-trips deeply', () => {
    expect(roundTrip(DATA_GRAPH)).toEqual(DATA_GRAPH)
  })

  it('mixed control+data graph round-trips deeply', () => {
    expect(roundTrip(MIXED_GRAPH)).toEqual(MIXED_GRAPH)
  })

  it('round-trips a non-default-port edge: branch.true → next.in', () => {
    const canonical: GraphEdge = { from: 'branch.true', to: 'next.in' }
    const back = fromFlowEdge(toFlowEdge(canonical))
    expect(back).toEqual(canonical)
  })

  it('round-trips a default-port edge: a.ok → b.in', () => {
    const canonical: GraphEdge = { from: 'a.ok', to: 'b.in' }
    const back = fromFlowEdge(toFlowEdge(canonical))
    expect(back).toEqual(canonical)
  })

  it('classifies a `data` source port as a React Flow `data` edge type', () => {
    const e = toFlowEdge({ from: 'a.data', to: 'b.value' })
    expect(e.type).toBe('data')
    expect(e.sourceHandle).toBe('data')
    expect(e.targetHandle).toBe('value')
  })

  it('classifies a non-`data` source port as a React Flow `control` edge type', () => {
    const e = toFlowEdge({ from: 'a.ok', to: 'b.in' })
    expect(e.type).toBe('control')
  })

  it('rounds float node positions to ints when serialising', () => {
    const flow = {
      id: 'n1',
      type: 'tool',
      position: { x: 12.7, y: -3.2 },
      data: { ref: 'shell.notify', kwargs: {} },
    }
    const canonical = fromFlowNode(flow)
    expect(canonical.pos).toEqual([13, -3])
  })

  it('round-trips a node through toFlowNode → fromFlowNode', () => {
    const canonical: GraphNode = {
      id: 'n1',
      ref: 'shell.notify',
      kwargs: { msg: 'hi' },
      pos: [42, 99],
    }
    const back = fromFlowNode(toFlowNode(canonical))
    expect(back).toEqual(canonical)
  })

  it('produces a deterministic React Flow edge id from canonical PortRefs', () => {
    const e1 = toFlowEdge({ from: 'a.ok', to: 'b.in' })
    const e2 = toFlowEdge({ from: 'a.ok', to: 'b.in' })
    expect(e1.id).toBe(e2.id)
  })

  describe('Issue #95: SPA Builder JSON shape divergence from canonical schema', () => {
    it('backend → SPA → backend round-trip preserves canonical edge shape for control edges', () => {
      const backendGraph: Graph = {
        schema_version: 1,
        name: 'test',
        kind: 'command',
        description: 'Test graph',
        enabled: true,
        llm_visible: true,
        inputs: [],
        nodes: [
          { id: 'a', ref: 'shell.notify', kwargs: {}, pos: [0, 0] },
          { id: 'b', ref: 'shell.notify', kwargs: {}, pos: [100, 0] },
        ],
        edges: [{ from: 'a.ok', to: 'b.in' }],
      }

      const { nodes, edges } = deserializeGraph(backendGraph)
      const meta = {
        schema_version: backendGraph.schema_version,
        name: backendGraph.name,
        kind: backendGraph.kind,
        description: backendGraph.description,
        enabled: backendGraph.enabled,
        llm_visible: backendGraph.llm_visible,
        inputs: backendGraph.inputs,
      } as Omit<Graph, 'nodes' | 'edges'>
      const savedGraph = serializeGraph(meta, nodes, edges)

      expect(savedGraph.edges).toEqual(backendGraph.edges)
      expect(savedGraph.edges[0]).toEqual({ from: 'a.ok', to: 'b.in' })
    })

    it('backend → SPA → backend round-trip preserves canonical edge shape for data edges', () => {
      const backendGraph: Graph = {
        schema_version: 1,
        name: 'test',
        kind: 'command',
        description: 'Test graph',
        enabled: true,
        llm_visible: true,
        inputs: [],
        nodes: [
          { id: 'a', ref: 'perception.clipboard', kwargs: {}, pos: [0, 0] },
          { id: 'b', ref: 'shell.notify', kwargs: {}, pos: [100, 0] },
        ],
        edges: [{ from: 'a.data', to: 'b.msg' }],
      }

      const { nodes, edges } = deserializeGraph(backendGraph)
      const meta = {
        schema_version: backendGraph.schema_version,
        name: backendGraph.name,
        kind: backendGraph.kind,
        description: backendGraph.description,
        enabled: backendGraph.enabled,
        llm_visible: backendGraph.llm_visible,
        inputs: backendGraph.inputs,
      } as Omit<Graph, 'nodes' | 'edges'>
      const savedGraph = serializeGraph(meta, nodes, edges)

      expect(savedGraph.edges).toEqual(backendGraph.edges)
      expect(savedGraph.edges[0]).toEqual({ from: 'a.data', to: 'b.msg' })
    })

    it('backend → SPA → backend round-trip preserves edges with default ports', () => {
      const backendGraph: Graph = {
        schema_version: 1,
        name: 'test',
        kind: 'command',
        description: 'Test graph',
        enabled: true,
        llm_visible: true,
        inputs: [],
        nodes: [
          { id: 'x', ref: 'shell.notify', kwargs: {}, pos: [0, 0] },
          { id: 'y', ref: 'shell.notify', kwargs: {}, pos: [100, 0] },
        ],
        edges: [{ from: 'x.ok', to: 'y.in' }],
      }

      const { nodes, edges } = deserializeGraph(backendGraph)
      const meta = {
        schema_version: backendGraph.schema_version,
        name: backendGraph.name,
        kind: backendGraph.kind,
        description: backendGraph.description,
        enabled: backendGraph.enabled,
        llm_visible: backendGraph.llm_visible,
        inputs: backendGraph.inputs,
      } as Omit<Graph, 'nodes' | 'edges'>
      const savedGraph = serializeGraph(meta, nodes, edges)

      expect(savedGraph.edges).toEqual(backendGraph.edges)
    })
  })
})
