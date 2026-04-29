import { useGraphStore } from '@/store/graphStore'
import { KwargsForm } from './KwargsForm'

export function PropertiesPane() {
  const { selectedNodeId, nodes } = useGraphStore()

  if (!selectedNodeId) {
    return (
      <div className="p-3 text-xs text-muted-foreground">
        Select a node to edit its properties.
      </div>
    )
  }

  const node = nodes.find((n) => n.id === selectedNodeId)
  if (!node) return null

  const data = node.data as { ref: string; kwargs: Record<string, unknown> }

  return (
    <div className="p-3 flex flex-col gap-3">
      <div>
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">Node</div>
        <div className="text-xs font-mono text-foreground truncate">{data.ref}</div>
        <div className="text-[10px] text-muted-foreground truncate">id: {selectedNodeId}</div>
      </div>
      <KwargsForm nodeId={selectedNodeId} kwargs={data.kwargs} />
    </div>
  )
}
