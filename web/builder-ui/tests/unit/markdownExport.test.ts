import { describe, it, expect } from 'vitest'
import { buildMarkdownExport } from '@/lib/markdownExport'
import type { RunDetail } from '@/types/run'

const FIXTURE_RUN: RunDetail = {
  run_id: 'a3f9bc1234567890',
  started_at: 1745846551,
  ended_at: 1745846552.2,
  duration_ms: 1200,
  transcript: 'open spotify and play discover weekly',
  status: 'ok',
  error_category: null,
  error_summary: null,
  step_count: 4,
  graph: null,
  daemon_pid: 18244,
  schema_version: 2,
  spans: [
    {
      span_id: 'root-span',
      run_id: 'a3f9bc1234567890',
      parent_span_id: null,
      type: 'run',
      name: 'run',
      started_at: 1745846551,
      ended_at: 1745846552.2,
      duration_ms: 1200,
      status: 'ok',
      attrs: {},
      output: null,
      error_type: null,
      error_msg: null,
      traceback: null,
      error_category: null,
    },
  ],
}

describe('buildMarkdownExport', () => {
  it('produces markdown with expected header', () => {
    const md = buildMarkdownExport(FIXTURE_RUN)
    expect(md).toContain('# Voice Commander run a3f9bc')
    expect(md).toContain('**Transcript:** "open spotify and play discover weekly"')
    expect(md).toContain('**Status:** ok')
    expect(md).toContain('**Daemon PID:** 18244')
  })

  it('includes span tree section', () => {
    const md = buildMarkdownExport(FIXTURE_RUN)
    expect(md).toContain('## Span tree')
    expect(md).toContain('✓ run')
  })

  it('does not include failure summary for ok runs', () => {
    const md = buildMarkdownExport(FIXTURE_RUN)
    expect(md).not.toContain('## Failure summary')
  })

  it('includes failure summary for error runs', () => {
    const errorRun: RunDetail = {
      ...FIXTURE_RUN,
      status: 'error',
      error_category: 'wiring',
      error_summary: "missing kwarg 'right'",
      spans: [
        {
          ...FIXTURE_RUN.spans[0]!,
          status: 'error',
          error_type: 'WiringError',
          error_msg: "missing kwarg 'right'",
          error_category: 'wiring',
        },
      ],
    }
    const md = buildMarkdownExport(errorRun)
    expect(md).toContain('## Failure summary')
    expect(md).toContain('wiring')
    expect(md).toContain('fix the graph')
  })
})
