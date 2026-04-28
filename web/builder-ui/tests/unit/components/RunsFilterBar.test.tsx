import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { RunsFilterBar } from '@/runs/RunsFilterBar'
import { useRunsStore } from '@/store/runsStore'

describe('RunsFilterBar', () => {
  beforeEach(() => {
    useRunsStore.setState((s) => ({
      ...s,
      filters: { statuses: [], categories: [], query: '' },
    }))
  })

  it('renders all pills', () => {
    render(<RunsFilterBar />)
    expect(screen.getByText(/all/)).toBeTruthy()
    expect(screen.getByText(/ok/)).toBeTruthy()
    expect(screen.getByText(/miss/)).toBeTruthy()
  })

  it('toggles status filter on pill click', () => {
    render(<RunsFilterBar />)
    const okPill = screen.getByText(/✓ ok/)
    fireEvent.click(okPill)
    const { filters } = useRunsStore.getState()
    expect(filters.statuses).toContain('ok')
  })
})
