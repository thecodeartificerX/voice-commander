# ADR 0070: Observability pipeline — live tracing + replay

**Date:** 2026-04-27
**Status:** Accepted

## Context

Per-utterance debugging today relies on rotating logs and `outputs/last_plan.json`. Diagnosing a misbehaving graph node, an LLM that returned a bad plan, or a cross-graph call gone wrong required grepping logs and reconstructing the timeline by hand. There was no machine-readable record of the run.

## Decision

Add an in-process Tracer that captures every utterance as a span tree persisted to SQLite (`outputs/runs.db`, WAL). Spans cover: `run`, `transcribe`, `llm_call` (with full prompt + raw response), `plan`, `tool_call`, `graph`, `node`. Live trace events broadcast on the existing EventBus under the `trace.*` namespace; existing subscribers (sprite) ignore them. New surfaces:

- `/api/runs/*` REST + `/api/runs/stream` SSE
- `vc debug` and `vc tail` CLIs
- `/page/runs` web inspector
- Builder UI overlay with live highlighting

Replay: LLM-only (safe, re-routes through current router) is unfettered; full re-fire is gated by `X-Replay-Confirm: yes` header / `--yes` flag.

## Consequences

* **Storage** — SQLite WAL, ~5 KB per run, default retention 1000 runs ≈ 5 MB. Pruned automatically every 50 inserts.
* **Performance** — single-thread async writer; instrumentation never blocks the audio or pipeline threads. Worst-case overhead ~0.5 ms per utterance.
* **Privacy** — all transcripts, clipboard captures, and OCR output stored verbatim. Acceptable for single-user local daemon. Revisit if cloud sync is ever introduced.
* **Replay risk** — full replay re-fires keystrokes into the current foreground window. Gated; never enabled by default.
* **No new deps.** stdlib `sqlite3` + `contextvars`.

## Alternatives considered

* **JSONL append-only.** Simpler, grep-friendly. Rejected — UI needs ad-hoc filter queries (status, graph, since, transcript regex) that SQL handles cleanly.
* **OpenTelemetry / Jaeger.** Overkill for a single-process daemon; introduces transport, encoding, and ops surface for zero benefit at this scale.
* **Skipping graph-node spans.** Loses the Builder overlay's main value (visualising which node failed inside a graph).

## Migration

Additive only — no existing code paths change semantics. `outputs/last_plan.json` is preserved.
