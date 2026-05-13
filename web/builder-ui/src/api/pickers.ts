import { apiFetch } from './client'

export interface WindowEntry {
  hwnd: number
  pid: number
  proc_name: string
  title: string
}

export interface AppEntry {
  display: string
  token: string
}

export async function apiGetWindows(): Promise<WindowEntry[]> {
  return apiFetch<WindowEntry[]>('/windows/active')
}

export async function apiGetApps(): Promise<AppEntry[]> {
  return apiFetch<AppEntry[]>('/apps/installed')
}
