import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { useRunsStore } from '@/store/runsStore'
import type { RunSummary } from '@/types/run'
import * as runsApi from '@/api/runs'

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

  it('dedupes run.appended on identical run_id (F-C2)', () => {
    const r = makeRun({ run_id: 'dup-1', transcript: 'once' })
    useRunsStore.getState().onSseEvent('run.appended', r)
    useRunsStore.getState().onSseEvent('run.appended', { ...r })

    const matches = useRunsStore.getState().runs.filter((x) => x.run_id === 'dup-1')
    expect(matches).toHaveLength(1)
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

describe('runsStore.fetchOlder — F-C1', () => {
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

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('appends older runs to the tail in arrival order', async () => {
    const newer = makeRun({ run_id: 'a', started_at: 100, transcript: 'a' })
    const middle = makeRun({ run_id: 'b', started_at: 90, transcript: 'b' })
    useRunsStore.setState({ runs: [newer, middle] })

    const older1 = makeRun({ run_id: 'c', started_at: 80, transcript: 'c' })
    const older2 = makeRun({ run_id: 'd', started_at: 70, transcript: 'd' })
    vi.spyOn(runsApi, 'apiFetchOlderRuns').mockResolvedValueOnce([older1, older2])

    await useRunsStore.getState().fetchOlder()
    const ids = useRunsStore.getState().runs.map((r) => r.run_id)
    expect(ids).toEqual(['a', 'b', 'c', 'd'])
  })

  it('uses oldest run started_at as the `before` cursor', async () => {
    const newer = makeRun({ run_id: 'a', started_at: 100 })
    const oldest = makeRun({ run_id: 'b', started_at: 50 })
    useRunsStore.setState({ runs: [newer, oldest] })

    const spy = vi.spyOn(runsApi, 'apiFetchOlderRuns').mockResolvedValueOnce([])
    await useRunsStore.getState().fetchOlder()
    expect(spy).toHaveBeenCalledWith({ limit: 50, before: 50 })
  })

  it('dedupes overlapping run_ids when server returns rows already present', async () => {
    const a = makeRun({ run_id: 'a', started_at: 100 })
    const b = makeRun({ run_id: 'b', started_at: 90 })
    useRunsStore.setState({ runs: [a, b] })

    const dupB = makeRun({ run_id: 'b', started_at: 90 })
    const c = makeRun({ run_id: 'c', started_at: 80 })
    vi.spyOn(runsApi, 'apiFetchOlderRuns').mockResolvedValueOnce([dupB, c])

    await useRunsStore.getState().fetchOlder()
    const ids = useRunsStore.getState().runs.map((r) => r.run_id)
    expect(ids).toEqual(['a', 'b', 'c'])
  })

  it('truncation keeps the newest MAX_RUNS rows', async () => {
    // Pre-fill 200 newest with strictly decreasing started_at (newest first)
    const initial = Array.from({ length: 200 }, (_, i) =>
      makeRun({ run_id: `n${i}`, started_at: 1000 - i }),
    )
    useRunsStore.setState({ runs: initial })

    const older = Array.from({ length: 50 }, (_, i) =>
      makeRun({ run_id: `o${i}`, started_at: 500 - i }),
    )
    vi.spyOn(runsApi, 'apiFetchOlderRuns').mockResolvedValueOnce(older)

    await useRunsStore.getState().fetchOlder()
    const runs = useRunsStore.getState().runs
    expect(runs).toHaveLength(200)
    // Newest run preserved at head
    expect(runs[0]?.run_id).toBe('n0')
    // Older fetched rows did not displace newest
    expect(runs.some((r) => r.run_id === 'n0')).toBe(true)
  })
})
