import { useCallback } from 'react'
import { Input } from '@/components/ui/input'
import { useGraphStore } from '@/store/graphStore'

interface KwargsFormProps {
  nodeId: string
  kwargs: Record<string, unknown>
}

type KwargType = 'number' | 'boolean' | 'string'

function classifyKwarg(value: unknown): KwargType {
  if (typeof value === 'number') return 'number'
  if (typeof value === 'boolean') return 'boolean'
  return 'string'
}

function coerce(value: string, type: KwargType): unknown {
  if (type === 'number') {
    if (value.trim() === '') return ''
    const n = Number(value)
    return Number.isNaN(n) ? value : n
  }
  if (type === 'boolean') {
    const v = value.trim().toLowerCase()
    if (v === 'true') return true
    if (v === 'false') return false
    // Preserve user's literal until they type a valid boolean
    return value
  }
  return value
}

export function KwargsForm({ nodeId, kwargs }: KwargsFormProps) {
  const setNodes = useGraphStore((s) => s.setNodes)

  const updateKwarg = useCallback(
    (key: string, raw: string, type: KwargType) => {
      const coerced = coerce(raw, type)
      setNodes((current) =>
        current.map((n) => {
          if (n.id !== nodeId) return n
          const data = n.data as { ref: string; kwargs: Record<string, unknown> }
          return {
            ...n,
            data: { ...data, kwargs: { ...data.kwargs, [key]: coerced } },
          }
        }),
      )
    },
    [nodeId, setNodes],
  )

  const entries = Object.entries(kwargs)

  if (entries.length === 0) {
    return <div className="text-[10px] text-muted-foreground">No kwargs.</div>
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Args</div>
      {entries.map(([key, val]) => {
        const type = classifyKwarg(val)
        return (
          <div key={key} className="flex flex-col gap-0.5">
            <label className="text-[10px] text-muted-foreground">
              {key}
              <span className="ml-1 text-muted-foreground/60">({type})</span>
            </label>
            <Input
              value={String(val ?? '')}
              onChange={(e) => updateKwarg(key, e.target.value, type)}
              className="h-6 text-xs font-mono"
            />
          </div>
        )
      })}
    </div>
  )
}
