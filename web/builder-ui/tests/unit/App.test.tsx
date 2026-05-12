import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, waitFor } from '@testing-library/react'
import { useGraphStore } from '@/store/graphStore'
import { useRunsStore } from '@/store/runsStore'

// Mock SSE module to avoid real EventSource use under jsdom
vi.mock('@/api/sse', () => ({
  sseConnect: vi.fn(),
  sseDisconnect: vi.fn(),
  sseSubscribe: vi.fn(() => () => {}),
}))

// Mock heavy canvas / panels — App.tsx still imports them but we don't need their real DOM
vi.mock('@/canvas/Canvas', () => ({ Canvas: () => null }))
vi.mock('@/runs/RunsPanel', () => ({ RunsPanel: () => null }))
vi.mock('@/palette/Palette', () => ({ Palette: () => null }))
vi.mock('@/properties/PropertiesPane', () => ({ PropertiesPane: () => null }))
vi.mock('@/toolbar/Toolbar', () => ({ Toolbar: () => null }))

const FAKE_GRAPH = {
  schema_version: 1,
  name: 'show_commands',
  kind: 'command' as const,
  description: '',
  enabled: true,
  llm_visible: true,
  inputs: [],
  nodes: [
    {
      id: 'n1',
      ref: 'shell.notify',
      kwargs: { msg: 'hi' },
      pos: [10, 20],
    },
  ],
  edges: [],
}

const FAKE_PALETTE = {
  pipeline: [],
  commands: [{ ref: 'command.show_commands', name: 'show_commands' }],
  workflows: [],
  control: {},
  value: {},
}

function setupLocalStorageMock(): void {
  const store = new Map<string, string>()
  const ls = {
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => {
      store.set(k, String(v))
    },
    removeItem: (k: string) => {
      store.delete(k)
    },
    clear: () => store.clear(),
    key: (i: number) => Array.from(store.keys())[i] ?? null,
    get length() {
      return store.size
    },
  }
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: ls,
  })
}

describe('App bootstrap — F-C3', () => {
  let originalFetch: typeof globalThis.fetch | undefined

  beforeEach(() => {
    originalFetch = globalThis.fetch
    setupLocalStorageMock()
    // Clear state
    useGraphStore.setState({
      graphId: null,
      graphKind: null,
      graphMeta: null,
      nodes: [],
      edges: [],
      selectedNodeId: null,
      dirty: false,
    })
    useRunsStore.setState({
      runs: [],
      detailById: {},
      selectedRunId: null,
      filters: { statuses: [], categories: [], query: '' },
      sseConnected: false,
      loading: false,
      error: null,
    })
    window.localStorage.clear()
    // Set URL with kind+name
    window.history.replaceState({}, '', '/?kind=command&name=show_commands')
  })

  afterEach(() => {
    if (originalFetch) globalThis.fetch = originalFetch
    window.history.replaceState({}, '', '/')
    vi.restoreAllMocks()
  })

  it('loads graph from URL params on mount', async () => {
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString()
      if (url.includes('/graph/show_commands')) {
        return new Response(JSON.stringify(FAKE_GRAPH), { status: 200 })
      }
      if (url.includes('/api/runs')) {
        return new Response(JSON.stringify({ runs: [] }), { status: 200 })
      }
      return new Response(JSON.stringify({}), { status: 200 })
    }) as unknown as typeof fetch

    const { default: App } = await import('@/App')
    render(<App />)

    await waitFor(() => {
      const state = useGraphStore.getState()
      expect(state.graphId).toBe('show_commands')
      expect(state.graphKind).toBe('command')
      expect(state.nodes).toHaveLength(1)
    })
  })

  it('falls back to palette discovery when no URL params and no localStorage', async () => {
    window.history.replaceState({}, '', '/')
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString()
      if (url.includes('/graph/palette')) {
        return new Response(JSON.stringify(FAKE_PALETTE), { status: 200 })
      }
      if (url.includes('/graph/show_commands')) {
        return new Response(JSON.stringify(FAKE_GRAPH), { status: 200 })
      }
      if (url.includes('/api/runs')) {
        return new Response(JSON.stringify({ runs: [] }), { status: 200 })
      }
      return new Response(JSON.stringify({}), { status: 200 })
    }) as unknown as typeof fetch

    const { default: App } = await import('@/App')
    render(<App />)

    await waitFor(() => {
      const state = useGraphStore.getState()
      expect(state.graphId).toBe('show_commands')
    })
  })

  it('opens a blank draft when ?kind=command and no name (new-command flow)', async () => {
    window.history.replaceState({}, '', '/?kind=command')
    // Pre-seed localStorage with a stale graph to verify it's bypassed.
    window.localStorage.setItem(
      'builder.lastGraph',
      JSON.stringify({ kind: 'command', name: 'show_commands' }),
    )
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString()
      if (url.includes('/api/runs')) {
        return new Response(JSON.stringify({ runs: [] }), { status: 200 })
      }
      return new Response(JSON.stringify({}), { status: 200 })
    }) as unknown as typeof fetch
    globalThis.fetch = fetchMock

    const { default: App } = await import('@/App')
    render(<App />)

    await waitFor(() => {
      const state = useGraphStore.getState()
      expect(state.draft).toBe(true)
      expect(state.graphKind).toBe('command')
      expect(state.graphId).toMatch(/^untitled_command_/)
      expect(state.nodes).toEqual([])
      expect(state.edges).toEqual([])
    })
    // Crucially: no /graph/<name> request should have fired.
    const calls = (fetchMock as unknown as { mock: { calls: unknown[][] } }).mock.calls
    const urls = calls.map((args) => String(args[0]))
    expect(urls.some((u) => u.includes('/graph/show_commands'))).toBe(false)
    expect(urls.some((u) => u.includes('/graph/palette'))).toBe(false)
  })
})
