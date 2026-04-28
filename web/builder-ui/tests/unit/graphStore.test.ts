import { describe, it, expect, beforeEach } from 'vitest'
import type { Node, Edge, NodeChange } from 'reactflow'
import { useGraphStore } from '@/store/graphStore'

const SAMPLE_NODES: Node[] = [
  {
    id: 'n1',
    type: 'tool',
    position: { x: 0, y: 0 },
    data: { ref: 'shell.notify', kwargs: { msg: 'hi' } },
  },
  {
    id: 'n2',
    type: 'tool',
    position: { x: 100, y: 0 },
    data: { ref: 'shell.notify', kwargs: {} },
  },
]

const SAMPLE_EDGES: Edge[] = []

function freshState() {
  useGraphStore.setState({
    graphId: 'test',
    graphKind: 'command',
    graphMeta: {
      schema_version: 1,
      name: 'test',
      kind: 'command',
      description: '',
      enabled: true,
      llm_visible: false,
      inputs: [],
    },
    nodes: SAMPLE_NODES.map((n) => ({ ...n })),
    edges: SAMPLE_EDGES,
    selectedNodeId: null,
    dirty: false,
    llmVisible: false,
    runStatusByNodeId: {},
  })
}

describe('graphStore — F-H2 dirty semantics', () => {
  beforeEach(freshState)

  it('does NOT mark dirty on `select` change', () => {
    const changes: NodeChange[] = [{ id: 'n1', type: 'select', selected: true }]
    useGraphStore.getState().applyNodeChanges(changes)
    expect(useGraphStore.getState().dirty).toBe(false)
  })

  it('does NOT mark dirty on `dimensions` change', () => {
    const changes: NodeChange[] = [
      { id: 'n1', type: 'dimensions', dimensions: { width: 100, height: 50 } },
    ]
    useGraphStore.getState().applyNodeChanges(changes)
    expect(useGraphStore.getState().dirty).toBe(false)
  })

  it('DOES mark dirty on a `position` change', () => {
    const changes: NodeChange[] = [
      { id: 'n1', type: 'position', position: { x: 50, y: 50 } },
    ]
    useGraphStore.getState().applyNodeChanges(changes)
    expect(useGraphStore.getState().dirty).toBe(true)
  })

  it('DOES mark dirty when a real edit accompanies a select change', () => {
    const changes: NodeChange[] = [
      { id: 'n1', type: 'select', selected: true },
      { id: 'n1', type: 'position', position: { x: 50, y: 50 } },
    ]
    useGraphStore.getState().applyNodeChanges(changes)
    expect(useGraphStore.getState().dirty).toBe(true)
  })

  it('setNodeRunStatus does NOT flip dirty', () => {
    useGraphStore.getState().setNodeRunStatus({ n1: 'ok', n2: 'error' })
    expect(useGraphStore.getState().dirty).toBe(false)
    expect(useGraphStore.getState().runStatusByNodeId).toEqual({ n1: 'ok', n2: 'error' })
  })

  it('selectNode does NOT flip dirty', () => {
    useGraphStore.getState().selectNode('n1')
    expect(useGraphStore.getState().dirty).toBe(false)
    expect(useGraphStore.getState().selectedNodeId).toBe('n1')
  })
})
