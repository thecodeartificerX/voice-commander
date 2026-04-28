import { memo } from 'react'
import { Handle, Position, type NodeProps } from 'reactflow'
import { Eye } from 'lucide-react'
import { cn } from '@/lib/cn'

interface PerceptionNodeData {
  ref: string
  kwargs: Record<string, unknown>
}

const PERCEPTION_LABELS: Record<string, string> = {
  'perception.ocr': 'OCR region',
  'perception.clipboard': 'Read clipboard',
  'perception.window': 'Window title',
  'perception.cursor': 'Cursor pos',
}

export const PerceptionNode = memo(function PerceptionNode({
  data,
  selected,
}: NodeProps<PerceptionNodeData>) {
  const label = PERCEPTION_LABELS[data.ref] ?? data.ref.split('.').pop() ?? data.ref

  return (
    <div
      className={cn(
        'min-w-[130px] rounded-md border border-emerald-600 bg-gradient-to-b from-emerald-950 to-emerald-900 px-3 py-2 shadow-md',
        selected && 'ring-2 ring-emerald-400 ring-offset-1 ring-offset-background',
      )}
    >
      <Handle
        type="target"
        position={Position.Left}
        id="in"
        style={{ background: 'var(--vc-port-in)' }}
      />
      <div className="flex items-center gap-1.5">
        <Eye className="h-3 w-3 text-emerald-400 shrink-0" />
        <span className="text-xs font-semibold text-emerald-200 truncate">{label}</span>
      </div>
      <Handle
        type="source"
        position={Position.Right}
        id="ok"
        style={{ background: 'var(--vc-port-ok)' }}
      />
    </div>
  )
})
