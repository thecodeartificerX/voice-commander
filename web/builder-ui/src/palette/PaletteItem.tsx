import type { DragEvent } from 'react'
import { cn } from '@/lib/cn'

interface PaletteItemProps {
  name: string
  ref_: string
  description?: string | undefined
  kind: string
}

const KIND_DOT: Record<string, string> = {
  command: 'bg-blue-400',
  workflow: 'bg-violet-400',
  tool: 'bg-slate-400',
  control: 'bg-yellow-400',
  perception: 'bg-emerald-400',
}

export function PaletteItem({ name, ref_, description, kind }: PaletteItemProps) {
  function onDragStart(e: DragEvent) {
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData('application/vc-palette', JSON.stringify({ ref: ref_, kind }))
  }

  return (
    <div
      draggable
      onDragStart={onDragStart}
      title={description}
      className={cn(
        'flex items-center gap-2 rounded px-2 py-1 text-xs cursor-grab select-none',
        'hover:bg-accent hover:text-accent-foreground transition-colors',
      )}
    >
      <span className={cn('h-2 w-2 rounded-full shrink-0', KIND_DOT[kind] ?? 'bg-gray-400')} />
      <span className="truncate">{name}</span>
    </div>
  )
}
