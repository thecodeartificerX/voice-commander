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
  commands: PaletteEntry[]
  workflows: PaletteEntry[]
}

/**
 * Hardcoded schemas for sections the backend doesn't enumerate
 * (control + perception). Pipeline / commands / workflows come from
 * `/graph/palette` directly. Kept in this module so the Palette is the
 * single source of truth that primes the schema store.
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

const PERCEPTION_SCHEMAS: ToolSchema[] = [
  {
    ref: 'perception.clipboard',
    name: 'clipboard',
    description: 'Read clipboard text',
    args: {},
  },
  {
    ref: 'perception.window',
    name: 'window',
    description: 'Get active window title',
    args: {},
  },
  {
    ref: 'perception.cursor',
    name: 'cursor',
    description: 'Get cursor position',
    args: {},
  },
  {
    ref: 'perception.ocr',
    name: 'ocr',
    description: 'OCR a screen region',
    args: {
      x: { type: 'int', required: true },
      y: { type: 'int', required: true },
      w: { type: 'int', required: true },
      h: { type: 'int', required: true },
    },
  },
]

function buildSchemas(data: PaletteData): ToolSchema[] {
  const schemas: ToolSchema[] = []

  for (const item of data.pipeline) {
    schemas.push({
      ref: item.ref ?? `pipeline.${item.name}`,
      name: item.name,
      ...(item.description !== undefined ? { description: item.description } : {}),
      args: item.args ?? {},
    })
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
  schemas.push(...CONTROL_SCHEMAS, ...PERCEPTION_SCHEMAS)
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
