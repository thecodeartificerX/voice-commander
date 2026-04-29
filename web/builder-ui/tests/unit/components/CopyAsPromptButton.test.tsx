import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, fireEvent, waitFor } from '@testing-library/react'
import { CopyAsPromptButton } from '@/runs/CopyAsPromptButton'
import * as runsApi from '@/api/runs'
import type { RunDetail } from '@/types/run'

vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}))

const RUN: RunDetail = {
  run_id: 'abc123',
  started_at: 1745846551,
  ended_at: 1745846552.2,
  duration_ms: 1200,
  transcript: 'open spotify',
  status: 'ok',
  error_category: null,
  error_summary: null,
  step_count: 1,
  graph: null,
  daemon_pid: 18244,
  schema_version: 2,
  spans: [],
}

describe('CopyAsPromptButton — F-M3 server export source', () => {
  let writeText: ReturnType<typeof vi.fn>

  beforeEach(() => {
    writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('fetches markdown from /api/runs/{id}/export.md and copies it', async () => {
    const spy = vi
      .spyOn(runsApi, 'apiFetchExportMd')
      .mockResolvedValueOnce('# server-rendered markdown')

    const { getByRole } = render(<CopyAsPromptButton run={RUN} />)
    fireEvent.click(getByRole('button'))

    await waitFor(() => {
      expect(spy).toHaveBeenCalledWith('abc123')
      expect(writeText).toHaveBeenCalledWith('# server-rendered markdown')
    })
  })

  it('falls back to local buildMarkdownExport on network failure', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.spyOn(runsApi, 'apiFetchExportMd').mockRejectedValueOnce(new Error('offline'))

    const { getByRole } = render(<CopyAsPromptButton run={RUN} />)
    fireEvent.click(getByRole('button'))

    await waitFor(() => {
      expect(writeText).toHaveBeenCalled()
      const arg = writeText.mock.calls[0]?.[0] as string
      expect(arg).toContain('# Voice Commander run abc123')
    })
  })
})
