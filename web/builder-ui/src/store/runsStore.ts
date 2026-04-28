import { create } from 'zustand'
import { toast } from 'sonner'
import type { RunSummary, RunDetail, ErrorCategory, RunStatus } from '@/types/run'
import { apiFetchRuns, apiFetchOlderRuns, apiFetchRunDetail } from '@/api/runs'

const MAX_RUNS = 200

interface RunFilter {
  statuses: RunStatus[]
  categories: Array<ErrorCategory | 'miss'>
  query: string
}

interface RunsState {
  runs: RunSummary[]
  detailById: Record<string, RunDetail>
  selectedRunId: string | null
  filters: RunFilter
  sseConnected: boolean
  loading: boolean
  error: string | null

  fetchInitial(): Promise<void>
  fetchOlder(): Promise<void>
  fetchDetail(runId: string): Promise<void>
  selectRun(id: string | null): void
  setFilter(partial: Partial<RunFilter>): void
  onSseEvent(type: string, data: unknown): void
  visibleRuns(): RunSummary[]
}

function applyFilter(runs: RunSummary[], filters: RunFilter): RunSummary[] {
  let result = runs
  if (filters.statuses.length > 0) {
    result = result.filter((r) => filters.statuses.includes(r.status))
  }
  if (filters.categories.length > 0) {
    result = result.filter((r) => {
      if (r.status === 'miss' && filters.categories.includes('miss')) return true
      if (r.error_category && filters.categories.includes(r.error_category)) return true
      return false
    })
  }
  if (filters.query) {
    const q = filters.query.toLowerCase()
    result = result.filter((r) => r.transcript.toLowerCase().includes(q))
  }
  return result
}

export const useRunsStore = create<RunsState>((set, get) => ({
  runs: [],
  detailById: {},
  selectedRunId: null,
  filters: { statuses: [], categories: [], query: '' },
  sseConnected: false,
  loading: false,
  error: null,

  async fetchInitial() {
    set({ loading: true, error: null })
    try {
      const runs = await apiFetchRuns({ limit: 50 })
      set({ runs, loading: false })
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      console.error('fetchInitial failed:', e)
      set({ loading: false, error: msg })
    }
  },

  async fetchOlder() {
    const { runs, loading } = get()
    if (loading) return
    // Oldest is the run with the smallest started_at; runs are stored newest-first
    // so the tail is oldest. We use that timestamp as the `before` cursor.
    const oldest = runs[runs.length - 1]
    if (!oldest) return
    try {
      const older = await apiFetchOlderRuns({ limit: 50, before: oldest.started_at })
      set((s) => {
        const seen = new Set(s.runs.map((r) => r.run_id))
        const deduped = older.filter((r) => !seen.has(r.run_id))
        // Older runs go to the TAIL (newest stays at the head)
        const merged = [...s.runs, ...deduped]
        // Truncation must not lose the newest — keep the head MAX_RUNS entries
        const capped = merged.length > MAX_RUNS ? merged.slice(0, MAX_RUNS) : merged
        return { runs: capped }
      })
    } catch (e) {
      console.error('fetchOlder failed:', e)
      toast.error('Failed to load older runs')
    }
  },

  async fetchDetail(runId) {
    if (get().detailById[runId]) return
    try {
      const detail = await apiFetchRunDetail(runId)
      set((s) => ({ detailById: { ...s.detailById, [runId]: detail } }))
    } catch (e) {
      console.error('fetchDetail failed:', e)
      toast.error('Failed to load run details')
    }
  },

  selectRun(id) {
    set({ selectedRunId: id })
    if (id) get().fetchDetail(id)
  },

  setFilter(partial) {
    set((s) => ({ filters: { ...s.filters, ...partial } }))
  },

  onSseEvent(type, data) {
    if (type === 'run.appended') {
      const run = data as RunSummary
      set((s) => {
        const updated = [run, ...s.runs]
        const capped = updated.length > MAX_RUNS ? updated.slice(0, MAX_RUNS) : updated
        return { runs: capped }
      })
    } else if (type === 'connection') {
      set({ sseConnected: (data as { connected: boolean }).connected })
    }
  },

  visibleRuns() {
    const { runs, filters } = get()
    const hasFilter =
      filters.statuses.length > 0 || filters.categories.length > 0 || filters.query
    return hasFilter ? applyFilter(runs, filters) : runs
  },
}))
