const BASE_URL = '' // relative — proxied to :8765 in dev; same-origin in prod

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  })
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`API ${init?.method ?? 'GET'} ${path} → ${res.status}: ${text}`)
  }
  return res.json() as Promise<T>
}
