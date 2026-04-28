import { useCallback } from 'react'
import { Circle, Ban, Zap, XCircle, Brain, WifiOff } from 'lucide-react'
import { cn } from '@/lib/cn'
import { relativeTime, formatDuration } from '@/lib/timeFormat'
import { getCategoryMeta } from '@/lib/errorCategory'
import { useRunsStore } from '@/store/runsStore'
import { useUiStore } from '@/store/uiStore'
import type { RunSummary } from '@/types/run'

function StatusIcon({ run }: { run: RunSummary }) {
  if (run.status === 'ok') return <Circle className="h-3 w-3 fill-green-500 text-green-500 shrink-0" />
  if (run.status === 'miss') return <Ban className="h-3 w-3 text-yellow-500 shrink-0" />
  if (run.status === 'running') return <Circle className="h-3 w-3 text-blue-400 shrink-0 animate-pulse" />
  // error — use category icon
  const cat = run.error_category
  if (cat === 'wiring') return <Zap className="h-3 w-3 text-orange-400 shrink-0" />
  if (cat === 'program') return <XCircle className="h-3 w-3 text-red-400 shrink-0" />
  if (cat === 'llm') return <Brain className="h-3 w-3 text-purple-400 shrink-0" />
  if (cat === 'infra') return <WifiOff className="h-3 w-3 text-gray-400 shrink-0" />
  return <XCircle className="h-3 w-3 text-red-400 shrink-0" />
}

interface RunRowProps {
  run: RunSummary
}

export function RunRow({ run }: RunRowProps) {
  const { selectedRunId, selectRun } = useRunsStore()
  const { setDrawerOpen } = useUiStore()
  const isSelected = selectedRunId === run.run_id
  const shortId = run.run_id.slice(0, 3)
  const catMeta = getCategoryMeta(run.error_category)

  const handleClick = useCallback(() => {
    selectRun(run.run_id)
    setDrawerOpen(true)
  }, [run.run_id, selectRun, setDrawerOpen])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault()
        handleClick()
      }
    },
    [handleClick],
  )

  return (
    <div
      role="option"
      aria-selected={isSelected}
      tabIndex={0}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      className={cn(
        'px-3 py-2 cursor-pointer border-b border-border/40 hover:bg-accent/40 transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring',
        isSelected && 'bg-accent',
      )}
    >
      {/* Line 1: status + time + duration + steps + short ID */}
      <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground min-w-0">
        <StatusIcon run={run} />
        <span className="shrink-0">{relativeTime(run.started_at)}</span>
        <span className="text-border">·</span>
        <span className="shrink-0">{formatDuration(run.duration_ms)}</span>
        {run.step_count !== undefined && run.step_count > 0 && (
          <>
            <span className="text-border">·</span>
            <span className="shrink-0">{run.step_count} steps</span>
          </>
        )}
        <span className="flex-1" />
        <span className="font-mono text-[9px] text-muted-foreground/60">{shortId}</span>
      </div>

      {/* Line 2: transcript */}
      <div className="text-xs text-foreground truncate mt-0.5">
        &ldquo;{run.transcript}&rdquo;
      </div>

      {/* Line 3: error info (only when error) */}
      {run.status === 'error' && (catMeta ?? run.error_summary) && (
        <div className={cn('flex items-center gap-1.5 text-[10px] mt-0.5', catMeta?.color ?? 'text-red-400')}>
          {catMeta && <span className="font-semibold">{catMeta.label}:</span>}
          {run.error_summary && (
            <span className="truncate text-muted-foreground">{run.error_summary}</span>
          )}
        </div>
      )}
    </div>
  )
}
