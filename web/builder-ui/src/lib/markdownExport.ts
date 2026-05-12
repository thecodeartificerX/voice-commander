/**
 * Fallback / preview-only markdown renderer.
 *
 * The canonical export lives server-side at `/api/runs/{id}/export.md` (see
 * `apiFetchExportMd`); the React UI calls that endpoint via `CopyAsPromptButton`
 * to keep parity with `vc debug` / `vc tail`. This module is retained for
 * offline mode (no network) and unit-test parity checks. Behavior should mirror
 * the server's error-summary fallback (`error_summary || error_msg`), but if
 * the two ever drift, the server is authoritative.
 */
import type { RunDetail, SpanRecord } from '@/types/run'
import { formatDuration } from './timeFormat'

function isoNow(): string {
  return new Date().toISOString()
}

function renderSpanTree(spans: SpanRecord[], parentId: string | null, depth: number): string {
  const children = spans.filter((s) => s.parent_span_id === parentId)
  return children
    .map((s) => {
      const indent = '  '.repeat(depth)
      const status = s.status === 'ok' ? '✓' : s.status === 'error' ? '✗' : s.status
      const dur = formatDuration(s.duration_ms)
      let line = `${indent}- ${status} ${s.name} · ${dur} · ${s.status}`
      const children_md = renderSpanTree(spans, s.span_id, depth + 1)
      if (s.error_type) {
        line += `\n${indent}  - error_type: ${s.error_type}`
      }
      if (s.error_msg) {
        line += `\n${indent}  - error_msg: ${s.error_msg}`
      }
      if (s.error_category) {
        line += `\n${indent}  - error_category: ${s.error_category}`
      }
      if (s.attrs['node_id']) {
        line += `\n${indent}  - canvas node id: ${String(s.attrs['node_id'])}`
      }
      if (children_md) {
        line += '\n' + children_md
      }
      return line
    })
    .join('\n')
}

function buildFailureSummary(run: RunDetail): string {
  if (run.status !== 'error') return ''
  const errorSpan = run.spans
    .slice()
    .reverse()
    .find((s) => s.status === 'error')
  if (!errorSpan) return ''
  const cat = run.error_category ?? 'unknown'
  const catText: Record<string, string> = {
    wiring: 'fix the graph, not a tool',
    program: 'fix the tool implementation or its dependencies',
    infra: 'check service health, restart daemon, replug device',
  }
  const fix = catText[cat] ?? 'investigate the error'
  return `The graph failed at span \`${errorSpan.name}\` because: ${errorSpan.error_msg ?? 'unknown error'}. This is a **${cat} error** — ${fix}.`
}

export function buildMarkdownExport(run: RunDetail): string {
  const shortId = run.run_id.slice(0, 6)
  const startIso = new Date(run.started_at * 1000).toISOString()
  const dur = formatDuration(run.duration_ms)
  const spanTree = renderSpanTree(run.spans, null, 0)
  const failureSummary = buildFailureSummary(run)

  let md = `# Voice Commander run ${shortId} — ${run.status}\n\n`
  md += `**Transcript:** "${run.transcript}"\n`
  md += `**Started:** ${startIso}\n`
  md += `**Duration:** ${dur}\n`
  md += `**Status:** ${run.status}\n`
  if (run.error_category) {
    md += `**Error category:** ${run.error_category}\n`
  }
  if (run.error_summary) {
    md += `**Error summary:** ${run.error_summary}\n`
  }
  md += `**Daemon PID:** ${run.daemon_pid}\n`
  md += `**Schema:** runs.db v${run.schema_version}\n`

  md += `\n## Span tree\n\n${spanTree}\n`

  if (failureSummary) {
    md += `\n## Failure summary\n\n${failureSummary}\n`
  }

  md += `\n---\n\n_Generated ${isoNow()} by Voice Commander Builder UI_\n`
  return md
}
