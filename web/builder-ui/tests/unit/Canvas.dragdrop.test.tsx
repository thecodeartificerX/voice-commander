import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, fireEvent, createEvent } from '@testing-library/react'
import type { ReactNode } from 'react'
import { useGraphStore } from '@/store/graphStore'

// Mock reactflow to a stub that renders a div with the drop handlers wired,
// and provides a deterministic `screenToFlowPosition`. This isolates the
// drop-handler logic in Canvas.tsx without spinning up the real React Flow
// runtime (which depends on viewport measurement that jsdom does not provide).
vi.mock('reactflow', () => {
  type RFProps = {
    children?: ReactNode
    onDragOver?: (e: React.DragEvent<HTMLDivElement>) => void
    onDrop?: (e: React.DragEvent<HTMLDivElement>) => void
  } & Record<string, unknown>

  const ReactFlow = ({ children, onDragOver, onDrop }: RFProps) => (
    <div data-testid="rf-canvas" onDragOver={onDragOver} onDrop={onDrop}>
      {children}
    </div>
  )

  return {
    default: ReactFlow,
    Background: () => null,
    Controls: () => null,
    MiniMap: () => null,
    ReactFlowProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
    addEdge: (edge: unknown, edges: unknown[]) => [...edges, edge],
    useReactFlow: () => ({
      screenToFlowPosition: ({ x, y }: { x: number; y: number }) => ({ x, y }),
    }),
    applyNodeChanges: <T,>(_c: unknown, nodes: T) => nodes,
    applyEdgeChanges: <T,>(_c: unknown, edges: T) => edges,
  }
})

// RunOverlay subscribes to /events SSE; stub it out so tests don't open
// network connections.
vi.mock('@/canvas/overlays/RunOverlay', () => ({
  RunOverlay: () => null,
}))

vi.mock('@/canvas/nodes', () => ({ nodeTypes: {} }))
vi.mock('@/canvas/edges', () => ({ edgeTypes: {} }))

// Import AFTER mocks so the mocked modules are picked up.
import { Canvas } from '@/canvas/Canvas'

function freshStore() {
  useGraphStore.setState({
    graphId: 'test',
    graphKind: 'command',
    graphMeta: {
      schema_version: 1,
      name: 'test',
      kind: 'command',
      description: '',
      enabled: true,
      llm_visible: true,
      inputs: [],
    },
    nodes: [],
    edges: [],
    selectedNodeId: null,
    dirty: false,
    llmVisible: true,
    draft: false,
    runStatusByNodeId: {},
  })
}

function makeDataTransfer(payload: string | null): DataTransfer {
  const store = new Map<string, string>()
  if (payload != null) store.set('application/vc-palette', payload)
  return {
    effectAllowed: 'move',
    dropEffect: 'move',
    setData: (k: string, v: string) => store.set(k, v),
    getData: (k: string) => store.get(k) ?? '',
    types: Array.from(store.keys()),
  } as unknown as DataTransfer
}

// jsdom's DragEvent does not propagate clientX/clientY/dataTransfer through
// fireEvent.drop's init dict reliably, so we build the event manually and
// stamp the required fields via Object.defineProperty.
function fireDropAt(
  el: Element,
  payload: string | null,
  clientX: number,
  clientY: number,
): void {
  const event = createEvent.drop(el)
  Object.defineProperty(event, 'clientX', { value: clientX })
  Object.defineProperty(event, 'clientY', { value: clientY })
  Object.defineProperty(event, 'dataTransfer', {
    value: makeDataTransfer(payload),
  })
  fireEvent(el, event)
}

describe('Canvas drag-and-drop', () => {
  beforeEach(() => {
    freshStore()
    vi.spyOn(crypto, 'randomUUID').mockReturnValue(
      '00000000-0000-0000-0000-000000000001' as `${string}-${string}-${string}-${string}-${string}`,
    )
  })

  it('drop with command ref pushes a node with type=command', () => {
    const { getByTestId } = render(<Canvas />)
    const canvas = getByTestId('rf-canvas')

    fireEvent.dragOver(canvas, { dataTransfer: makeDataTransfer(null) })
    fireDropAt(
      canvas,
      JSON.stringify({ ref: 'command.greet', kind: 'command' }),
      150,
      220,
    )

    const s = useGraphStore.getState()
    expect(s.nodes).toHaveLength(1)
    expect(s.nodes[0]).toMatchObject({
      type: 'command',
      position: { x: 150, y: 220 },
      data: { ref: 'command.greet', kwargs: {} },
    })
    expect(s.dirty).toBe(true)
    expect(s.selectedNodeId).toBe(s.nodes[0]!.id)
  })

  it('drop with control.branch ref produces type=branch', () => {
    const { getByTestId } = render(<Canvas />)
    const canvas = getByTestId('rf-canvas')

    fireDropAt(
      canvas,
      JSON.stringify({ ref: 'control.branch', kind: 'control' }),
      50,
      60,
    )

    const s = useGraphStore.getState()
    expect(s.nodes).toHaveLength(1)
    expect(s.nodes[0]?.type).toBe('branch')
    expect(s.nodes[0]?.data).toEqual({ ref: 'control.branch', kwargs: {} })
  })

  it('drop with perception.* ref produces type=perception', () => {
    const { getByTestId } = render(<Canvas />)
    const canvas = getByTestId('rf-canvas')

    fireDropAt(
      canvas,
      JSON.stringify({ ref: 'perception.ocr', kind: 'perception' }),
      0,
      0,
    )

    expect(useGraphStore.getState().nodes[0]?.type).toBe('perception')
  })

  it('drop with malformed JSON does not add a node and does not throw', () => {
    const { getByTestId } = render(<Canvas />)
    const canvas = getByTestId('rf-canvas')

    expect(() => fireDropAt(canvas, 'not-json{', 10, 10)).not.toThrow()

    expect(useGraphStore.getState().nodes).toHaveLength(0)
  })

  it('drop with missing palette MIME does not add a node', () => {
    const { getByTestId } = render(<Canvas />)
    const canvas = getByTestId('rf-canvas')

    fireDropAt(canvas, null, 10, 10)

    expect(useGraphStore.getState().nodes).toHaveLength(0)
  })

  it('drop with object missing required fields is rejected', () => {
    const { getByTestId } = render(<Canvas />)
    const canvas = getByTestId('rf-canvas')

    fireDropAt(canvas, JSON.stringify({ ref: 'command.x' }), 10, 10)

    expect(useGraphStore.getState().nodes).toHaveLength(0)
  })
})
