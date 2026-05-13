import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { Input } from '@/components/ui/input'
import { apiGetApps, apiGetWindows, type AppEntry, type WindowEntry } from '@/api/pickers'

interface PickerRow {
  /** Value stored into the kwarg when this row is picked. */
  stored: string
  /** Primary label rendered in the row (large). */
  primary: string
  /** Optional secondary label rendered under primary (small, dimmer). */
  secondary?: string
  /** Text used for substring filtering — primary + secondary concatenated. */
  filterKey: string
}

interface TargetPickerProps {
  kind: 'window' | 'app'
  value: string
  onChange: (next: string) => void
}

function rowsFromWindows(entries: WindowEntry[]): PickerRow[] {
  return entries.map((w) => ({
    stored: w.proc_name || w.title,
    primary: w.title,
    secondary: w.proc_name,
    filterKey: `${w.title} ${w.proc_name}`.toLowerCase(),
  }))
}

function rowsFromApps(entries: AppEntry[]): PickerRow[] {
  return entries.map((a) => ({
    stored: a.display,
    primary: a.display,
    filterKey: a.display.toLowerCase(),
  }))
}

/**
 * Generic picker for focus.target / open.target. Shows a button with the
 * current value; clicking expands an inline panel with a search box and
 * scrollable list. Selection stores the canonical id (process name for
 * windows, display name for apps) — the runtime resolver matches that exact
 * value against the same enumeration source (SSOT).
 *
 * A "type" toggle escapes to a free-text input for power users who want to
 * hand-author values the picker can't show (e.g. a window-title fragment to
 * disambiguate two windows of the same process).
 */
export function TargetPicker({ kind, value, onChange }: TargetPickerProps) {
  const [open, setOpen] = useState(false)
  const [typeMode, setTypeMode] = useState(false)
  const [rows, setRows] = useState<PickerRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const containerRef = useRef<HTMLDivElement | null>(null)
  const searchRef = useRef<HTMLInputElement | null>(null)

  const refresh = useCallback(async () => {
    setError(null)
    try {
      if (kind === 'window') {
        setRows(rowsFromWindows(await apiGetWindows()))
      } else {
        setRows(rowsFromApps(await apiGetApps()))
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(msg)
      setRows([])
    }
  }, [kind])

  // Open → fetch fresh list. Window list is a live snapshot so we never
  // cache; app list is daemon-cached on the backend so the second fetch is
  // cheap.
  useEffect(() => {
    if (!open) return
    void refresh()
    queueMicrotask(() => searchRef.current?.focus())
  }, [open, refresh])

  // Click-away to close.
  useEffect(() => {
    if (!open) return
    const onClickAway = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    window.addEventListener('mousedown', onClickAway, true)
    return () => window.removeEventListener('mousedown', onClickAway, true)
  }, [open])

  // Esc to close.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  const filtered = useMemo(() => {
    if (!rows) return []
    const q = query.trim().toLowerCase()
    if (!q) return rows.slice(0, 200)
    return rows.filter((r) => r.filterKey.includes(q)).slice(0, 200)
  }, [rows, query])

  const placeholder = kind === 'window' ? 'Pick window…' : 'Pick app…'

  if (typeMode) {
    return (
      <div className="flex flex-col gap-1">
        <Input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={kind === 'window' ? "e.g. 'comet.exe'" : "e.g. 'Spotify'"}
          className="h-6 text-xs font-mono"
        />
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setTypeMode(false)}
            className="h-5 rounded border border-border px-2 text-[10px] hover:bg-accent"
            title="Switch to picker"
          >
            list
          </button>
        </div>
      </div>
    )
  }

  return (
    <div ref={containerRef} className="relative flex flex-col gap-1">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={
          'h-6 w-full rounded border px-2 text-xs font-mono text-left ' +
          (open
            ? 'border-amber-400 bg-amber-950/30'
            : 'border-border bg-background hover:bg-accent')
        }
        title={kind === 'window' ? 'Pick a visible window' : 'Pick an installed app'}
      >
        {value || <span className="text-muted-foreground">{placeholder}</span>}
      </button>
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={() => setTypeMode(true)}
          className="h-5 rounded border border-border px-2 text-[10px] hover:bg-accent"
          title="Type a value manually"
        >
          type
        </button>
        {value ? (
          <button
            type="button"
            onClick={() => onChange('')}
            className="h-5 rounded border border-border px-2 text-[10px] hover:bg-accent"
            title="Clear value"
          >
            clear
          </button>
        ) : null}
      </div>
      {open ? (
        <div
          className="absolute left-0 top-[28px] z-50 w-[280px] rounded border border-border bg-popover shadow-lg"
          onMouseDown={(e) => e.stopPropagation()}
        >
          <div className="p-1.5 border-b border-border flex items-center gap-1">
            <Input
              ref={searchRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="filter…"
              className="h-6 text-xs font-mono flex-1"
            />
            <button
              type="button"
              onClick={() => void refresh()}
              className="h-6 rounded border border-border px-2 text-[10px] hover:bg-accent"
              title="Refresh list"
            >
              ↻
            </button>
          </div>
          <div className="max-h-[280px] overflow-y-auto">
            {rows == null ? (
              <div className="px-2 py-3 text-[11px] text-muted-foreground">loading…</div>
            ) : error ? (
              <div className="px-2 py-3 text-[11px] text-red-400">{error}</div>
            ) : filtered.length === 0 ? (
              <div className="px-2 py-3 text-[11px] text-muted-foreground">no matches</div>
            ) : (
              filtered.map((r, i) => (
                <button
                  key={`${r.stored}-${i}`}
                  type="button"
                  onClick={() => {
                    onChange(r.stored)
                    setOpen(false)
                    setQuery('')
                  }}
                  className="block w-full px-2 py-1.5 text-left text-xs hover:bg-accent border-b border-border/40 last:border-b-0"
                >
                  <div className="font-mono truncate">{r.primary}</div>
                  {r.secondary ? (
                    <div className="text-[10px] text-muted-foreground truncate">{r.secondary}</div>
                  ) : null}
                </button>
              ))
            )}
          </div>
        </div>
      ) : null}
    </div>
  )
}
