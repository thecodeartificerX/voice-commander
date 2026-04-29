export type EdgeKind = 'ok' | 'error' | 'true' | 'false' | 'data' | 'item' | 'after'

export interface GraphInput {
  name: string
  type: string
  required: boolean
  default?: unknown
}

export interface NodeArg {
  name: string
  type?: string
  required?: boolean
  default?: unknown
}

export interface GraphNode {
  id: string
  ref: string
  kwargs?: Record<string, unknown>
  position?: { x: number; y: number }
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  sourceHandle?: string
  targetHandle?: string
  kind: EdgeKind
  data_field?: string
}

export type GraphKind = 'command' | 'workflow'

export interface Graph {
  schema_version: number
  name: string
  kind: GraphKind
  description: string
  enabled: boolean
  llm_visible: boolean
  inputs: GraphInput[]
  nodes: GraphNode[]
  edges: GraphEdge[]
  timeout_ms?: number
}
