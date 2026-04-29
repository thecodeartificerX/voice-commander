import { create } from 'zustand'
import type { Node, Edge, NodeChange, EdgeChange } from 'reactflow'
import { applyNodeChanges, applyEdgeChanges } from 'reactflow'
import type { Graph, GraphKind } from '@/types/graph'
import { deserializeGraph, serializeGraph } from '@/lib/graphSerialize'
import { apiGetGraph, apiSaveGraph } from '@/api/graphs'

/**
 * Change types that should NOT mark the graph dirty. These are transient
 * UI states (selection, dimension measurement after layout) reported by
 * React Flow and have no effect on the persisted graph JSON.
 */
const NON_DIRTY_CHANGE_TYPES = new Set<string>(['select', 'dimensions'])

function changesAreDirty<T extends { type: string }>(changes: readonly T[]): boolean {
  return changes.some((c) => !NON_DIRTY_CHANGE_TYPES.has(c.type))
}

interface GraphState {
  graphId: string | null
  graphKind: GraphKind | null
  graphMeta: Omit<Graph, 'nodes' | 'edges'> | null
  nodes: Node[]
  edges: Edge[]
  selectedNodeId: string | null
  dirty: boolean
  llmVisible: boolean
  /** Per-node run status overlay; not persisted, does NOT mark graph dirty. */
  runStatusByNodeId: Record<string, 'ok' | 'error' | 'skipped'>

  load(kind: GraphKind, name: string): Promise<void>
  save(): Promise<void>
  setNodes(updater: Node[] | ((nodes: Node[]) => Node[])): void
  setEdges(updater: Edge[] | ((edges: Edge[]) => Edge[])): void
  /**
   * Apply React Flow `NodeChange` events. Only marks the graph dirty when
   * a change is an actual edit (position, add, remove, data) — transient
   * select/dimensions changes are applied without flipping `dirty`.
   */
  applyNodeChanges(changes: NodeChange[]): void
  applyEdgeChanges(changes: EdgeChange[]): void
  selectNode(id: string | null): void
  toggleLlmVisible(): void
  /** Set the run-status overlay; never marks the graph dirty. */
  setNodeRunStatus(map: Record<string, 'ok' | 'error' | 'skipped'>): void
}

export const useGraphStore = create<GraphState>((set, get) => ({
  graphId: null,
  graphKind: null,
  graphMeta: null,
  nodes: [],
  edges: [],
  selectedNodeId: null,
  dirty: false,
  llmVisible: false,
  runStatusByNodeId: {},

  async load(kind, name) {
    const graph = await apiGetGraph(name)
    const { nodes, edges } = deserializeGraph(graph)
    set({
      graphId: name,
      graphKind: kind,
      graphMeta: { ...graph },
      nodes,
      edges,
      selectedNodeId: null,
      dirty: false,
      llmVisible: graph.llm_visible,
      runStatusByNodeId: {},
    })
  },

  async save() {
    const { graphId, graphKind, graphMeta, nodes, edges } = get()
    if (!graphId || !graphKind || !graphMeta) return
    const canonical = serializeGraph(graphMeta, nodes, edges)
    await apiSaveGraph(graphId, canonical)
    set({ dirty: false })
  },

  setNodes(updater) {
    set((s) => ({
      nodes: typeof updater === 'function' ? updater(s.nodes) : updater,
      dirty: true,
    }))
  },

  setEdges(updater) {
    set((s) => ({
      edges: typeof updater === 'function' ? updater(s.edges) : updater,
      dirty: true,
    }))
  },

  applyNodeChanges(changes) {
    set((s) => {
      const next = applyNodeChanges(changes, s.nodes)
      const dirty = s.dirty || changesAreDirty(changes)
      return { nodes: next, dirty }
    })
  },

  applyEdgeChanges(changes) {
    set((s) => {
      const next = applyEdgeChanges(changes, s.edges)
      const dirty = s.dirty || changesAreDirty(changes)
      return { edges: next, dirty }
    })
  },

  selectNode(id) {
    set({ selectedNodeId: id })
  },

  setNodeRunStatus(map) {
    // Visual-only state: never flip `dirty`. Object identity is preserved
    // when the new map is shallow-equal to the previous to avoid unnecessary
    // re-renders.
    set((s) => {
      const prev = s.runStatusByNodeId
      const prevKeys = Object.keys(prev)
      const nextKeys = Object.keys(map)
      if (
        prevKeys.length === nextKeys.length &&
        nextKeys.every((k) => prev[k] === map[k])
      ) {
        return s
      }
      return { runStatusByNodeId: map }
    })
  },

  toggleLlmVisible() {
    set((s) => {
      const next = !s.llmVisible
      return {
        llmVisible: next,
        graphMeta: s.graphMeta ? { ...s.graphMeta, llm_visible: next } : s.graphMeta,
        dirty: true,
      }
    })
  },
}))
