type Handler = (data: unknown) => void

const handlers = new Map<string, Set<Handler>>()
let es: EventSource | null = null
let backoffMs = 1000
let reconnectTimer: ReturnType<typeof setTimeout> | null = null

function connect(onConnect?: (connected: boolean) => void) {
  if (es) return
  es = new EventSource('/events')

  es.onopen = () => {
    backoffMs = 1000
    onConnect?.(true)
    dispatch('connection', { connected: true })
  }

  es.onerror = () => {
    es?.close()
    es = null
    onConnect?.(false)
    dispatch('connection', { connected: false })
    if (reconnectTimer) clearTimeout(reconnectTimer)
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      connect(onConnect)
    }, backoffMs)
    backoffMs = Math.min(backoffMs * 2, 30_000)
  }

  es.onmessage = (ev) => {
    try {
      dispatch('message', JSON.parse(ev.data as string))
    } catch (e) {
      console.warn('SSE: malformed event data, dropping:', ev.data, e)
    }
  }

  // Named event types from EventBus
  const eventTypes = [
    'run.appended',
    'trace.run_started',
    'trace.span_ended',
    'trace.run_completed',
    'key_recorder_started',
    'key_recorder_captured',
    'key_recorder_cancelled',
    'key_recorder_timeout',
    'key_recorder_failed',
  ]
  for (const type of eventTypes) {
    es.addEventListener(type, (ev: MessageEvent) => {
      try {
        dispatch(type, JSON.parse(ev.data as string))
      } catch (e) {
        console.warn('SSE: malformed event data, dropping:', ev.data, e)
      }
    })
  }
}

function dispatch(type: string, data: unknown) {
  handlers.get(type)?.forEach((h) => h(data))
}

export function sseSubscribe(type: string, handler: Handler): () => void {
  if (!handlers.has(type)) handlers.set(type, new Set())
  handlers.get(type)!.add(handler)
  return () => {
    handlers.get(type)?.delete(handler)
  }
}

export function sseConnect(onStatusChange?: (connected: boolean) => void) {
  connect(onStatusChange)
}

export function sseDisconnect() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  es?.close()
  es = null
}
