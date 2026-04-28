import { memo } from 'react'
import { Handle, Position, type NodeProps } from 'reactflow'
import { cn } from '@/lib/cn'
import { useGraphStore } from '@/store/graphStore'

interface ToolNodeData {
  ref: string
  kwargs: Record<string, unknown>
  label?: string
}

function getNodeColors(ref: string): { bg: string; border: string; dot: string } {
  if (ref.startsWith('command.'))
    return { bg: 'from-blue-950 to-blue-900', border: 'border-blue-600', dot: 'bg-blue-400' }
  if (ref.startsWith('workflow.'))
    return {
      bg: 'from-violet-950 to-violet-900',
      border: 'border-violet-600',
      dot: 'bg-violet-400',
    }
  if (ref.startsWith('pipeline.'))
    return { bg: 'from-slate-800 to-slate-700', border: 'border-slate-500', dot: 'bg-slate-300' }
  return { bg: 'from-slate-800 to-slate-700', border: 'border-slate-500', dot: 'bg-slate-300' }
}

export const ToolNode = memo(function ToolNode({ id, data, selected }: NodeProps<ToolNodeData>) {
  const colors = getNodeColors(data.ref)
  const label = data.label ?? data.ref.split('.').pop() ?? data.ref
  const kwargsEntries = Object.entries(data.kwargs ?? {})
  const runStatus = useGraphStore((s) => s.runStatusByNodeId[id])

  return (
    <div
      className={cn(
        'min-w-[140px] rounded-md border bg-gradient-to-b px-3 py-2 shadow-md',
        colors.bg,
        colors.border,
        selected && 'ring-2 ring-blue-400 ring-offset-1 ring-offset-background',
        runStatus && `run-node-${runStatus}`,
      )}
    >
      <Handle
        type="target"
        position={Position.Left}
        id="in"
        style={{ background: 'var(--vc-port-in)' }}
      />

      <div className="flex items-center gap-1.5 mb-1">
        <span className={cn('h-2 w-2 rounded-full shrink-0', colors.dot)} />
        <span className="text-xs font-semibold text-gray-100 truncate">{label}</span>
      </div>

      {kwargsEntries.length > 0 && (
        <div className="space-y-0.5 mt-1">
          {kwargsEntries.map(([k, v]) => (
            <div key={k} className="flex items-center gap-1 text-[10px] text-gray-400">
              <span className="truncate max-w-[60px]">{k}:</span>
              <span className="truncate text-gray-300 max-w-[60px]">{String(v)}</span>
            </div>
          ))}
        </div>
      )}

      <Handle
        type="source"
        position={Position.Right}
        id="ok"
        style={{ background: 'var(--vc-port-ok)' }}
      />
      <Handle
        type="source"
        position={Position.Right}
        id="error"
        style={{ background: 'var(--vc-port-error)', top: '65%' }}
      />
    </div>
  )
})
