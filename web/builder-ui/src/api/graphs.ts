import { apiFetch } from './client'
import type { Graph } from '@/types/graph'

export async function apiGetGraph(name: string): Promise<Graph> {
  return apiFetch<Graph>(`/graph/${encodeURIComponent(name)}`)
}

export async function apiSaveGraph(name: string, graph: Graph): Promise<void> {
  await apiFetch<{ ok: boolean }>(`/graph/${encodeURIComponent(name)}`, {
    method: 'POST',
    body: JSON.stringify(graph),
  })
}

export async function apiGetPalette(): Promise<unknown> {
  return apiFetch('/graph/palette')
}

export async function apiValidateGraph(graph: Graph): Promise<{ errors: unknown[] }> {
  return apiFetch('/graph/validate', { method: 'POST', body: JSON.stringify(graph) })
}
