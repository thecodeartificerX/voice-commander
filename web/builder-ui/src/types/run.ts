export type RunStatus = 'ok' | 'miss' | 'error' | 'running'

export type ErrorCategory = 'program' | 'wiring' | 'infra'

export interface RunSummary {
  run_id: string
  started_at: number
  ended_at: number | null
  duration_ms: number | null
  transcript: string
  status: RunStatus
  error_category: ErrorCategory | null
  error_summary: string | null
  step_count?: number
  graph?: string | null
  daemon_pid: number
  schema_version: number
}

export interface SpanRecord {
  span_id: string
  run_id: string
  parent_span_id: string | null
  type: string
  name: string
  started_at: number
  ended_at: number | null
  duration_ms: number | null
  status: 'ok' | 'error' | 'skipped' | 'running'
  attrs: Record<string, unknown>
  output: unknown
  error_type: string | null
  error_msg: string | null
  traceback: string | null
  error_category: ErrorCategory | null
}

export interface RunDetail extends RunSummary {
  spans: SpanRecord[]
}
