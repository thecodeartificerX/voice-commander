import { useState } from 'react'
import { Save, Eye, EyeOff, RefreshCw } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { useGraphStore } from '@/store/graphStore'
import { useUiStore } from '@/store/uiStore'
import { PromptInspectorDialog } from './PromptInspectorDialog'

export function Toolbar() {
  const { graphId, dirty, save, llmVisible, toggleLlmVisible } = useGraphStore()
  const { promptInspectorOpen, setPromptInspectorOpen } = useUiStore()
  const [saving, setSaving] = useState(false)

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

  return (
    <header className="h-10 border-b border-border flex items-center px-3 gap-2 shrink-0 bg-card">
      <span className="text-sm font-semibold text-foreground mr-2">
        {graphId ?? 'Voice Commander — Builder'}
      </span>
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
