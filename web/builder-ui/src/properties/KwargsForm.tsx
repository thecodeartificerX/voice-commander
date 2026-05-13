import { useCallback } from 'react'
import { Input } from '@/components/ui/input'
import { useGraphStore } from '@/store/graphStore'
import { useSchemaStore } from '@/store/schemaStore'
import type { ToolArgMeta } from '@/types/graph'
import { KeyRecorder } from './KeyRecorder'
import { TargetPicker } from './TargetPicker'

// Backward-compat fallback for primitive/arg pairs whose backend metadata
// pre-dates the `widget_kind` field. The runtime check prefers
// `meta.widget_kind` from the palette; this map only fires when it is absent.
const LEGACY_WIDGET_HINTS: Record<string, Record<string, string>> = {
  'pipeline.press': { combo: 'key-recorder' },
  'pipeline.focus': { target: 'window-picker' },
  'pipeline.open': { target: 'app-picker' },
}

function resolveWidgetKind(
  nodeRef: string,
  argKey: string,
  meta: ToolArgMeta,
): string | undefined {
  return meta.widget_kind ?? LEGACY_WIDGET_HINTS[nodeRef]?.[argKey]
}

interface KwargsFormProps {
  nodeId: string
  kwargs: Record<string, unknown>
  /**
   * Canonical ref of the selected node (e.g. `pipeline.wait`,
   * `command.copy`). Named `nodeRef` to avoid collision with React's
   * reserved `ref` prop.
   */
  nodeRef: string
}

/** Coarse classification of an `ArgMeta.type` Python type string. */
type WidgetKind = 'int' | 'float' | 'bool' | 'string'

function classifyType(typeStr: string | undefined): WidgetKind {
  const t = (typeStr ?? '').trim().toLowerCase()
  // Optional / union forms like "int | None" — split on `|`, take first piece.
  const head = t.split('|')[0]?.trim() ?? t
  if (head === 'int' || head === 'integer') return 'int'
  if (head === 'float' || head === 'number') return 'float'
  if (head === 'bool' || head === 'boolean') return 'bool'
  return 'string'
}

/**
 * Fallback classifier for stray kwargs that have no schema entry — the form
 * still wants to render them rather than dropping them. Mirrors the legacy
 * pre-schema behaviour.
 */
function classifyByValue(value: unknown): WidgetKind {
  if (typeof value === 'number') return Number.isInteger(value) ? 'int' : 'float'
  if (typeof value === 'boolean') return 'bool'
  return 'string'
}

interface FieldUpdate {
  /** New value to merge into kwargs, or `undefined` to drop the key. */
  value: unknown
  drop: boolean
}

function coerceFromTextInput(raw: string, kind: WidgetKind): FieldUpdate {
  if (kind === 'int') {
    if (raw.trim() === '') return { value: undefined, drop: true }
    const n = Number(raw)
    if (Number.isFinite(n) && Number.isInteger(n)) return { value: n, drop: false }
    // Reject — keep prior value by reporting drop so the store update is a no-op
    // for this key. The browser's number input prevents most invalid characters,
    // but a stray decimal would land here.
    return { value: undefined, drop: true }
  }
  if (kind === 'float') {
    if (raw.trim() === '') return { value: undefined, drop: true }
    const n = Number(raw)
    if (Number.isFinite(n)) return { value: n, drop: false }
    return { value: undefined, drop: true }
  }
  // string (or unknown)
  return { value: raw, drop: false }
}

export function KwargsForm({ nodeId, kwargs, nodeRef }: KwargsFormProps) {
  const setNodes = useGraphStore((s) => s.setNodes)
  const schema = useSchemaStore((s) => s.getSchema(nodeRef))

  const updateKwarg = useCallback(
    (key: string, update: FieldUpdate) => {
      setNodes((current) =>
        current.map((n) => {
          if (n.id !== nodeId) return n
          const data = n.data as { ref: string; kwargs: Record<string, unknown> }
          const nextKwargs: Record<string, unknown> = { ...data.kwargs }
          if (update.drop) {
            delete nextKwargs[key]
          } else {
            nextKwargs[key] = update.value
          }
          return {
            ...n,
            data: { ...data, kwargs: nextKwargs },
          }
        }),
      )
    },
    [nodeId, setNodes],
  )

  // Build the list of fields to render. Schema-driven when present; otherwise
  // fall back to whatever keys already exist in kwargs (legacy behaviour).
  const fields: Array<[string, ToolArgMeta]> = schema
    ? Object.entries(schema.args)
    : Object.keys(kwargs).map((k) => [k, { type: '' } satisfies ToolArgMeta])

  if (fields.length === 0) {
    return <div className="text-[10px] text-muted-foreground">No kwargs.</div>
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Args</div>
      {fields.map(([key, meta]) => {
        const declaredKind = classifyType(meta.type)
        // For unschemaed fallback fields, classifyType returns 'string'; refine
        // by looking at the actual value so legacy numeric/boolean kwargs keep
        // the right widget.
        const kind = schema ? declaredKind : classifyByValue(kwargs[key])
        const value = kwargs[key]
        const required = meta.required ?? false
        const typeLabel = meta.type || kind
        const widget = resolveWidgetKind(nodeRef, key, meta)
        const setString = (next: string) =>
          updateKwarg(key, next === '' ? { value: undefined, drop: true } : { value: next, drop: false })
        return (
          <div key={key} className="flex flex-col gap-0.5">
            <label className="text-[10px] text-muted-foreground">
              {key}
              <span className="ml-1 text-muted-foreground/60">({typeLabel})</span>
              {required ? <span className="ml-1 text-red-400">*</span> : null}
            </label>
            {widget === 'key-recorder' ? (
              <KeyRecorder
                value={typeof value === 'string' ? value : ''}
                onChange={setString}
              />
            ) : widget === 'window-picker' ? (
              <TargetPicker
                kind="window"
                value={typeof value === 'string' ? value : ''}
                onChange={setString}
              />
            ) : widget === 'app-picker' ? (
              <TargetPicker
                kind="app"
                value={typeof value === 'string' ? value : ''}
                onChange={setString}
              />
            ) : kind === 'bool' ? (
              <input
                type="checkbox"
                checked={value === true}
                onChange={(e) =>
                  updateKwarg(key, { value: e.target.checked, drop: false })
                }
                className="h-4 w-4"
              />
            ) : kind === 'int' || kind === 'float' ? (
              <Input
                type="number"
                step={kind === 'int' ? 1 : 'any'}
                value={
                  typeof value === 'number' && Number.isFinite(value) ? String(value) : ''
                }
                onChange={(e) => updateKwarg(key, coerceFromTextInput(e.target.value, kind))}
                className="h-6 text-xs font-mono"
              />
            ) : (
              <Input
                value={typeof value === 'string' ? value : value == null ? '' : String(value)}
                onChange={(e) => updateKwarg(key, coerceFromTextInput(e.target.value, kind))}
                className="h-6 text-xs font-mono"
              />
            )}
            {meta.description ? (
              <div className="text-[10px] text-muted-foreground/70 truncate">
                {meta.description}
              </div>
            ) : null}
          </div>
        )
      })}
    </div>
  )
}
