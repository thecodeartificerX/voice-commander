import { memo } from 'react'
import { Handle, Position, type NodeProps } from 'reactflow'
import { cn } from '@/lib/cn'
import { useGraphStore } from '@/store/graphStore'

interface ForeachNodeData {
  ref: string
  kwargs: Record<string, unknown>
}

export const ForeachNode = memo(function ForeachNode({
  id,
  selected,
}: NodeProps<ForeachNodeData>) {
  const runStatus = useGraphStore((s) => s.runStatusByNodeId[id])
  return (
    <div
      className={cn(
        'min-w-[120px] rounded-lg border-2 border-cyan-600 bg-gradient-to-b from-cyan-950 to-cyan-900 px-3 py-2 shadow-md',
        selected && 'ring-2 ring-cyan-400 ring-offset-1 ring-offset-background',
        runStatus && `run-node-${runStatus}`,
      )}
    >
      <Handle
        type="target"
        position={Position.Left}
        id="list"
        style={{ background: 'var(--vc-port-in)' }}
      />
      <div className="text-xs font-semibold text-cyan-200 text-center">&#x27F3; foreach</div>
      <Handle
        type="source"
        position={Position.Right}
        id="item"
        style={{ background: 'var(--vc-port-item)', top: '35%' }}
      />
      <Handle
        type="source"
        position={Position.Right}
        id="after"
        style={{ background: 'var(--vc-port-after)', top: '65%' }}
      />
    </div>
  )
})
