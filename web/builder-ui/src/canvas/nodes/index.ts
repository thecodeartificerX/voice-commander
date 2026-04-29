import type { NodeTypes } from 'reactflow'
import { ToolNode } from './ToolNode'
import { BranchNode } from './BranchNode'
import { ForeachNode } from './ForeachNode'
import { PerceptionNode } from './PerceptionNode'

export const nodeTypes: NodeTypes = {
  tool: ToolNode,
  command: ToolNode,
  workflow: ToolNode,
  branch: BranchNode,
  foreach: ForeachNode,
  perception: PerceptionNode,
}
