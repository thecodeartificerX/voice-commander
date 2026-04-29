import { describe, it, expect, beforeEach, vi } from 'vitest'
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

describe('graphStore — draft / new-graph flow', () => {
  beforeEach(() => {
    useGraphStore.setState({
      graphId: null,
      graphKind: null,
      graphMeta: null,
      nodes: [],
      edges: [],
      selectedNodeId: null,
      dirty: false,
      llmVisible: false,
      draft: false,
      runStatusByNodeId: {},
    })
  })

  it('initBlank populates a draft graph with empty nodes/edges', () => {
    useGraphStore.getState().initBlank('command', 'untitled_command_abcd')
    const s = useGraphStore.getState()
    expect(s.graphId).toBe('untitled_command_abcd')
    expect(s.graphKind).toBe('command')
    expect(s.nodes).toEqual([])
    expect(s.edges).toEqual([])
    expect(s.draft).toBe(true)
    // Save should be enabled immediately so the user can pick a name + save.
    expect(s.dirty).toBe(true)
    expect(s.graphMeta?.schema_version).toBe(1)
    expect(s.graphMeta?.kind).toBe('command')
    expect(s.graphMeta?.llm_visible).toBe(true)
  })

  it('initBlank for workflow defaults llm_visible=true (matches backend)', () => {
    useGraphStore.getState().initBlank('workflow', 'untitled_workflow_x')
    expect(useGraphStore.getState().graphKind).toBe('workflow')
    expect(useGraphStore.getState().graphMeta?.llm_visible).toBe(true)
  })

  it('renameDraft updates name on a draft graph', () => {
    useGraphStore.getState().initBlank('command', 'untitled_command_abcd')
    useGraphStore.getState().renameDraft('open_my_app')
    const s = useGraphStore.getState()
    expect(s.graphId).toBe('open_my_app')
    expect(s.graphMeta?.name).toBe('open_my_app')
    expect(s.draft).toBe(true)
    expect(s.dirty).toBe(true)
  })

  it('renameDraft no-ops on a saved (non-draft) graph', () => {
    useGraphStore.setState({
      graphId: 'existing_command',
      graphKind: 'command',
      graphMeta: {
        schema_version: 1,
        name: 'existing_command',
        kind: 'command',
        description: '',
        enabled: true,
        llm_visible: true,
        inputs: [],
      },
      nodes: [],
      edges: [],
      selectedNodeId: null,
      dirty: false,
      llmVisible: true,
      draft: false,
      runStatusByNodeId: {},
    })
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    useGraphStore.getState().renameDraft('renamed')
    expect(useGraphStore.getState().graphId).toBe('existing_command')
    expect(warn).toHaveBeenCalled()
    warn.mockRestore()
  })

  it('save() clears draft + dirty after a successful API round-trip', async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ ok: true, name: 'untitled_command_abcd', version: 1 }), {
        status: 200,
      }),
    )
    const originalFetch = globalThis.fetch
    globalThis.fetch = fetchMock as unknown as typeof fetch
    try {
      useGraphStore.getState().initBlank('command', 'untitled_command_abcd')
      await useGraphStore.getState().save()
      const s = useGraphStore.getState()
      expect(s.draft).toBe(false)
      expect(s.dirty).toBe(false)
    } finally {
      globalThis.fetch = originalFetch
    }
  })
})

describe('graphStore — Issue #95: load() must strip nodes/edges from graphMeta', () => {
  beforeEach(() => {
    useGraphStore.setState({
      graphId: null,
      graphKind: null,
      graphMeta: null,
      nodes: [],
      edges: [],
      selectedNodeId: null,
      dirty: false,
      llmVisible: false,
      draft: false,
      runStatusByNodeId: {},
    })
  })

  it('graphMeta does not contain nodes or edges after load()', async () => {
    const backendGraph = {
      schema_version: 1,
      name: 'my_cmd',
      kind: 'command',
      description: '',
      enabled: true,
      llm_visible: true,
      inputs: [],
      nodes: [{ id: 'a', ref: 'shell.notify', kwargs: {}, pos: [0, 0] }],
      edges: [{ from: 'a.ok', to: 'a.in' }],
    }
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify(backendGraph), { status: 200 }),
    )
    const originalFetch = globalThis.fetch
    globalThis.fetch = fetchMock as unknown as typeof fetch
    try {
      await useGraphStore.getState().load('command', 'my_cmd')
      const { graphMeta } = useGraphStore.getState()
      expect(graphMeta).not.toHaveProperty('nodes')
      expect(graphMeta).not.toHaveProperty('edges')
      expect(graphMeta?.name).toBe('my_cmd')
      expect(graphMeta?.schema_version).toBe(1)
    } finally {
      globalThis.fetch = originalFetch
    }
  })

  it('nodes and edges in store come from deserializeGraph, not graphMeta', async () => {
    const backendGraph = {
      schema_version: 1,
      name: 'my_cmd',
      kind: 'command',
      description: '',
      enabled: true,
      llm_visible: true,
      inputs: [],
      nodes: [{ id: 'n1', ref: 'shell.notify', kwargs: {}, pos: [10, 20] }],
      edges: [{ from: 'n1.ok', to: 'n1.in' }],
    }
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify(backendGraph), { status: 200 }),
    )
    const originalFetch = globalThis.fetch
    globalThis.fetch = fetchMock as unknown as typeof fetch
    try {
      await useGraphStore.getState().load('command', 'my_cmd')
      const { nodes, graphMeta } = useGraphStore.getState()

      // React Flow nodes are deserialized (not backend format)
      expect(nodes).toHaveLength(1)
      expect(nodes[0].id).toBe('n1')
      expect(nodes[0]).toHaveProperty('position') // React Flow shape
      expect(nodes[0]).not.toHaveProperty('pos') // NOT backend shape

      // graphMeta has no contamination
      expect(Object.keys(graphMeta ?? {})).not.toContain('nodes')
      expect(Object.keys(graphMeta ?? {})).not.toContain('edges')
    } finally {
      globalThis.fetch = originalFetch
    }
  })
})
