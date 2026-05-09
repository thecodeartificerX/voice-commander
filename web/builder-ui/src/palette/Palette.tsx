import { useEffect, useState } from 'react'
import { apiFetch } from '@/api/client'
import type { ToolArgMeta, ToolSchema } from '@/types/graph'
import { useSchemaStore } from '@/store/schemaStore'
import { PaletteItem } from './PaletteItem'

interface PaletteEntry {
  name: string
  description?: string
  ref?: string
  args?: Record<string, ToolArgMeta>
}

interface PaletteData {
  pipeline: PaletteEntry[]
  perception: PaletteEntry[]
  commands: PaletteEntry[]
  workflows: PaletteEntry[]
}

/**
 * Hardcoded schemas for the Control section the backend doesn't enumerate.
 * Pipeline / perception / commands / workflows come from `/graph/palette`
 * directly so there's a single source of truth — perception primitives
 * use the same canonical `pipeline.<name>` refs as Pipeline entries; the
 * backend just partitions them into their own list for display.
 */
const CONTROL_SCHEMAS: ToolSchema[] = [
  {
    ref: 'control.branch',
    name: 'branch',
    description: 'Conditional branch (if/else)',
    args: { cond: { type: 'boolean', required: true } },
  },
  {
    ref: 'control.foreach',
    name: 'foreach',
    description: 'Loop over a list',
    args: { list: { type: 'array', required: true } },
  },
]

function buildSchemas(data: PaletteData): ToolSchema[] {
  const schemas: ToolSchema[] = []

  const primitiveSections: Array<{ items: PaletteEntry[]; prefix: string }> = [
    { items: data.pipeline, prefix: 'pipeline' },
    { items: data.perception, prefix: 'pipeline' },
  ]
  for (const { items, prefix } of primitiveSections) {
    for (const item of items) {
      schemas.push({
        ref: item.ref ?? `${prefix}.${item.name}`,
        name: item.name,
        ...(item.description !== undefined ? { description: item.description } : {}),
        args: item.args ?? {},
      })
    }
  }
  for (const item of data.commands) {
    schemas.push({
      ref: item.ref ?? `command.${item.name}`,
      name: item.name,
      ...(item.description !== undefined ? { description: item.description } : {}),
      args: item.args ?? {},
    })
  }
  for (const item of data.workflows) {
    schemas.push({
      ref: item.ref ?? `workflow.${item.name}`,
      name: item.name,
      ...(item.description !== undefined ? { description: item.description } : {}),
      args: item.args ?? {},
    })
  }
  schemas.push(...CONTROL_SCHEMAS)
  return schemas
}

export function Palette() {
  const [data, setData] = useState<PaletteData | null>(null)

  useEffect(() => {
    apiFetch<PaletteData>('/graph/palette')
      .then((d) => {
        setData(d)
        useSchemaStore.getState().setSchemas(buildSchemas(d))
      })
      .catch(console.error)
  }, [])

  if (!data) {
    return (
      <div className="p-2 text-xs text-muted-foreground animate-pulse">Loading palette…</div>
    )
  }

  const sections: Array<{ title: string; items: PaletteEntry[]; kind: string }> = [
    { title: 'Commands', items: data.commands, kind: 'command' },
    { title: 'Workflows', items: data.workflows, kind: 'workflow' },
    { title: 'Primitives', items: data.pipeline, kind: 'tool' },
    {
      title: 'Control',
      items: [
        { name: 'branch', ref: 'control.branch', description: 'Conditional branch (if/else)' },
        { name: 'foreach', ref: 'control.foreach', description: 'Loop over a list' },
      ],
      kind: 'control',
    },
    { title: 'Perception', items: data.perception, kind: 'perception' },
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
