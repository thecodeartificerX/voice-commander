import { useEffect } from 'react'
import { Toolbar } from '@/toolbar/Toolbar'
import { Canvas } from '@/canvas/Canvas'
import { Palette } from '@/palette/Palette'
import { PropertiesPane } from '@/properties/PropertiesPane'
import { RunsPanel } from '@/runs/RunsPanel'
import { useRunsStore } from '@/store/runsStore'
import { useGraphStore } from '@/store/graphStore'
import { sseConnect, sseDisconnect, sseSubscribe } from '@/api/sse'
import { ScrollArea } from '@/components/ui/scroll-area'
import { apiGetPalette } from '@/api/graphs'
import type { RunSummary } from '@/types/run'
import type { GraphKind } from '@/types/graph'

const LAST_GRAPH_KEY = 'builder.lastGraph'

interface PaletteResponse {
  commands?: Array<{ ref?: string; name?: string }>
  workflows?: Array<{ ref?: string; name?: string }>
}

function readGraphFromUrl(): { kind: GraphKind; name: string } | null {
  if (typeof window === 'undefined') return null
  const params = new URLSearchParams(window.location.search)
  const kind = params.get('kind')
  const name = params.get('name')
  if ((kind === 'command' || kind === 'workflow') && name) {
    return { kind, name }
  }
  return null
}

function readGraphFromStorage(): { kind: GraphKind; name: string } | null {
  if (typeof window === 'undefined' || !window.localStorage) return null
  try {
    const raw = window.localStorage.getItem(LAST_GRAPH_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as { kind?: string; name?: string }
    if ((parsed.kind === 'command' || parsed.kind === 'workflow') && parsed.name) {
      return { kind: parsed.kind, name: parsed.name }
    }
  } catch {
    // ignore corrupt storage
  }
  return null
}

function persistLastGraph(kind: GraphKind, name: string): void {
  if (typeof window === 'undefined' || !window.localStorage) return
  try {
    window.localStorage.setItem(LAST_GRAPH_KEY, JSON.stringify({ kind, name }))
  } catch {
    // ignore
  }
}

async function discoverFirstGraph(): Promise<{ kind: GraphKind; name: string } | null> {
  try {
    const palette = (await apiGetPalette()) as PaletteResponse
    const firstCommand = palette.commands?.[0]
    if (firstCommand) {
      const ref = firstCommand.ref ?? firstCommand.name
      if (typeof ref === 'string') {
        const name = ref.startsWith('command.') ? ref.slice('command.'.length) : ref
        return { kind: 'command', name }
      }
    }
    const firstWorkflow = palette.workflows?.[0]
    if (firstWorkflow) {
      const ref = firstWorkflow.ref ?? firstWorkflow.name
      if (typeof ref === 'string') {
        const name = ref.startsWith('workflow.') ? ref.slice('workflow.'.length) : ref
        return { kind: 'workflow', name }
      }
    }
  } catch (e) {
    console.error('discoverFirstGraph failed:', e)
  }
  return null
}

export default function App() {
  const { fetchInitial, onSseEvent } = useRunsStore()
  const loadGraph = useGraphStore((s) => s.load)

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

  useEffect(() => {
    let cancelled = false
    async function bootstrap() {
      const target =
        readGraphFromUrl() ?? readGraphFromStorage() ?? (await discoverFirstGraph())
      if (cancelled || !target) return
      try {
        await loadGraph(target.kind, target.name)
        persistLastGraph(target.kind, target.name)
      } catch (e) {
        console.error('initial graph load failed:', e)
      }
    }
    void bootstrap()
    return () => {
      cancelled = true
    }
  }, [loadGraph])

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
