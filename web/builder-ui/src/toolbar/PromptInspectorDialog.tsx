import { useEffect, useState } from 'react'
import * as DialogPrimitive from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { cn } from '@/lib/cn'
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

  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay
          className={cn(
            'fixed inset-0 z-50 bg-black/60',
            'data-[state=open]:animate-in data-[state=closed]:animate-out',
            'data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0',
          )}
        />
        <DialogPrimitive.Content
          className={cn(
            'fixed left-[50%] top-[50%] z-50 grid w-[680px] max-h-[80vh] translate-x-[-50%] translate-y-[-50%]',
            'gap-4 overflow-y-auto rounded-lg border border-border bg-card p-6 shadow-xl',
            'data-[state=open]:animate-in data-[state=closed]:animate-out',
            'data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0',
          )}
        >
          <DialogPrimitive.Title className="text-base font-semibold">
            Prompt Inspector
          </DialogPrimitive.Title>
          <DialogPrimitive.Description className="sr-only">
            Inspect the resolved system prompt, model, and tool count delivered to LM
            Studio.
          </DialogPrimitive.Description>

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

          <DialogPrimitive.Close
            className={cn(
              'absolute right-3 top-3 rounded-sm opacity-70 transition-opacity',
              'hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-ring',
            )}
            aria-label="Close prompt inspector"
          >
            <X className="h-4 w-4" />
          </DialogPrimitive.Close>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
