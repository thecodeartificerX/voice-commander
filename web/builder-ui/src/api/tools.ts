import { apiFetch } from './client'

export async function apiGetTools(): Promise<unknown[]> {
  const res = await apiFetch<{ pipeline: unknown[]; commands: unknown[]; workflows: unknown[] }>(
    '/graph/palette',
  )
  return [...(res.pipeline ?? []), ...(res.commands ?? []), ...(res.workflows ?? [])]
}
