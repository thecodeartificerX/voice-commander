import { useState, useRef, type KeyboardEvent } from 'react'
import { X } from 'lucide-react'
import { cn } from '@/lib/cn'

interface SynonymsEditorProps {
  value: string[]
  onChange: (next: string[]) => void
  placeholder?: string
  className?: string
}

/**
 * Chip-style tag input for editing a list of spoken phrases.
 *
 * - Enter or comma commits the trailing draft as a new chip.
 * - Backspace on an empty draft removes the last chip.
 * - Click ✕ on a chip to remove it.
 * - Empty / whitespace-only / case-insensitive duplicates are silently ignored.
 *
 * Phrases are stored as the user typed them — match logic is already
 * case-insensitive via `_normalize_spoken` in `verb_router.py`.
 */
export function SynonymsEditor({
  value,
  onChange,
  placeholder = 'add phrase, press Enter…',
  className,
}: SynonymsEditorProps) {
  const [draft, setDraft] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  function commitDraft() {
    const trimmed = draft.trim()
    if (!trimmed) {
      setDraft('')
      return
    }
    const exists = value.some((v) => v.toLowerCase() === trimmed.toLowerCase())
    if (!exists) {
      onChange([...value, trimmed])
    }
    setDraft('')
  }

  function removeAt(index: number) {
    onChange(value.filter((_, i) => i !== index))
  }

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault()
      commitDraft()
    } else if (e.key === 'Backspace' && draft === '' && value.length > 0) {
      e.preventDefault()
      removeAt(value.length - 1)
    }
  }

  return (
    <div
      className={cn(
        'flex flex-wrap items-center gap-1 rounded-md border border-border bg-secondary px-2 py-1.5 min-h-[2.25rem] cursor-text focus-within:border-ring focus-within:ring-1 focus-within:ring-ring',
        className,
      )}
      onClick={() => inputRef.current?.focus()}
      role="list"
    >
      {value.map((phrase, i) => (
        <span
          key={`${phrase}-${i}`}
          role="listitem"
          className="inline-flex items-center gap-1 rounded bg-accent text-accent-foreground text-xs px-1.5 py-0.5 font-mono"
        >
          <span>{phrase}</span>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              removeAt(i)
            }}
            className="text-muted-foreground hover:text-foreground"
            aria-label={`Remove phrase ${phrase}`}
          >
            <X className="h-3 w-3" />
          </button>
        </span>
      ))}
      <input
        ref={inputRef}
        type="text"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={handleKeyDown}
        onBlur={commitDraft}
        placeholder={value.length === 0 ? placeholder : ''}
        className="flex-1 min-w-[8rem] bg-transparent text-xs text-foreground placeholder:text-muted-foreground focus:outline-none"
        spellCheck={false}
        autoCapitalize="off"
        autoCorrect="off"
        aria-label="Synonyms"
      />
    </div>
  )
}
