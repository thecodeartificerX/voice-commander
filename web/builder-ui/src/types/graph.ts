/**
 * Edge "kind" semantics encoded into the source PortRef's port segment.
 *
 * The canonical wire format does NOT carry `kind` as a separate field — it is
 * folded into `from: "<node>.<port>"`. This alias is retained for use by the
 * serializer + canvas styling logic when classifying control vs. data edges.
 */
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

/**
 * Canonical graph node — mirrors `voice_commander.commands.graph.Node` and the
 * JSON shape produced/consumed by `graph_schema.serialise_graph` /
 * `_parse_node`. `pos` is a two-element `[x, y]` integer array; the backend
 * coerces with `int()` so callers must round any float coordinates before
 * serialising.
 */
export interface GraphNode {
  id: string
  ref: string
  kwargs?: Record<string, unknown>
  pos: [number, number]
}

/**
 * Canonical graph edge — mirrors `voice_commander.commands.graph.Edge` and the
 * JSON shape `{from, to}` produced by `graph_schema.serialise_graph`. Both
 * fields are PortRef strings of the form `"<node_id>.<port>"`. `kind` /
 * `sourceHandle` / `targetHandle` are React Flow internals and are NOT part
 * of the canonical wire — they are encoded inside the `from`/`to` PortRefs.
 */
export interface GraphEdge {
  from: string
  to: string
}

export type GraphKind = 'command' | 'workflow'

/**
 * Tool argument metadata as returned by the backend palette
 * (`describe_tool_for_builder`). `type` is a verbatim Python type string
 * (`"int"`, `"str"`, `"bool"`, `"float"`, `"str | None"`, etc.) — the
 * frontend uses it to pick an input widget and coerce values.
 */
export interface ToolArgMeta {
  type: string
  description?: string
  required?: boolean
}

/**
 * Schema describing a single tool/command/workflow/control/perception node.
 * `ref` is the canonical drag ref (e.g. `pipeline.wait`, `command.copy`,
 * `control.branch`, `perception.ocr`). The PropertiesPane looks up a node's
 * schema by its `ref` to render argument inputs.
 */
export interface ToolSchema {
  ref: string
  name: string
  description?: string
  args: Record<string, ToolArgMeta>
}

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
