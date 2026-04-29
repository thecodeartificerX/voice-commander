import { apiFetch } from './client'

export async function apiGetPromptData(): Promise<{
  template_raw: string
  template_resolved: string
  placeholders: Record<string, string>
  tools: unknown[]
  tools_count: number
  model_id: string
  endpoint_url: string
}> {
  return apiFetch('/api/prompt')
}

export async function apiSavePromptTemplate(template: string): Promise<void> {
  await apiFetch('/api/prompt', { method: 'POST', body: JSON.stringify({ template }) })
}
