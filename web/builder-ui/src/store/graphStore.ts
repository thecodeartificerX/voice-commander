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
  /**
   * True when the graph has never been saved (created via `initBlank`).
   * The toolbar uses this to expose an editable name input and to gate
   * the rename action. Cleared on a successful `save()`.
   */
  draft: boolean
  /** Per-node run status overlay; not persisted, does NOT mark graph dirty. */
  runStatusByNodeId: Record<string, 'ok' | 'error' | 'skipped'>

  load(kind: GraphKind, name: string): Promise<void>
  /**
   * Initialise the store with a brand-new, unsaved graph. Used by the "+
   * New command" flow so the canvas opens blank instead of falling through
   * to `discoverFirstGraph()`. No API call is made — the graph only hits
   * the server on first `save()`.
   */
  initBlank(kind: GraphKind, name: string): void
  /**
   * Rename a draft graph. No-ops (with a console.warn) on saved graphs.
   */
  renameDraft(name: string): void
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

// Mirrors src/voice_commander/commands/graph_schema.py::CURRENT_SCHEMA_VERSION.
// Bump in lock-step with the backend constant when the schema evolves.
const BLANK_SCHEMA_VERSION = 1

function makeBlankGraphMeta(
  kind: GraphKind,
  name: string,
): Omit<Graph, 'nodes' | 'edges'> {
  return {
    schema_version: BLANK_SCHEMA_VERSION,
    name,
    kind,
    description: '',
    enabled: true,
    llm_visible: true,
    inputs: [],
  }
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
  draft: false,
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
      draft: false,
      runStatusByNodeId: {},
    })
  },

  initBlank(kind, name) {
    const meta = makeBlankGraphMeta(kind, name)
    set({
      graphId: name,
      graphKind: kind,
      graphMeta: meta,
      nodes: [],
      edges: [],
      selectedNodeId: null,
      // Mark dirty so Save is enabled immediately — the user usually wants
      // to rename + save, not edit the canvas first.
      dirty: true,
      llmVisible: meta.llm_visible,
      draft: true,
      runStatusByNodeId: {},
    })
  },

  renameDraft(name) {
    const state = get()
    if (!state.draft) {
      // Renaming a saved graph would orphan the old file on disk. Out of
      // scope for the new-graph flow.
      console.warn('renameDraft called on a non-draft graph; ignoring')
      return
    }
    if (!state.graphMeta) return
    set({
      graphId: name,
      graphMeta: { ...state.graphMeta, name },
      dirty: true,
    })
  },

  async save() {
    const { graphId, graphKind, graphMeta, nodes, edges } = get()
    if (!graphId || !graphKind || !graphMeta) return
    const canonical = serializeGraph(graphMeta, nodes, edges)
    await apiSaveGraph(graphId, canonical)
    set({ dirty: false, draft: false })
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
