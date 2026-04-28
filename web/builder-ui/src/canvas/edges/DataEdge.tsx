import { memo } from 'react'
import {
  BaseEdge,
  EdgeLabelRenderer,
  getSmoothStepPath,
  type EdgeProps,
} from 'reactflow'

interface DataEdgeData {
  sourceField?: string
  targetField?: string
}

export const DataEdge = memo(function DataEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
  markerEnd,
}: EdgeProps<DataEdgeData>) {
  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  })

  const label =
    data?.sourceField != null && data?.targetField != null
      ? `${data.sourceField} → ${data.targetField}`
      : undefined

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        {...(markerEnd != null ? { markerEnd } : {})}
        style={{ stroke: '#3b82f6', strokeWidth: 1.5, strokeDasharray: '4 3' }}
      />
      {label != null && (
        <EdgeLabelRenderer>
          <div
            style={{
              transform: `translate(-50%,-50%) translate(${labelX}px,${labelY}px)`,
              background: 'rgba(0,0,0,0.7)',
            }}
            className="absolute pointer-events-none text-[9px] text-blue-300 px-1 rounded"
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
})
