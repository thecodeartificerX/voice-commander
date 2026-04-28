import { create } from 'zustand'
import type { Node, Edge } from 'reactflow'
import type { Graph, GraphKind } from '@/types/graph'
import { deserializeGraph, serializeGraph } from '@/lib/graphSerialize'
import { apiGetGraph, apiSaveGraph } from '@/api/graphs'

interface GraphState {
  graphId: string | null
  graphKind: GraphKind | null
  graphMeta: Omit<Graph, 'nodes' | 'edges'> | null
  nodes: Node[]
  edges: Edge[]
  selectedNodeId: string | null
  dirty: boolean
  llmVisible: boolean

  load(kind: GraphKind, name: string): Promise<void>
  save(): Promise<void>
  setNodes(updater: Node[] | ((nodes: Node[]) => Node[])): void
  setEdges(updater: Edge[] | ((edges: Edge[]) => Edge[])): void
  selectNode(id: string | null): void
  toggleLlmVisible(): void
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

  selectNode(id) {
    set({ selectedNodeId: id })
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
