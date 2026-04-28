import { useEffect, useState } from 'react'
import { apiGetPromptData } from '@/api/prompt'

type PromptData = Awaited<ReturnType<typeof apiGetPromptData>>

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function PromptInspectorDialog({ open, onOpenChange }: Props) {
  const [data, setData] = useState<PromptData | null>(null)

  useEffect(() => {
    if (open && !data) {
      apiGetPromptData().then(setData).catch(console.error)
    }
  }, [open, data])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={() => onOpenChange(false)}
    >
      <div
        className="bg-card border border-border rounded-lg p-6 w-[680px] max-h-[80vh] overflow-y-auto shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-base font-semibold mb-3">Prompt Inspector</h2>
        {!data ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : (
          <div className="space-y-4">
            <div>
              <div className="text-xs text-muted-foreground mb-1">
                Model: {data.model_id} · Tools: {data.tools_count} · Endpoint:{' '}
                {data.endpoint_url}
              </div>
            </div>
            <div>
              <div className="text-xs font-medium mb-1">Resolved prompt</div>
              <pre className="text-[10px] bg-muted rounded p-3 overflow-x-auto whitespace-pre-wrap break-words max-h-60">
                {data.template_resolved}
              </pre>
            </div>
          </div>
        )}
        <button
          className="mt-4 text-xs text-muted-foreground hover:text-foreground"
          onClick={() => onOpenChange(false)}
        >
          Close
        </button>
      </div>
    </div>
  )
}
