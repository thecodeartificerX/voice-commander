import { describe, it, expect } from 'vitest'
import {
  deserializeGraph,
  serializeGraph,
  fromFlowEdge,
  toFlowEdge,
} from '@/lib/graphSerialize'
import type { Graph } from '@/types/graph'

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
    { id: 'a', ref: 'shell.notify', kwargs: { msg: 'hi' }, position: { x: 0, y: 0 } },
    { id: 'b', ref: 'shell.notify', kwargs: {}, position: { x: 100, y: 0 } },
  ],
  edges: [
    { id: 'e1', source: 'a', target: 'b', kind: 'ok' },
    { id: 'e2', source: 'a', target: 'b', kind: 'error' },
  ],
}

const DATA_GRAPH: Graph = {
  ...meta(),
  nodes: [
    { id: 'a', ref: 'perception.clipboard', kwargs: {}, position: { x: 0, y: 0 } },
    { id: 'b', ref: 'shell.notify', kwargs: {}, position: { x: 100, y: 0 } },
  ],
  edges: [
    {
      id: 'd1',
      source: 'a',
      target: 'b',
      kind: 'data',
      data_field: 'msg',
    },
  ],
}

const MIXED_GRAPH: Graph = {
  ...meta(),
  nodes: [
    { id: 'a', ref: 'control.branch', kwargs: {}, position: { x: 0, y: 0 } },
    { id: 'b', ref: 'shell.notify', kwargs: {}, position: { x: 100, y: 0 } },
    { id: 'c', ref: 'shell.notify', kwargs: {}, position: { x: 100, y: 100 } },
  ],
  edges: [
    { id: 'e1', source: 'a', target: 'b', kind: 'true' },
    { id: 'e2', source: 'a', target: 'c', kind: 'false' },
    { id: 'e3', source: 'b', target: 'c', kind: 'ok' },
    {
      id: 'd1',
      source: 'a',
      target: 'b',
      kind: 'data',
      data_field: 'cond',
    },
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

  it('data-only graph round-trips deeply (data_field preserved)', () => {
    expect(roundTrip(DATA_GRAPH)).toEqual(DATA_GRAPH)
  })

  it('mixed control+data graph round-trips deeply', () => {
    expect(roundTrip(MIXED_GRAPH)).toEqual(MIXED_GRAPH)
  })

  it('does not inject sourceHandle when JSON had none and it would equal kind', () => {
    const e = toFlowEdge({
      id: 'x',
      source: 'a',
      target: 'b',
      kind: 'ok',
    })
    const back = fromFlowEdge(e)
    expect(back.sourceHandle).toBeUndefined()
    expect(back.targetHandle).toBeUndefined()
  })

  it('preserves an explicitly-set sourceHandle that differs from kind', () => {
    const e = toFlowEdge({
      id: 'x',
      source: 'a',
      target: 'b',
      kind: 'ok',
      sourceHandle: 'after',
    })
    const back = fromFlowEdge(e)
    expect(back.sourceHandle).toBe('after')
  })

  it('preserves data_field on data edges', () => {
    const e = toFlowEdge({
      id: 'x',
      source: 'a',
      target: 'b',
      kind: 'data',
      data_field: 'value',
    })
    const back = fromFlowEdge(e)
    expect(back.data_field).toBe('value')
  })
})
