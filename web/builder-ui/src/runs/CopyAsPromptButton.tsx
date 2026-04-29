import { useState, useRef, useEffect } from 'react'
import { Copy, Check } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { apiFetchExportMd } from '@/api/runs'
import { buildMarkdownExport } from '@/lib/markdownExport'
import type { RunDetail } from '@/types/run'

interface CopyAsPromptButtonProps {
  run: RunDetail
}

/**
 * Copies the run's markdown export to the clipboard. Source of truth is the
 * server endpoint `/api/runs/{id}/export.md` so the result mirrors what the
 * `vc debug` CLI produces and never drifts from the backend's error-summary
 * fallback semantics. If the network call fails (offline / dev preview), we
 * fall back to the local `buildMarkdownExport` so the button is never dead.
 */
export function CopyAsPromptButton({ run }: CopyAsPromptButtonProps) {
  const [copied, setCopied] = useState(false)
  const [busy, setBusy] = useState(false)
  const resetTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    return () => {
      if (resetTimer.current) clearTimeout(resetTimer.current)
    }
  }, [])

  async function fetchMarkdown(): Promise<string> {
    try {
      return await apiFetchExportMd(run.run_id)
    } catch (e) {
      console.warn('export.md fetch failed, falling back to client render:', e)
      return buildMarkdownExport(run)
    }
  }

  async function handleClick() {
    if (busy) return
    setBusy(true)
    try {
      const md = await fetchMarkdown()
      await navigator.clipboard.writeText(md)
      setCopied(true)
      toast.success('Copied to clipboard')
      if (resetTimer.current) clearTimeout(resetTimer.current)
      resetTimer.current = setTimeout(() => setCopied(false), 2000)
    } catch {
      toast.error('Failed to copy — check clipboard permissions')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Button
      size="sm"
      variant="outline"
      className="text-xs gap-1 h-7"
      onClick={handleClick}
      disabled={busy}
    >
      {copied ? <Check className="h-3 w-3 text-green-400" /> : <Copy className="h-3 w-3" />}
      Copy as prompt
    </Button>
  )
}
