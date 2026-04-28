import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  ReactFlowProvider,
  addEdge,
  applyNodeChanges,
  applyEdgeChanges,
  type OnConnect,
  type OnNodesChange,
  type OnEdgesChange,
} from 'reactflow'
import 'reactflow/dist/style.css'
import { useCallback } from 'react'
import type { Node } from 'reactflow'
import { useGraphStore } from '@/store/graphStore'
import { nodeTypes } from './nodes'
import { edgeTypes } from './edges'
import { RunOverlay } from './overlays/RunOverlay'

function CanvasInner() {
  const { nodes, edges, setNodes, setEdges, selectNode } = useGraphStore()

  const onNodesChange: OnNodesChange = useCallback(
    (changes) => setNodes(applyNodeChanges(changes, nodes)),
    [nodes, setNodes],
  )

  const onEdgesChange: OnEdgesChange = useCallback(
    (changes) => setEdges(applyEdgeChanges(changes, edges)),
    [edges, setEdges],
  )

  const onConnect: OnConnect = useCallback(
    (connection) =>
      setEdges(
        addEdge(
          {
            ...connection,
            type: 'control',
            data: { kind: 'ok' },
          },
          edges,
        ),
      ),
    [edges, setEdges],
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
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        deleteKeyCode="Delete"
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
