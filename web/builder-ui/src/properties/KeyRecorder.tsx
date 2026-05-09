import { useCallback, useEffect, useRef, useState } from 'react'

import { sseSubscribe } from '@/api/sse'

interface KeyRecorderProps {
  value: string
  onChange: (combo: string) => void
}

// ---------------------------------------------------------------------------
// Browser fallback — runs when the daemon endpoint is unreachable. Limited
// because browser chrome shortcuts (Ctrl+L, Ctrl+T, F11, ...) are caught by
// the browser before keydown reaches the page; preventDefault cannot stop
// them. The backend recorder uses a Windows low-level keyboard hook and does
// not have this limitation, except for SAS chords (Ctrl+Alt+Del, Win+L).
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Backend recorder — POST /key_recorder/start, await SSE event with combo.
// ---------------------------------------------------------------------------

type StartResult =
  | { kind: 'started' }
  | { kind: 'busy' }
  | { kind: 'unavailable' } // 503 / network error → fall back to browser path
  | { kind: 'error'; message: string }

async function startBackendRecorder(): Promise<StartResult> {
  try {
    const resp = await fetch('/key_recorder/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    })
    if (resp.status === 202) return { kind: 'started' }
    if (resp.status === 409) return { kind: 'busy' }
    if (resp.status === 503) return { kind: 'unavailable' }
    return { kind: 'error', message: `HTTP ${resp.status}` }
  } catch {
    return { kind: 'unavailable' }
  }
}

async function cancelBackendRecorder(): Promise<void> {
  try {
    await fetch('/key_recorder/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    })
  } catch {
    // best-effort — recorder will time out on its own
  }
}

export function KeyRecorder({ value, onChange }: KeyRecorderProps) {
  const [recording, setRecording] = useState(false)
  const [usingFallback, setUsingFallback] = useState(false)
  const [statusHint, setStatusHint] = useState<string | null>(null)
  const [typeMode, setTypeMode] = useState(false)
  const containerRef = useRef<HTMLDivElement | null>(null)

  // Stable refs so the SSE subscription effect only re-runs on `recording`.
  const onChangeRef = useRef(onChange)
  onChangeRef.current = onChange

  const stopRecording = useCallback(() => {
    setRecording(false)
    setUsingFallback(false)
    setStatusHint(null)
  }, [])

  // Backend SSE listener — only active while recording in non-fallback mode.
  useEffect(() => {
    if (!recording || usingFallback) return
    const unsubs: Array<() => void> = []
    unsubs.push(
      sseSubscribe('key_recorder_captured', (data) => {
        const combo = (data as { combo?: string } | null)?.combo
        if (typeof combo === 'string' && combo.length > 0) {
          onChangeRef.current(combo)
        }
        stopRecording()
      }),
    )
    unsubs.push(
      sseSubscribe('key_recorder_cancelled', () => stopRecording()),
    )
    unsubs.push(
      sseSubscribe('key_recorder_timeout', () => stopRecording()),
    )
    unsubs.push(
      sseSubscribe('key_recorder_failed', (data) => {
        const reason = (data as { reason?: string } | null)?.reason ?? 'unknown key'
        setStatusHint(`failed: ${reason}`)
        setRecording(false)
        setUsingFallback(false)
      }),
    )
    return () => {
      for (const u of unsubs) u()
    }
  }, [recording, usingFallback, stopRecording])

  // Browser fallback — direct keydown capture when the backend is unavailable.
  useEffect(() => {
    if (!recording || !usingFallback) return
    const onKeyDown = (e: KeyboardEvent) => {
      e.preventDefault()
      e.stopPropagation()
      if (e.key === 'Escape' && !e.ctrlKey && !e.altKey && !e.shiftKey && !e.metaKey) {
        stopRecording()
        return
      }
      if (isModifier(e)) return
      const combo = chordFromEvent(e)
      if (combo) {
        onChangeRef.current(combo)
        stopRecording()
      }
    }
    window.addEventListener('keydown', onKeyDown, true)
    return () => window.removeEventListener('keydown', onKeyDown, true)
  }, [recording, usingFallback, stopRecording])

  // Click-away cancels the active session (both modes).
  useEffect(() => {
    if (!recording) return
    const onClickAway = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        if (!usingFallback) void cancelBackendRecorder()
        stopRecording()
      }
    }
    window.addEventListener('mousedown', onClickAway, true)
    return () => window.removeEventListener('mousedown', onClickAway, true)
  }, [recording, usingFallback, stopRecording])

  // Esc cancels the backend session (browser path handles its own Esc above).
  useEffect(() => {
    if (!recording || usingFallback) return
    const onDocKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        void cancelBackendRecorder()
        stopRecording()
      }
    }
    window.addEventListener('keydown', onDocKey)
    return () => window.removeEventListener('keydown', onDocKey)
  }, [recording, usingFallback, stopRecording])

  // Cleanup on unmount: cancel any in-flight backend session.
  useEffect(() => {
    return () => {
      if (recording && !usingFallback) void cancelBackendRecorder()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const beginRecording = useCallback(async () => {
    setStatusHint(null)
    const result = await startBackendRecorder()
    switch (result.kind) {
      case 'started':
        setUsingFallback(false)
        setRecording(true)
        return
      case 'busy':
        setStatusHint('another recorder is active')
        return
      case 'unavailable':
        setUsingFallback(true)
        setRecording(true)
        setStatusHint('local capture (Win/Ctrl+L may be swallowed)')
        return
      case 'error':
        setStatusHint(result.message)
        return
    }
  }, [])

  const toggleRecording = useCallback(() => {
    if (recording) {
      if (!usingFallback) void cancelBackendRecorder()
      stopRecording()
    } else {
      void beginRecording()
    }
  }, [recording, usingFallback, beginRecording, stopRecording])

  const recordTitle = recording
    ? 'Press a key combination — Esc to cancel'
    : 'Click to record. Ctrl+Alt+Del and Win+L cannot be captured by any app.'

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
          onClick={toggleRecording}
          className={
            'h-6 w-full rounded border px-2 text-xs font-mono text-left ' +
            (recording
              ? 'border-amber-400 bg-amber-950/30 text-amber-200 animate-pulse'
              : 'border-border bg-background hover:bg-accent')
          }
          title={recordTitle}
        >
          {recording ? 'recording… press combo' : value || '(unset)'}
        </button>
      )}
      {statusHint ? (
        <div className="text-[10px] text-muted-foreground">{statusHint}</div>
      ) : null}
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={() => {
            setTypeMode((t) => !t)
            if (recording) {
              if (!usingFallback) void cancelBackendRecorder()
              stopRecording()
            }
          }}
          className="h-5 rounded border border-border px-2 text-[10px] hover:bg-accent"
          title={
            typeMode
              ? 'Switch to record mode'
              : 'Switch to type mode (use for Ctrl+Alt+Del / Win+L)'
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
