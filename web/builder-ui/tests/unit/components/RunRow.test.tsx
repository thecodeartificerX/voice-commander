import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { RunRow } from '@/runs/RunRow'
import { useRunsStore } from '@/store/runsStore'
import type { RunSummary } from '@/types/run'

const RUN: RunSummary = {
  run_id: 'abc123def456',
  started_at: Date.now() / 1000 - 120,
  ended_at: Date.now() / 1000 - 118,
  duration_ms: 1200,
  transcript: 'open spotify',
  status: 'ok',
  error_category: null,
  error_summary: null,
  step_count: 2,
  graph: null,
  daemon_pid: 999,
  schema_version: 2,
}

describe('RunRow', () => {
  it('renders transcript', () => {
    render(<RunRow run={RUN} />)
    expect(screen.getByText(/open spotify/)).toBeTruthy()
  })

  it('shows ok status icon', () => {
    render(<RunRow run={RUN} />)
    // green circle SVG exists — check aria or text
    const el = document.querySelector('svg')
    expect(el).toBeTruthy()
  })

  it('shows error line for error run', () => {
    const errRun: RunSummary = {
      ...RUN,
      status: 'error',
      error_category: 'wiring',
      error_summary: "missing kwarg 'right'",
    }
    render(<RunRow run={errRun} />)
    expect(screen.getByText(/wiring/)).toBeTruthy()
    expect(screen.getByText(/missing kwarg/)).toBeTruthy()
  })

  it('calls selectRun on click', () => {
    const selectRun = vi.fn()
    useRunsStore.setState((s) => ({ ...s, selectedRunId: null, selectRun }))
    render(<RunRow run={RUN} />)
    const row = document.querySelector('[role="option"]')!
    fireEvent.click(row)
    expect(selectRun).toHaveBeenCalledWith(RUN.run_id)
  })
})
