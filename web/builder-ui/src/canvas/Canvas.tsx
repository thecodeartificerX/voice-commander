import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  ReactFlowProvider,
  addEdge,
  useReactFlow,
  type OnConnect,
  type OnNodesChange,
  type OnEdgesChange,
} from 'reactflow'
import 'reactflow/dist/style.css'
import { useCallback, useEffect } from 'react'
import type { DragEvent as ReactDragEvent } from 'react'
import type { Node } from 'reactflow'
import { useGraphStore } from '@/store/graphStore'
import { refToNodeType } from '@/lib/graphSerialize'
import { nodeTypes } from './nodes'
import { edgeTypes } from './edges'
import { RunOverlay } from './overlays/RunOverlay'

const PALETTE_MIME = 'application/vc-palette'

function generateNodeId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `node_${Date.now()}_${Math.floor(Math.random() * 0xffff).toString(16)}`
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  if (target.isContentEditable) return true
  const tag = target.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'
}

function cloneNodeData(data: unknown): unknown {
  if (typeof structuredClone === 'function') {
    try {
      return structuredClone(data)
    } catch {
      // fall through
    }
  }
  return JSON.parse(JSON.stringify(data ?? {}))
}

function CanvasInner() {
  const nodes = useGraphStore((s) => s.nodes)
  const edges = useGraphStore((s) => s.edges)
  const setNodes = useGraphStore((s) => s.setNodes)
  const setEdges = useGraphStore((s) => s.setEdges)
  const selectNode = useGraphStore((s) => s.selectNode)
  const selectedNodeId = useGraphStore((s) => s.selectedNodeId)
  const applyNodeChangesStore = useGraphStore((s) => s.applyNodeChanges)
  const applyEdgeChangesStore = useGraphStore((s) => s.applyEdgeChanges)
  const { screenToFlowPosition } = useReactFlow()

  const onNodesChange: OnNodesChange = useCallback(
    (changes) => applyNodeChangesStore(changes),
    [applyNodeChangesStore],
  )

  const onEdgesChange: OnEdgesChange = useCallback(
    (changes) => applyEdgeChangesStore(changes),
    [applyEdgeChangesStore],
  )

  const onConnect: OnConnect = useCallback(
    (connection) =>
      setEdges((current) =>
        addEdge(
          {
            ...connection,
            type: 'control',
            data: { kind: 'ok' },
          },
          current,
        ),
      ),
    [setEdges],
  )

  const onNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      selectNode(node.id)
    },
    [selectNode],
  )

  const onPaneClick = useCallback(() => {
    selectNode(null)
  }, [selectNode])

  const duplicateSelected = useCallback(() => {
    if (!selectedNodeId) return
    const source = nodes.find((n) => n.id === selectedNodeId)
    if (!source) return
    const newNode: Node = {
      ...source,
      id: generateNodeId(),
      position: {
        x: (source.position?.x ?? 0) + 40,
        y: (source.position?.y ?? 0) + 40,
      },
      data: cloneNodeData(source.data),
      selected: false,
      dragging: false,
    }
    setNodes((current) => [...current, newNode])
    selectNode(newNode.id)
  }, [nodes, selectedNodeId, setNodes, selectNode])

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (!(event.ctrlKey || event.metaKey)) return
      if (event.key !== 'd' && event.key !== 'D') return
      if (isEditableTarget(event.target)) return
      event.preventDefault()
      duplicateSelected()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [duplicateSelected])

  const onDragOver = useCallback((event: ReactDragEvent<HTMLDivElement>) => {
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
  }, [])

  const onDrop = useCallback(
    (event: ReactDragEvent<HTMLDivElement>) => {
      event.preventDefault()
      const raw = event.dataTransfer.getData(PALETTE_MIME)
      if (!raw) return
      let payload: { ref: string; kind: string }
      try {
        const parsed = JSON.parse(raw) as unknown
        if (
          !parsed ||
          typeof parsed !== 'object' ||
          typeof (parsed as { ref?: unknown }).ref !== 'string' ||
          typeof (parsed as { kind?: unknown }).kind !== 'string'
        ) {
          return
        }
        payload = parsed as { ref: string; kind: string }
      } catch {
        return
      }
      const position = screenToFlowPosition({ x: event.clientX, y: event.clientY })
      const newNode: Node = {
        id: generateNodeId(),
        type: refToNodeType(payload.ref),
        position,
        data: { ref: payload.ref, kwargs: {} },
      }
      setNodes((current) => [...current, newNode])
      selectNode(newNode.id)
    },
    [screenToFlowPosition, setNodes, selectNode],
  )

  return (
    <div className="h-full w-full relative">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeClick={onNodeClick}
        onPaneClick={onPaneClick}
        onDragOver={onDragOver}
        onDrop={onDrop}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        deleteKeyCode={['Delete', 'Backspace']}
        className="bg-background"
      >
        <Background color="#334155" gap={16} />
        <Controls className="[&>button]:bg-card [&>button]:border-border [&>button]:text-foreground" />
        <MiniMap
          className="bg-card border border-border"
          nodeColor="#334155"
          maskColor="rgba(0,0,0,0.5)"
        />
      </ReactFlow>
      <RunOverlay />
    </div>
  )
}

export function Canvas() {
  return (
    <ReactFlowProvider>
      <CanvasInner />
    </ReactFlowProvider>
  )
}
