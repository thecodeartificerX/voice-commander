import { useEffect, useState } from 'react'
import { apiFetch } from '@/api/client'
import { PaletteItem } from './PaletteItem'

interface PaletteEntry {
  name: string
  description?: string
  ref?: string
}

interface PaletteData {
  pipeline: PaletteEntry[]
  commands: PaletteEntry[]
  workflows: PaletteEntry[]
}

export function Palette() {
  const [data, setData] = useState<PaletteData | null>(null)

  useEffect(() => {
    apiFetch<PaletteData>('/graph/palette').then(setData).catch(console.error)
  }, [])

  if (!data) {
    return (
      <div className="p-2 text-xs text-muted-foreground animate-pulse">Loading palette…</div>
    )
  }

  const sections: Array<{ title: string; items: PaletteEntry[]; kind: string }> = [
    { title: 'Commands', items: data.commands, kind: 'command' },
    { title: 'Workflows', items: data.workflows, kind: 'workflow' },
    { title: 'Pipeline', items: data.pipeline, kind: 'tool' },
    {
      title: 'Control',
      items: [
        { name: 'branch', ref: 'control.branch', description: 'Conditional branch (if/else)' },
        { name: 'foreach', ref: 'control.foreach', description: 'Loop over a list' },
      ],
      kind: 'control',
    },
    {
      title: 'Perception',
      items: [
        { name: 'clipboard', ref: 'perception.clipboard', description: 'Read clipboard text' },
        { name: 'window', ref: 'perception.window', description: 'Get active window title' },
        { name: 'cursor', ref: 'perception.cursor', description: 'Get cursor position' },
        { name: 'ocr', ref: 'perception.ocr', description: 'OCR a screen region' },
      ],
      kind: 'perception',
    },
  ]

  return (
    <div className="flex flex-col gap-1 p-2">
      {sections.map((s) => (
        <div key={s.title}>
          <div className="px-1 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            {s.title}
          </div>
          {s.items.map((item) => (
            <PaletteItem
              key={item.ref ?? item.name}
              name={item.name}
              ref_={item.ref ?? `${s.kind}.${item.name}`}
              description={item.description}
              kind={s.kind}
            />
          ))}
        </div>
      ))}
    </div>
  )
}
