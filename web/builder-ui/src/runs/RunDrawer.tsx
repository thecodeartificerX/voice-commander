import { useEffect } from 'react'
import { ExternalLink } from 'lucide-react'
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet'
import { Button } from '@/components/ui/button'
import { useRunsStore } from '@/store/runsStore'
import { useUiStore } from '@/store/uiStore'
import { relativeTime, formatDuration } from '@/lib/timeFormat'
import { SpanTree } from './SpanTree'
import { CopyAsPromptButton } from './CopyAsPromptButton'

export function RunDrawer() {
  const { selectedRunId, detailById, fetchDetail } = useRunsStore()
  const { drawerOpen, setDrawerOpen } = useUiStore()

  useEffect(() => {
    if (selectedRunId && drawerOpen) {
      void fetchDetail(selectedRunId)
    }
  }, [selectedRunId, drawerOpen, fetchDetail])

  const run = selectedRunId ? detailById[selectedRunId] : undefined
  const shortId = selectedRunId?.slice(0, 6) ?? ''

  return (
    <Sheet open={drawerOpen} onOpenChange={setDrawerOpen}>
      <SheetContent side="right" className="flex flex-col p-0 gap-0 w-[480px] max-w-[95vw]">
        <SheetHeader className="px-5 pt-5 pb-3 border-b border-border shrink-0">
          <SheetTitle className="text-sm">
            Run {shortId}
            {run && (
              <span
                className={`ml-2 text-xs font-normal ${
                  run.status === 'ok' ? 'text-green-400' : run.status === 'error' ? 'text-red-400' : 'text-yellow-400'
                }`}
              >
                {run.status}
              </span>
            )}
          </SheetTitle>
          {run && (
            <SheetDescription className="text-xs mt-0.5">
              &ldquo;{run.transcript}&rdquo;
              <br />
              <span className="text-muted-foreground">
                {relativeTime(run.started_at)} · {formatDuration(run.duration_ms)}
              </span>
            </SheetDescription>
          )}
          {run && (
            <div className="flex items-center gap-2 pt-1">
              <CopyAsPromptButton run={run} />
              <Button
                size="sm"
                variant="outline"
                className="text-xs gap-1 h-7"
                onClick={() => window.open(`/page/runs`, '_blank')}
              >
                <ExternalLink className="h-3 w-3" />
                Runs page
              </Button>
            </div>
          )}
        </SheetHeader>

        <div className="flex-1 overflow-y-auto px-3 py-3">
          {!run ? (
            <div className="text-xs text-muted-foreground animate-pulse">Loading…</div>
          ) : (
            <SpanTree spans={run.spans} />
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}
