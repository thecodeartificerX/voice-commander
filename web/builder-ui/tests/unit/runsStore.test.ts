import { describe, it, expect, beforeEach } from 'vitest'
import { useRunsStore } from '@/store/runsStore'
import type { RunSummary } from '@/types/run'

function makeRun(overrides: Partial<RunSummary> = {}): RunSummary {
  return {
    run_id: Math.random().toString(36).slice(2),
    started_at: Date.now() / 1000,
    ended_at: null,
    duration_ms: 500,
    transcript: 'test command',
    status: 'ok',
    error_category: null,
    error_summary: null,
    step_count: 1,
    graph: null,
    daemon_pid: 1234,
    schema_version: 2,
    ...overrides,
  }
}

describe('runsStore', () => {
  beforeEach(() => {
    useRunsStore.setState({
      runs: [],
      detailById: {},
      selectedRunId: null,
      filters: { statuses: [], categories: [], query: '' },
      sseConnected: false,
      loading: false,
    })
  })

  it('prepends run on run.appended SSE event', () => {
    const existing = makeRun({ transcript: 'first' })
    useRunsStore.setState({ runs: [existing] })

    const newRun = makeRun({ transcript: 'second' })
    useRunsStore.getState().onSseEvent('run.appended', newRun)

    const { runs } = useRunsStore.getState()
    expect(runs[0]?.transcript).toBe('second')
    expect(runs[1]?.transcript).toBe('first')
  })

  it('caps runs at 200', () => {
    const runs = Array.from({ length: 200 }, () => makeRun())
    useRunsStore.setState({ runs })

    const newRun = makeRun()
    useRunsStore.getState().onSseEvent('run.appended', newRun)

    expect(useRunsStore.getState().runs).toHaveLength(200)
    expect(useRunsStore.getState().runs[0]?.run_id).toBe(newRun.run_id)
  })

  it('filters by status', () => {
    const ok = makeRun({ status: 'ok' })
    const err = makeRun({ status: 'error', error_category: 'program' })
    useRunsStore.setState({ runs: [ok, err], filters: { statuses: ['ok'], categories: [], query: '' } })

    const visible = useRunsStore.getState().visibleRuns()
    expect(visible).toHaveLength(1)
    expect(visible[0]?.status).toBe('ok')
  })

  it('filters by transcript query', () => {
    const spotify = makeRun({ transcript: 'open spotify' })
    const copy = makeRun({ transcript: 'copy text' })
    useRunsStore.setState({ runs: [spotify, copy], filters: { statuses: [], categories: [], query: 'spotify' } })

    const visible = useRunsStore.getState().visibleRuns()
    expect(visible).toHaveLength(1)
    expect(visible[0]?.transcript).toBe('open spotify')
  })
})
