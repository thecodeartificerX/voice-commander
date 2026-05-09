import { ExternalLink } from 'lucide-react'
import { useGraphStore } from '@/store/graphStore'
import { SynonymsEditor } from './SynonymsEditor'

/**
 * Renders in the PropertiesPane when no node is selected. Edits the
 * graph-level metadata (description + spoken phrases) that round-trips
 * through `POST /graph/{name}` via the existing Save button in Toolbar.
 */
export function GraphSettingsPanel() {
  const { graphMeta, setSynonyms, setDescription } = useGraphStore()

  if (!graphMeta) {
    return (
      <div className="p-3 text-xs text-muted-foreground">
        No graph loaded.
      </div>
    )
  }

  const synonyms = graphMeta.synonyms ?? []

  return (
    <div className="p-3 flex flex-col gap-4">
      <div>
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">
          Graph Settings
        </div>
        <div className="text-xs font-mono text-foreground truncate">
          {graphMeta.kind} · {graphMeta.name}
        </div>
      </div>

      <div>
        <label
          htmlFor="graph-description"
          className="block text-[10px] uppercase tracking-wider text-muted-foreground mb-1"
        >
          Description
        </label>
        <textarea
          id="graph-description"
          rows={3}
          value={graphMeta.description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="What does this command do?"
          className="w-full rounded-md border border-border bg-secondary px-2 py-1.5 text-xs text-foreground font-mono placeholder:text-muted-foreground focus:outline-none focus:border-ring focus:ring-1 focus:ring-ring resize-y"
          spellCheck={false}
        />
      </div>

      <div>
        <label className="block text-[10px] uppercase tracking-wider text-muted-foreground mb-1">
          Phrases
        </label>
        <p className="text-[10px] text-muted-foreground mb-1.5 leading-snug">
          Spoken phrases that trigger this command. Add weird transcriptions
          here (e.g.{' '}
          <code className="font-mono bg-secondary px-1 rounded">p.a.c.t</code>{' '}
          for{' '}
          <code className="font-mono bg-secondary px-1 rounded">paste</code>).
        </p>
        <SynonymsEditor value={synonyms} onChange={setSynonyms} />
      </div>

      <div className="pt-2 border-t border-border">
        <a
          href="/page/primitives"
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground"
        >
          Edit primitive phrases
          <ExternalLink className="h-3 w-3" />
        </a>
      </div>
    </div>
  )
}
