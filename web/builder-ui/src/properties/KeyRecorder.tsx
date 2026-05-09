import { useCallback, useEffect, useRef, useState } from 'react'

interface KeyRecorderProps {
  value: string
  onChange: (combo: string) => void
}

function browserKeyToPyautogui(e: KeyboardEvent): string | null {
  const k = e.key
  if (k.length === 1) {
    if (k === ' ') return 'space'
    return k.toLowerCase()
  }
  const map: Record<string, string> = {
    Escape: 'esc',
    Enter: 'enter',
    Tab: 'tab',
    Backspace: 'backspace',
    Delete: 'delete',
    Insert: 'insert',
    Home: 'home',
    End: 'end',
    PageUp: 'pageup',
    PageDown: 'pagedown',
    ArrowUp: 'up',
    ArrowDown: 'down',
    ArrowLeft: 'left',
    ArrowRight: 'right',
    CapsLock: 'capslock',
    ScrollLock: 'scrolllock',
    NumLock: 'numlock',
  }
  if (map[k]) return map[k]
  if (/^F\d{1,2}$/.test(k)) return k.toLowerCase()
  return null
}

function isModifier(e: KeyboardEvent): boolean {
  return ['Control', 'Alt', 'Shift', 'Meta', 'OS'].includes(e.key)
}

function chordFromEvent(e: KeyboardEvent): string | null {
  const mods: string[] = []
  if (e.ctrlKey) mods.push('ctrl')
  if (e.altKey) mods.push('alt')
  if (e.shiftKey) mods.push('shift')
  if (e.metaKey) mods.push('win')
  const main = browserKeyToPyautogui(e)
  if (!main) return null
  if (isModifier(e)) {
    return mods.length > 0 ? mods.join('+') : null
  }
  return [...mods, main].join('+')
}

export function KeyRecorder({ value, onChange }: KeyRecorderProps) {
  const [recording, setRecording] = useState(false)
  const [typeMode, setTypeMode] = useState(false)
  const containerRef = useRef<HTMLDivElement | null>(null)

  const stop = useCallback(() => setRecording(false), [])

  useEffect(() => {
    if (!recording) return
    const onKeyDown = (e: KeyboardEvent) => {
      e.preventDefault()
      e.stopPropagation()
      if (e.key === 'Escape' && !e.ctrlKey && !e.altKey && !e.shiftKey && !e.metaKey) {
        stop()
        return
      }
      if (isModifier(e)) return
      const combo = chordFromEvent(e)
      if (combo) {
        onChange(combo)
        stop()
      }
    }
    window.addEventListener('keydown', onKeyDown, true)
    return () => window.removeEventListener('keydown', onKeyDown, true)
  }, [recording, onChange, stop])

  useEffect(() => {
    if (!recording) return
    const onClickAway = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        stop()
      }
    }
    window.addEventListener('mousedown', onClickAway, true)
    return () => window.removeEventListener('mousedown', onClickAway, true)
  }, [recording, stop])

  return (
    <div ref={containerRef} className="flex flex-col gap-1">
      {typeMode ? (
        <input
          autoFocus
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="e.g. win+l, ctrl+shift+t"
          className="h-6 w-full rounded border border-border bg-background px-2 text-xs font-mono"
        />
      ) : (
        <button
          type="button"
          onClick={() => setRecording((r) => !r)}
          className={
            'h-6 w-full rounded border px-2 text-xs font-mono text-left ' +
            (recording
              ? 'border-amber-400 bg-amber-950/30 text-amber-200 animate-pulse'
              : 'border-border bg-background hover:bg-accent')
          }
          title={recording ? 'Press a key combination — Esc to cancel' : 'Click to record a combo'}
        >
          {recording ? 'recording… press combo' : value || '(unset)'}
        </button>
      )}
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={() => {
            setTypeMode((t) => !t)
            if (recording) stop()
          }}
          className="h-5 rounded border border-border px-2 text-[10px] hover:bg-accent"
          title={
            typeMode
              ? 'Switch to record mode'
              : 'Switch to type mode (use when OS swallows the key, e.g. Win)'
          }
        >
          {typeMode ? '🎙 record' : '✏ type'}
        </button>
        {value && !recording ? (
          <button
            type="button"
            onClick={() => onChange('')}
            className="h-5 rounded border border-border px-2 text-[10px] hover:bg-accent"
            title="Clear combo"
          >
            ✕ clear
          </button>
        ) : null}
      </div>
    </div>
  )
}
