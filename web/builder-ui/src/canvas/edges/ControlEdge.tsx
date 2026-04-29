import { memo } from 'react'
import {
  BaseEdge,
  EdgeLabelRenderer,
  getSmoothStepPath,
  type EdgeProps,
} from 'reactflow'

const KIND_COLORS: Record<string, string> = {
  ok: '#22c55e',
  error: '#ef4444',
  true: '#22c55e',
  false: '#f97316',
  default: '#3b82f6',
}

interface ControlEdgeData {
  kind: string
}

export const ControlEdge = memo(function ControlEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
  markerEnd,
}: EdgeProps<ControlEdgeData>) {
  const kind = data?.kind ?? 'ok'
  const color = KIND_COLORS[kind] ?? KIND_COLORS['default'] ?? '#3b82f6'

  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  })

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        {...(markerEnd != null ? { markerEnd } : {})}
        style={{ stroke: color, strokeWidth: 2 }}
      />
      {kind !== 'ok' && (
        <EdgeLabelRenderer>
          <div
            style={{
              transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
              color,
              background: 'rgba(0,0,0,0.6)',
            }}
            className="absolute pointer-events-none text-[9px] font-bold px-1 rounded"
          >
            {kind}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
})
