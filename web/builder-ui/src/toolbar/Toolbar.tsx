import { useState } from 'react'
import { ChevronLeft, Save, Eye, EyeOff, RefreshCw } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { useGraphStore } from '@/store/graphStore'
import { useUiStore } from '@/store/uiStore'
import { PromptInspectorDialog } from './PromptInspectorDialog'

export function Toolbar() {
  const {
    graphId,
    graphKind,
    dirty,
    draft,
    save,
    renameDraft,
    llmVisible,
    toggleLlmVisible,
  } = useGraphStore()
  const { promptInspectorOpen, setPromptInspectorOpen } = useUiStore()
  const [saving, setSaving] = useState(false)

  const backHref = graphKind === 'workflow' ? '/page/workflows' : '/page/commands'

  async function handleSave() {
    setSaving(true)
    try {
      await save()
      toast.success('Graph saved')
    } catch (e) {
      toast.error(`Save failed: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setSaving(false)
    }
  }

  function handleBackClick(e: React.MouseEvent<HTMLAnchorElement>) {
    if (!useGraphStore.getState().dirty) return
    const ok = window.confirm('Discard unsaved changes?')
    if (!ok) e.preventDefault()
  }

  return (
    <header className="h-10 border-b border-border flex items-center px-3 gap-2 shrink-0 bg-card">
      <a href={backHref} onClick={handleBackClick} aria-label="Back" title="Back">
        <Button size="sm" variant="ghost" className="gap-1 text-xs px-2">
          <ChevronLeft className="h-3 w-3" />
          Back
        </Button>
      </a>

      {draft ? (
        <input
          type="text"
          value={graphId ?? ''}
          onChange={(e) => renameDraft(e.target.value)}
          className="text-sm font-semibold text-foreground mr-2 bg-transparent border-b border-border focus:outline-none focus:border-primary px-1 min-w-0 w-48"
          placeholder="name your command…"
          spellCheck={false}
          aria-label="Graph name"
        />
      ) : (
        <span className="text-sm font-semibold text-foreground mr-2">
          {graphId ?? 'Voice Commander — Builder'}
        </span>
      )}
      {dirty && <span className="text-[10px] text-yellow-400">●</span>}

      <div className="flex-1" />

      <Button
        size="sm"
        variant="ghost"
        onClick={toggleLlmVisible}
        title={llmVisible ? 'LLM-visible: on' : 'LLM-visible: off'}
        className="gap-1 text-xs"
      >
        {llmVisible ? <Eye className="h-3 w-3" /> : <EyeOff className="h-3 w-3" />}
        LLM
      </Button>

      <Button
        size="sm"
        variant="ghost"
        onClick={() => setPromptInspectorOpen(true)}
        className="text-xs"
      >
        Prompt
      </Button>

      <Button
        size="sm"
        variant={dirty ? 'default' : 'ghost'}
        onClick={() => void handleSave()}
        disabled={!dirty || saving}
        className="gap-1 text-xs"
      >
        {saving ? <RefreshCw className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
        Save
      </Button>

      <PromptInspectorDialog
        open={promptInspectorOpen}
        onOpenChange={setPromptInspectorOpen}
      />
    </header>
  )
}
