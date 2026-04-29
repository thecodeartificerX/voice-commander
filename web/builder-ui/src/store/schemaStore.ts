import { create } from 'zustand'
import type { ToolSchema } from '@/types/graph'

/**
 * Holds the per-ref tool schema dictionary used by the PropertiesPane to
 * render argument inputs. Populated once the Palette finishes its
 * `/graph/palette` fetch — including pipeline primitives, commands,
 * workflows, control nodes, and perception primitives.
 *
 * Keyed by canonical ref (`pipeline.wait`, `control.branch`, etc.) so that a
 * selected canvas node can look up its declared args via its `data.ref`.
 */
interface SchemaState {
  schemasByRef: Record<string, ToolSchema>
  setSchemas(schemas: ToolSchema[]): void
  getSchema(ref: string): ToolSchema | undefined
}

export const useSchemaStore = create<SchemaState>((set, get) => ({
  schemasByRef: {},

  setSchemas(schemas) {
    const next: Record<string, ToolSchema> = {}
    for (const s of schemas) {
      next[s.ref] = s
    }
    set({ schemasByRef: next })
  },

  getSchema(ref) {
    return get().schemasByRef[ref]
  },
}))
