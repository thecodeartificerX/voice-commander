import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SpanTree } from '@/runs/SpanTree'
import type { SpanRecord } from '@/types/run'

const SPANS: SpanRecord[] = [
  {
    span_id: 'root',
    run_id: 'r1',
    parent_span_id: null,
    type: 'run',
    name: 'run',
    started_at: 1000,
    ended_at: 1001.2,
    duration_ms: 1200,
    status: 'ok',
    attrs: {},
    output: null,
    error_type: null,
    error_msg: null,
    traceback: null,
    error_category: null,
  },
  {
    span_id: 'child',
    run_id: 'r1',
    parent_span_id: 'root',
    type: 'tool_call',
    name: 'focus',
    started_at: 1000.1,
    ended_at: 1000.2,
    duration_ms: 100,
    status: 'error',
    attrs: { node_id: 'n1' },
    output: null,
    error_type: 'WiringError',
    error_msg: "missing kwarg 'target'",
    traceback: null,
    error_category: 'wiring',
  },
]

describe('SpanTree', () => {
  it('renders root span name', () => {
    render(<SpanTree spans={SPANS} />)
    expect(screen.getByText('run')).toBeTruthy()
  })

  it('shows error span details', () => {
    render(<SpanTree spans={SPANS} />)
    expect(screen.getByText(/WiringError/)).toBeTruthy()
    expect(screen.getByText(/missing kwarg/)).toBeTruthy()
  })

  it('shows empty state when no spans', () => {
    render(<SpanTree spans={[]} />)
    expect(screen.getByText(/No spans recorded/)).toBeTruthy()
  })
})
