import { useEffect } from 'react'
import { useRunsStore } from '@/store/runsStore'
import { useGraphStore } from '@/store/graphStore'
import type { RunDetail } from '@/types/run'

/**
 * Computes per-node run status for the selected run and writes it to a
 * non-dirty visual-state field on graphStore (`runStatusByNodeId`). Nodes
 * read this map and apply the appropriate CSS class — keeping graph JSON
 * unaffected and the dirty flag clean.
 *
 * F-H1 fix: the effect depends only on the selected run's detail (or null),
 * so it does not fire when unrelated runs are inserted into `detailById`.
 */
export function RunOverlay() {
  const detail = useRunsStore((s) =>
    s.selectedRunId ? s.detailById[s.selectedRunId] ?? null : null,
  ) as RunDetail | null
  const setNodeRunStatus = useGraphStore((s) => s.setNodeRunStatus)

  useEffect(() => {
    if (!detail) {
      setNodeRunStatus({})
      return
    }
    const map: Record<string, 'ok' | 'error' | 'skipped'> = {}
    for (const span of detail.spans) {
      const nodeId = span.attrs['node_id']
      if (typeof nodeId === 'string') {
        const st: 'ok' | 'error' | 'skipped' =
          span.status === 'ok' ? 'ok' : span.status === 'error' ? 'error' : 'skipped'
        map[nodeId] = st
      }
    }
    setNodeRunStatus(map)
  }, [detail, setNodeRunStatus])

  return null
}
