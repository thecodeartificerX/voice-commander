import { useCallback } from 'react'
import { Input } from '@/components/ui/input'
import { useGraphStore } from '@/store/graphStore'

interface KwargsFormProps {
  nodeId: string
  kwargs: Record<string, unknown>
}

export function KwargsForm({ nodeId, kwargs }: KwargsFormProps) {
  const { setNodes, nodes } = useGraphStore()

  const updateKwarg = useCallback(
    (key: string, value: string) => {
      setNodes(
        nodes.map((n) => {
          if (n.id !== nodeId) return n
          const data = n.data as { ref: string; kwargs: Record<string, unknown> }
          return { ...n, data: { ...data, kwargs: { ...data.kwargs, [key]: value } } }
        }),
      )
    },
    [nodeId, nodes, setNodes],
  )

  const entries = Object.entries(kwargs)

  if (entries.length === 0) {
    return <div className="text-[10px] text-muted-foreground">No kwargs.</div>
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Args</div>
      {entries.map(([key, val]) => (
        <div key={key} className="flex flex-col gap-0.5">
          <label className="text-[10px] text-muted-foreground">{key}</label>
          <Input
            value={String(val ?? '')}
            onChange={(e) => updateKwarg(key, e.target.value)}
            className="h-6 text-xs font-mono"
          />
        </div>
      ))}
    </div>
  )
}
