import { useState } from 'react'
import { Copy, Check } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { buildMarkdownExport } from '@/lib/markdownExport'
import type { RunDetail } from '@/types/run'

interface CopyAsPromptButtonProps {
  run: RunDetail
}

export function CopyAsPromptButton({ run }: CopyAsPromptButtonProps) {
  const [copied, setCopied] = useState(false)

  async function handleClick() {
    const md = buildMarkdownExport(run)
    try {
      await navigator.clipboard.writeText(md)
      setCopied(true)
      toast.success('Copied to clipboard')
      setTimeout(() => setCopied(false), 2000)
    } catch {
      toast.error('Failed to copy — check clipboard permissions')
    }
  }

  return (
    <Button size="sm" variant="outline" className="text-xs gap-1 h-7" onClick={handleClick}>
      {copied ? <Check className="h-3 w-3 text-green-400" /> : <Copy className="h-3 w-3" />}
      Copy as prompt
    </Button>
  )
}
