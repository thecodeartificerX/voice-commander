import { useState, useMemo } from 'react'
import { ChevronRight, ChevronDown } from 'lucide-react'
import { cn } from '@/lib/cn'
import type { SpanRecord } from '@/types/run'
import { SpanRow } from './SpanRow'

interface SpanTreeProps {
  spans: SpanRecord[]
}

export function SpanTree({ spans }: SpanTreeProps) {
  // Build parent → children map (memoized to avoid O(n) rebuild on every render)
  const childrenMap = useMemo(() => {
    const map = new Map<string | null, SpanRecord[]>()
    for (const s of spans) {
      const key = s.parent_span_id ?? null
      const arr = map.get(key) ?? []
      arr.push(s)
      map.set(key, arr)
    }
    return map
  }, [spans])

  const roots = childrenMap.get(null) ?? []

  if (roots.length === 0) {
    return <div className="text-xs text-muted-foreground">No spans recorded.</div>
  }

  return (
    <div role="tree" className="text-xs">
      {roots.map((s) => (
        <SpanTreeNode key={s.span_id} span={s} childrenMap={childrenMap} depth={0} defaultExpanded={true} />
      ))}
    </div>
  )
}

interface SpanTreeNodeProps {
  span: SpanRecord
  childrenMap: Map<string | null, SpanRecord[]>
  depth: number
  defaultExpanded: boolean
}

function SpanTreeNode({ span, childrenMap, depth, defaultExpanded }: SpanTreeNodeProps) {
  const [expanded, setExpanded] = useState(defaultExpanded || span.status === 'error')
  const children = childrenMap.get(span.span_id) ?? []
  const hasChildren = children.length > 0

  return (
    <div style={{ paddingLeft: depth * 12 }}>
      <div
        className={cn(
          'flex items-start gap-1 py-0.5 cursor-pointer hover:bg-accent/30 rounded px-1 -mx-1',
        )}
        onClick={() => setExpanded((v) => !v)}
        role="treeitem"
        aria-expanded={hasChildren ? expanded : undefined}
      >
        <span className="w-3 shrink-0 mt-0.5 text-muted-foreground">
          {hasChildren ? (
            expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />
          ) : null}
        </span>
        <SpanRow span={span} />
      </div>
      {expanded && hasChildren && (
        <div>
          {children.map((c) => (
            <SpanTreeNode
              key={c.span_id}
              span={c}
              childrenMap={childrenMap}
              depth={depth + 1}
              defaultExpanded={c.status === 'error'}
            />
          ))}
        </div>
      )}
    </div>
  )
}
