import { memo } from 'react'
import { Handle, Position, type NodeProps } from 'reactflow'
import { cn } from '@/lib/cn'
import { useGraphStore } from '@/store/graphStore'

interface BranchNodeData {
  ref: string
  kwargs: Record<string, unknown>
}

export const BranchNode = memo(function BranchNode({
  id,
  selected,
}: NodeProps<BranchNodeData>) {
  const runStatus = useGraphStore((s) => s.runStatusByNodeId[id])
  return (
    <div
      className={cn(
        'relative flex items-center justify-center w-16 h-16',
        selected && 'drop-shadow-[0_0_6px_rgba(234,179,8,0.8)]',
        runStatus && `run-node-${runStatus}`,
      )}
    >
      {/* Diamond shape */}
      <div className="w-12 h-12 rotate-45 border-2 border-yellow-500 bg-gradient-to-br from-yellow-950 to-yellow-900" />
      <span className="absolute text-[9px] font-bold text-yellow-300">branch</span>

      <Handle
        type="target"
        position={Position.Left}
        id="cond"
        style={{ background: 'var(--vc-port-in)', left: 0 }}
      />
      <Handle
        type="source"
        position={Position.Top}
        id="true"
        style={{ background: 'var(--vc-port-true)' }}
      />
      <Handle
        type="source"
        position={Position.Bottom}
        id="false"
        style={{ background: 'var(--vc-port-false)' }}
      />
    </div>
  )
})
