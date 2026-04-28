import { useEffect } from 'react'
import { useRunsStore } from '@/store/runsStore'
import { useGraphStore } from '@/store/graphStore'

/**
 * Applies run-status CSS classes to canvas nodes via graphStore.setNodes so
 * that the single source of truth (graphStore) is never bypassed. Using
 * useReactFlow().setNodes would create a second divergent state tree.
 */
export function RunOverlay() {
  const { selectedRunId, detailById } = useRunsStore()
  const { setNodes } = useGraphStore()

  useEffect(() => {
    if (!selectedRunId) {
      setNodes((nodes) =>
        nodes.map((n) => ({
          ...n,
          className: (n.className ?? '').replace(/run-node-\w+/g, '').trim(),
        })),
      )
      return
    }

    const detail = detailById[selectedRunId]
    if (!detail) return

    const nodeStatus = new Map<string, 'ok' | 'error' | 'skipped'>()
    for (const span of detail.spans) {
      const nodeId = span.attrs['node_id']
      if (typeof nodeId === 'string') {
        const st: 'ok' | 'error' | 'skipped' =
          span.status === 'ok' ? 'ok' : span.status === 'error' ? 'error' : 'skipped'
        nodeStatus.set(nodeId, st)
      }
    }

    setNodes((nodes) =>
      nodes.map((n) => {
        const st = nodeStatus.get(n.id)
        const baseClass = (n.className ?? '').replace(/run-node-\w+/g, '').trim()
        const runClass = st != null ? `run-node-${st}` : ''
        return { ...n, className: [baseClass, runClass].filter(Boolean).join(' ') }
      }),
    )
  }, [selectedRunId, detailById, setNodes])

  return null
}
