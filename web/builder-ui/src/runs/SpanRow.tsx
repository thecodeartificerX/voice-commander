import { cn } from '@/lib/cn'
import { formatDuration } from '@/lib/timeFormat'
import { getCategoryMeta } from '@/lib/errorCategory'
import type { SpanRecord } from '@/types/run'

interface SpanRowProps {
  span: SpanRecord
}

export function SpanRow({ span }: SpanRowProps) {
  const catMeta = getCategoryMeta(span.error_category)

  return (
    <div className="flex-1 min-w-0">
      <div className="flex items-center gap-1.5">
        <StatusBadge status={span.status} />
        {span.type !== span.name && (
          <span className="text-[10px] text-muted-foreground shrink-0">{span.type}</span>
        )}
        <span className="text-xs font-medium truncate">{span.name}</span>
        <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">
          {formatDuration(span.duration_ms)}
        </span>
      </div>

      {span.status === 'error' && (
        <div className="mt-0.5 space-y-0.5">
          {span.error_type && (
            <div className="text-[10px] text-red-400">
              {span.error_type}
            </div>
          )}
          {span.error_msg && (
            <div className="text-[10px] text-muted-foreground truncate">{span.error_msg}</div>
          )}
          {catMeta && (
            <div className={cn('text-[10px] font-semibold', catMeta.color)}>
              {catMeta.label}
            </div>
          )}
          {Boolean(span.attrs['node_id']) && (
            <div className="text-[10px] text-muted-foreground font-mono">
              node: {String(span.attrs['node_id'])}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function StatusBadge({ status }: { status: SpanRecord['status'] }) {
  const color = {
    ok: 'text-green-400',
    error: 'text-red-400',
    skipped: 'text-gray-500',
    running: 'text-blue-400 animate-pulse',
  }[status] ?? 'text-gray-400'

  const icon = { ok: '✓', error: '✗', skipped: '—', running: '◉' }[status] ?? '?'

  return <span className={cn('text-[10px] font-bold shrink-0 w-3', color)}>{icon}</span>
}
