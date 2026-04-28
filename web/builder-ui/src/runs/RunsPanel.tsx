import { useRef, useMemo } from 'react'
import { RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useRunsStore } from '@/store/runsStore'
import { useUiStore } from '@/store/uiStore'
import { RunsFilterBar } from './RunsFilterBar'
import { RunRow } from './RunRow'
import { RunDrawer } from './RunDrawer'

export function RunsPanel() {
  const { runs, visibleRuns, fetchInitial, fetchOlder, sseConnected, loading } = useRunsStore()
  const { drawerOpen } = useUiStore()
  const bottomRef = useRef<HTMLDivElement>(null)

  // useMemo avoids re-computing the filter on every render when runs/filters haven't changed
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const visible = useMemo(() => visibleRuns(), [runs, visibleRuns])

  return (
    <>
      <div className="w-72 shrink-0 border-l border-border bg-card flex flex-col h-full">
        {/* Header */}
        <div className="flex items-center justify-between px-3 py-2 border-b border-border shrink-0">
          <span className="text-xs font-semibold">Runs</span>
          <div className="flex items-center gap-1">
            <span
              title={sseConnected ? 'Live updates active' : 'Live updates disconnected'}
              className={`h-2 w-2 rounded-full ${sseConnected ? 'bg-green-500' : 'bg-red-500 animate-pulse'}`}
            />
            <Button
              size="icon"
              variant="ghost"
              className="h-6 w-6"
              onClick={() => fetchInitial()}
              title="Refresh"
            >
              <RefreshCw className="h-3 w-3" />
            </Button>
          </div>
        </div>

        {/* Filter bar */}
        <RunsFilterBar />

        {/* Run list */}
        <ScrollArea className="flex-1">
          {loading && runs.length === 0 && (
            <div className="p-3 space-y-2">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-14 bg-muted/40 rounded animate-pulse" />
              ))}
            </div>
          )}

          {!loading && visible.length === 0 && (
            <div className="p-4 text-xs text-muted-foreground text-center">
              {runs.length === 0
                ? 'No runs yet. Press Scroll Lock and speak a command.'
                : 'No runs match these filters.'}
            </div>
          )}

          {visible.map((run) => (
            <RunRow key={run.run_id} run={run} />
          ))}

          {/* Sentinel for scroll-bottom pagination */}
          <div ref={bottomRef} className="h-px" />

          {visible.length > 0 && (
            <button
              className="w-full py-2 text-[10px] text-muted-foreground hover:text-foreground transition-colors"
              onClick={() => fetchOlder()}
            >
              Load older
            </button>
          )}
        </ScrollArea>
      </div>

      {/* Side drawer */}
      {drawerOpen && <RunDrawer />}
    </>
  )
}
