import { useEffect } from 'react'
import { Toolbar } from '@/toolbar/Toolbar'
import { Canvas } from '@/canvas/Canvas'
import { Palette } from '@/palette/Palette'
import { PropertiesPane } from '@/properties/PropertiesPane'
import { RunsPanel } from '@/runs/RunsPanel'
import { useRunsStore } from '@/store/runsStore'
import { sseConnect, sseDisconnect, sseSubscribe } from '@/api/sse'
import { ScrollArea } from '@/components/ui/scroll-area'
import type { RunSummary } from '@/types/run'

export default function App() {
  const { fetchInitial, onSseEvent } = useRunsStore()

  useEffect(() => {
    void fetchInitial()
    sseConnect((connected) => {
      onSseEvent('connection', { connected })
    })
    const unsub = sseSubscribe('run.appended', (data) => {
      onSseEvent('run.appended', data as RunSummary)
    })
    return () => {
      unsub()
      sseDisconnect()
    }
  }, [fetchInitial, onSseEvent])

  return (
    <div className="flex h-screen flex-col bg-background text-foreground overflow-hidden">
      <Toolbar />

      <div className="flex flex-1 overflow-hidden">
        {/* Palette */}
        <aside className="w-52 shrink-0 border-r border-border bg-card overflow-hidden flex flex-col">
          <ScrollArea className="flex-1">
            <Palette />
          </ScrollArea>
        </aside>

        {/* Canvas */}
        <main className="flex-1 overflow-hidden">
          <Canvas />
        </main>

        {/* Properties pane */}
        <aside className="w-64 shrink-0 border-l border-border bg-card overflow-hidden flex flex-col">
          <div className="px-3 py-1.5 border-b border-border text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Properties
          </div>
          <ScrollArea className="flex-1">
            <PropertiesPane />
          </ScrollArea>
        </aside>

        {/* Runs panel */}
        <RunsPanel />
      </div>
    </div>
  )
}
