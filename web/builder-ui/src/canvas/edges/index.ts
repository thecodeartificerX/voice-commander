import type { EdgeTypes } from 'reactflow'
import { ControlEdge } from './ControlEdge'
import { DataEdge } from './DataEdge'

export const edgeTypes: EdgeTypes = {
  control: ControlEdge,
  data: DataEdge,
}
