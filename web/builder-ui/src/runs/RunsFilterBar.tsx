import { useState, useRef, useCallback } from 'react'
import { Search } from 'lucide-react'
import { cn } from '@/lib/cn'
import { useRunsStore } from '@/store/runsStore'
import type { ErrorCategory, RunStatus } from '@/types/run'

const STATUS_PILLS: Array<{ id: RunStatus | 'all'; label: string }> = [
  { id: 'all', label: '● all' },
  { id: 'ok', label: '✓ ok' },
  { id: 'miss', label: '⊘ miss' },
  { id: 'error', label: '✗ error' },
]

const CATEGORY_PILLS: Array<{ id: ErrorCategory; label: string }> = [
  { id: 'program', label: '⚙ prog' },
  { id: 'wiring', label: '⚡ wir' },
  { id: 'infra', label: '📡 infra' },
]

export function RunsFilterBar() {
  const { filters, setFilter } = useRunsStore()
  const [search, setSearch] = useState('')
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  function toggleStatus(id: RunStatus | 'all') {
    if (id === 'all') {
      setFilter({ statuses: [], categories: [] })
      return
    }
    const cur = filters.statuses
    const next = cur.includes(id) ? cur.filter((s) => s !== id) : [...cur, id]
    setFilter({ statuses: next })
  }

  function toggleCategory(id: ErrorCategory) {
    const cur = filters.categories as ErrorCategory[]
    const next = cur.includes(id) ? cur.filter((c) => c !== id) : [...cur, id]
    setFilter({ categories: next })
  }

  const handleSearch = useCallback(
    (val: string) => {
      setSearch(val)
      if (debounceTimer.current) clearTimeout(debounceTimer.current)
      debounceTimer.current = setTimeout(() => setFilter({ query: val }), 300)
    },
    [setFilter],
  )

  const allActive = filters.statuses.length === 0 && filters.categories.length === 0

  return (
    <div className="px-2 py-1.5 border-b border-border space-y-1.5 shrink-0">
      {/* Status pills */}
      <div className="flex flex-wrap gap-1" role="group" aria-label="Status filter">
        {STATUS_PILLS.map((p) => {
          const active =
            p.id === 'all' ? allActive : filters.statuses.includes(p.id as RunStatus)
          return (
            <button
              key={p.id}
              role="checkbox"
              aria-checked={active}
              onClick={() => toggleStatus(p.id)}
              className={cn(
                'px-2 py-0.5 rounded text-[10px] transition-colors border',
                active
                  ? 'bg-primary text-primary-foreground border-primary'
                  : 'border-border text-muted-foreground hover:border-muted-foreground',
              )}
            >
              {p.label}
            </button>
          )
        })}
      </div>

      {/* Category pills */}
      <div className="flex flex-wrap gap-1" role="group" aria-label="Error category filter">
        {CATEGORY_PILLS.map((p) => {
          const active = (filters.categories as ErrorCategory[]).includes(p.id)
          return (
            <button
              key={p.id}
              role="checkbox"
              aria-checked={active}
              onClick={() => toggleCategory(p.id)}
              className={cn(
                'px-2 py-0.5 rounded text-[10px] transition-colors border',
                active
                  ? 'bg-primary text-primary-foreground border-primary'
                  : 'border-border text-muted-foreground hover:border-muted-foreground',
              )}
            >
              {p.label}
            </button>
          )
        })}
      </div>

      {/* Search */}
      <div className="relative">
        <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3 w-3 text-muted-foreground" />
        <input
          type="search"
          placeholder="search transcript…"
          value={search}
          onChange={(e) => handleSearch(e.target.value)}
          className="w-full pl-7 pr-2 py-1 text-xs bg-muted rounded border border-border focus:outline-none focus:ring-1 focus:ring-ring placeholder:text-muted-foreground"
        />
      </div>
    </div>
  )
}
