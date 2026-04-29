import { apiFetch } from './client'
import type { RunSummary, RunDetail } from '@/types/run'

interface ListRunsParams {
  limit?: number
  status?: string
  category?: string
  before?: number
}

export async function apiFetchRuns(params: ListRunsParams): Promise<RunSummary[]> {
  const q = new URLSearchParams()
  if (params.limit !== undefined) q.set('limit', String(params.limit))
  if (params.status) q.set('status', params.status)
  if (params.category) q.set('category', params.category)
  const res = await apiFetch<{ runs: RunSummary[] }>(`/api/runs?${q}`)
  return res.runs
}

export async function apiFetchOlderRuns(params: {
  limit: number
  before: number
}): Promise<RunSummary[]> {
  const q = new URLSearchParams({
    limit: String(params.limit),
    before: new Date(params.before * 1000).toISOString(),
  })
  const res = await apiFetch<{ runs: RunSummary[] }>(`/api/runs?${q}`)
  return res.runs
}

export async function apiFetchRunDetail(runId: string): Promise<RunDetail> {
  return apiFetch<RunDetail>(`/api/runs/${encodeURIComponent(runId)}`)
}

export async function apiFetchExportMd(runId: string): Promise<string> {
  const res = await fetch(`/api/runs/${encodeURIComponent(runId)}/export.md`)
  if (!res.ok) throw new Error(`export.md → ${res.status}`)
  return res.text()
}
