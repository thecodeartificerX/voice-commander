# Observability Pipeline — Design Spec

**Date:** 2026-04-27
**Status:** Approved, ready for plan
**Scope:** Live tracing, replay, terminal tail, web UI inspector, agent-debuggable API. Eval scoring suite is out of scope (future phase).

---

## 1. Problem

Today, when a voice command misbehaves, the only artefacts are `outputs/last_plan.json` (transcript + final plan), the rotating daemon log, and an audio miss-chime. There is no per-step record of what the LLM proposed, which tool calls fired, which graph node failed, what arguments resolved to, what error was raised, or how long each step took. The user — and AI agents debugging on the user's behalf — cannot pull up "exactly what happened in the last session" without grepping logs.

We need a live, persistent observability layer that captures every utterance end-to-end as a structured run with a span tree, exposes it in the terminal as it happens, in the web UI for visual inspection (Builder overlay + standalone runs page), and via an HTTP/CLI API for agent-driven debugging.

## 2. Non-goals

- **No regression eval suite** in this spec. Fixture transcripts + plan-equivalence scoring is a separate follow-up that will consume this layer's data, not extend it.
- **No PII redaction.** Single-user local daemon, no network egress; raw transcripts, clipboard, and OCR output are stored verbatim. Revisit if cloud sync ever lands.
- **No token-level LLM tracing.** LM Studio is non-streaming for tool calls; per-token timing has no debugging value at this layer.
- **No distributed tracing.** All spans live in one process. No OpenTelemetry adoption — context is propagated via Python `contextvars`.

## 3. Architecture

### 3.1 Component map

```
┌─────────── daemon process ─────────────────────────────────┐
│                                                            │
│   observability/                                           │
│     tracer.py    Tracer singleton, contextvars-based       │
│     store.py     SQLite writer (WAL), schema, prune        │
│     replay.py    LLM-replay + full-replay executors        │
│     api.py       FastAPI router /api/runs/*                │
│     cli.py       vc debug + vc tail subcommands            │
│                                                            │
│   Instrumentation seams (existing modules edited):         │
│     daemon.StreamingDaemon._process_utterance              │
│     transcriber.Transcriber.transcribe                     │
│     llm_router.LLMRouter.route                             │
│     dispatcher.run_plan                                    │
│     commands/graph_runtime.GraphRuntime.run                │
│                                                            │
└────────────────┬─────────────────────────┬─────────────────┘
                 │                         │
            outputs/runs.db          EventBus (existing)
            (SQLite, WAL)             trace.run_started
                 │                    trace.span_started
                 │                    trace.span_ended
                 │                    trace.run_completed
                 │                         │
        ┌────────┴─────┐         SSE /events (existing)
        │              │                   │
   /api/runs/*    vc debug CLI       Builder UI overlay
   (REST)         (HTTP wrapper)     /page/runs UI
                                     sprite (ignores trace.*)
```

### 3.2 Span tree shape

One **run** per utterance. Span tree:

```
run                        type="run"      transcript, status, duration_ms
├── transcribe             type="transcribe"   confidence, text
├── llm_call               type="llm_call"     prompt_hash, model, latency, plan
└── plan                   type="plan"     strict, n_steps
    ├── tool_call          type="tool_call"    tool_name, kwargs, settle_ms, result
    ├── tool_call          type="tool_call"    (graph-backed; has children)
    │   └── graph          type="graph"        graph_name, kind
    │       ├── node       type="node"     node_id, ref, kwargs, port_outputs
    │       ├── node       type="node"
    │       └── ...
    └── tool_call          type="tool_call"
```

Granularity = "medium" per brainstorm decision: pipeline primitives **inside** a graph node are not given individual spans (the node's `port_outputs` and any error message capture enough). Direct (non-graph) tool calls in the top-level plan get one span each. Sub-graph calls (`command.X` from inside a workflow) nest naturally — each is a `tool_call` whose child is a `graph` span.

### 3.3 Tracer API

`observability/tracer.py`:

```python
class Tracer:
    def __init__(self, store: Store, bus: EventBus): ...

    def start_run(self, transcript: str) -> RunHandle: ...

    @contextmanager
    def span(self, type: str, **attrs) -> Span: ...
        # Auto-parents to current contextvar; sets new contextvar; closes on exit;
        # records duration; captures exception via __exit__ → span.status="error".
```

`Span.set_output(value)` / `Span.set_attr(k, v)` / `Span.add_event(name, **data)` mutate the in-memory record before close. On `__exit__`:

1. Compute `duration_ms = monotonic() - start`.
2. If exception unhandled: `status="error"`, `error_type`, `error_msg`, `traceback` (last 10 frames).
3. Persist via `store.write_span(span_record)`.
4. Publish `trace.span_ended` on EventBus.

Context is held in two `contextvars`: `current_run_id`, `current_span_id`. The pipeline worker thread is long-lived and processes one utterance at a time — `start_run()` sets the run-id contextvar at the top of `_process_utterance`, and a `finally` block clears it. All downstream code (transcriber, LLM router, dispatcher, graph runtime) runs synchronously inside that same thread, so they observe the contextvar without any cross-thread propagation. The audio callback and VAD threads do not create spans; only the pipeline worker does.

### 3.4 Storage — SQLite

File: `outputs/runs.db`. Mode: `journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON`.

Schema:

```sql
CREATE TABLE runs (
    run_id          TEXT PRIMARY KEY,         -- short uuid (8 chars)
    started_at      REAL NOT NULL,            -- unix epoch
    ended_at        REAL,
    transcript      TEXT NOT NULL,
    status          TEXT NOT NULL,            -- running|ok|error|partial
    error_msg       TEXT,
    duration_ms     INTEGER,
    daemon_pid      INTEGER NOT NULL,
    schema_version  INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX idx_runs_started ON runs(started_at DESC);
CREATE INDEX idx_runs_status  ON runs(status, started_at DESC);

CREATE TABLE spans (
    span_id         TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    parent_span_id  TEXT,                     -- nullable for root run-span
    type            TEXT NOT NULL,            -- run|transcribe|llm_call|plan|tool_call|graph|node
    name            TEXT NOT NULL,            -- tool_name / node_id / "llm_call" / etc.
    started_at      REAL NOT NULL,
    ended_at        REAL,
    duration_ms     INTEGER,
    status          TEXT NOT NULL,            -- ok|error|skipped
    attrs           TEXT NOT NULL,            -- JSON: kwargs, args, model, etc.
    output          TEXT,                     -- JSON: return value
    error_type      TEXT,
    error_msg       TEXT,
    traceback       TEXT
);
CREATE INDEX idx_spans_run     ON spans(run_id, started_at);
CREATE INDEX idx_spans_parent  ON spans(parent_span_id);
CREATE INDEX idx_spans_status  ON spans(status, started_at DESC);
CREATE INDEX idx_spans_name    ON spans(name);
```

Writer is a single thread with a bounded `queue.Queue` — instrumented code paths enqueue rows and return immediately; SQLite I/O never blocks the audio/pipeline threads. Queue overflow drops the span and increments a counter logged at INFO; a dropped span never crashes the daemon.

Retention: last N runs (default 1000, config `[observability] keep_runs`). Prune runs synchronously on `run_completed` after every 50 inserts; `ON DELETE CASCADE` removes their spans.

### 3.5 LLM call capture

`LLMRouter.route()` records:

- `attrs.model` — current model id from config
- `attrs.prompt_hash` — sha256 of system prompt (16 hex chars)
- `attrs.prompt_full` — full system prompt + transcript (raw, no redaction per §2)
- `attrs.tools_array` — JSON list of tool names sent to LM Studio (filtering result)
- `attrs.raw_response` — raw `chat/completions` response body (`Plan.raw_response`)
- `output` — parsed `Plan.steps` as `[{tool, kwargs}, ...]`
- `attrs.timeout` and `error_type="TimeoutException"` on timeout

This is what makes LLM-replay possible: `replay.py` reads `attrs.prompt_full` + `transcript` from the run, re-POSTs against current `LLMRouter`, diffs new plan vs old `output`.

### 3.6 EventBus integration

Existing EventBus gains four new event types (under `trace.` prefix so existing subscribers ignore them by default):

| Event                | Payload                                                            |
|----------------------|--------------------------------------------------------------------|
| `trace.run_started`  | `{run_id, transcript, started_at}`                                 |
| `trace.span_started` | `{run_id, span_id, parent_span_id, type, name, started_at, attrs}` |
| `trace.span_ended`   | `{run_id, span_id, status, duration_ms, output, error_msg}`        |
| `trace.run_completed`| `{run_id, status, duration_ms, n_spans}`                           |

Builder UI live mode subscribes to these for node highlighting. `/page/runs` uses them for the live runs feed. Sprite remains unaware (it subscribes to specific high-level events, not a wildcard).

### 3.7 Daemon stdout — one-liner per run

After existing transcript/MATCH/MISS lines, dispatcher's existing `PLAN_START`/`PLAN_COMPLETE` hooks gain:

```
run 7f3a2b18 ok        487ms  3 steps   "open chrome and search cats"
run 8a1b04c9 ERROR    1203ms  1/2 steps "do the thing"   FocusWindowError: no match
```

Single line per run, color-coded if stdout is a TTY (`green` ok / `red` error / `yellow` partial). Existing per-step `plan step N/M:` lines stay.

### 3.8 `vc tail` CLI

Subcommand of existing `vc` entry point. Polls `runs.db` (or subscribes via SSE if daemon's web port is discoverable through `outputs/.daemon-port` lockfile-style sentinel) and renders new runs as a tree:

```
─ run 7f3a (12:04:18) "open chrome and search cats" ─ ok 487ms
  transcribe          156ms  conf=0.94
  llm_call            198ms  gemma-4-e4b
  plan
    focus(chrome)      45ms  ok    hwnd=0x002a01b4
    type(cats)         22ms  ok
    press(enter)       18ms  ok
```

Args: `--since DURATION`, `--status STATUS`, `--graph NAME`, `--follow` (default), `--no-follow` to dump and exit.

### 3.9 `vc debug` CLI

Subcommand for one-shot queries (agents, scripts):

```
vc debug last [--json|--tree]            last run, default tree
vc debug run <id> [--json|--tree]        specific run
vc debug runs [--limit N --status S]     list
vc debug errors [--since 1h]             error runs only
vc debug grep <text>                     transcript regex search
vc debug llm <id>                        full prompt + response of run's llm_call
vc debug replay-llm <id>                 re-route through current LLMRouter, show diff
vc debug replay-full <id> --yes          re-execute full plan (footgun, see §4.5)
vc debug prune [--keep N]                manual prune
```

CLI is a thin shell over the REST API: each subcommand maps to one HTTP call, output formatting client-side. If the daemon isn't running, falls back to direct SQLite read for read-only commands.

### 3.10 REST API — `/api/runs/*`

| Method | Path                              | Returns                                          |
|--------|-----------------------------------|--------------------------------------------------|
| GET    | `/api/runs`                       | list, query params: `limit`, `status`, `graph`, `since`, `q` (transcript regex) |
| GET    | `/api/runs/last`                  | most recent run with full span tree              |
| GET    | `/api/runs/{run_id}`              | run + full span tree                             |
| GET    | `/api/runs/{run_id}/llm`          | LLM call span: prompt, response, plan            |
| GET    | `/api/runs/stream`                | SSE: live `trace.*` events filtered for UI       |
| POST   | `/api/runs/{run_id}/replay-llm`   | LLM-replay; returns `{old_plan, new_plan, diff}` |
| POST   | `/api/runs/{run_id}/replay-full`  | requires header `X-Replay-Confirm: yes`; re-fires plan; returns new run_id |
| POST   | `/api/runs/prune`                 | manual prune; body `{keep: N}`                   |

Run response shape:

```json
{
  "run_id": "7f3a2b18",
  "started_at": 1745765058.412,
  "duration_ms": 487,
  "status": "ok",
  "transcript": "open chrome and search cats",
  "spans": [
    {"span_id": "...", "parent_span_id": null, "type": "run", "name": "run", ...},
    {"span_id": "...", "parent_span_id": "...", "type": "transcribe", ...},
    ...
  ]
}
```

Spans returned as flat list with `parent_span_id` — client builds tree. Avoids recursive JSON nesting and matches DB shape.

### 3.11 Web UI

#### `/page/runs` — standalone runs inspector

Server-rendered HTMX page (consistent with ADR 0022). Three regions:

1. **List (left, scrollable):** rows = run_id, time, status badge, duration, transcript snippet. Filter bar: status, graph, since, free-text. Auto-prepends new runs from `/api/runs/stream`.
2. **Detail (right, dominant):** selected run's span tree as nested `<details>` blocks. Each span shows name, status, duration, kwargs/output JSON (collapsed). Errors expanded by default, traceback in a `<pre>`.
3. **Action bar (top of detail):** `Replay LLM` button (always shown), `Replay Full` button (hidden behind a "danger" toggle). LLM diff renders inline as a two-pane plan diff.

#### Builder UI overlay

Existing Builder gets a "Runs" panel (collapsible, right side). Lists last 20 runs that exercised the currently-open graph. Clicking a run:

- Paints each node by `node` span status: green = ok, red = error, grey = not visited.
- Failed node's tooltip shows error message inline.
- Edges fired by control flow highlight (already a Drawflow concept).

**Live mode toggle** (default off): subscribes to `/api/runs/stream`. While daemon executes the open graph, nodes pulse as their spans start/end. Terminates highlight on `trace.run_completed`. Implementation: small JS in `static/builder.js` consumes SSE, looks up DOM nodes by `data-node-id`, applies CSS class transitions defined in `static/builder.css`.

## 4. Concerns

### 4.1 Performance

Audio/pipeline threads must never block on SQLite. Tracer enqueues; writer thread drains. Span object creation is cheap (dict + monotonic clock). Worst-case overhead per utterance: ~5-10 spans × ~50µs each ≈ 0.5 ms — negligible against ~500 ms LLM latency.

### 4.2 Thread safety

`contextvars` are thread-local-ish (each `Thread` gets a copy of the active context at spawn). The pipeline worker thread carries the run context for the whole utterance. Spans created inside `Dispatcher.run_plan` and `GraphRuntime.run` (same thread) auto-parent correctly. The transcribe span lives in the pipeline worker too.

### 4.3 Crash recovery

If the daemon dies mid-run, the run row stays `status='running'` with no `ended_at`. On daemon startup, `store.py` runs `UPDATE runs SET status='error', error_msg='daemon crash' WHERE status='running' AND daemon_pid != ?` against the new pid. Cleans up zombies without losing partial spans.

### 4.4 DB corruption

WAL mode + `synchronous=NORMAL` is durable enough for local logs. `outputs/runs.db` is gitignored; deleting it costs nothing. Nuke-and-recreate path: if `store.open()` fails to read schema, backup file to `runs.db.corrupt-<ts>` and recreate empty. Never crashes the daemon over an observability DB.

### 4.5 Replay safety (full replay)

Full replay re-fires `focus`, `type`, `press` — these will hit whatever window is foreground at replay time, which is almost certainly **not** the window the original run targeted. Mitigations:

- Endpoint requires header `X-Replay-Confirm: yes` (not enabled by default).
- CLI requires `--yes` flag.
- UI hides button behind a "danger" toggle.
- Replay creates a new run with `attrs.replay_of=<original_run_id>` so the trace is distinguishable.

LLM-replay (re-route only, no tool fires) is safe and unfettered.

### 4.6 Graph runtime instrumentation

`GraphRuntime.run` currently has no hook seam. Add `tracer` as a constructor dep (or import singleton). Wrap the per-node dispatch (`graph_runtime.py:90-221`, the loop body) in `with tracer.span("node", node_id=node.id, ref=node.ref, kwargs=node.kwargs) as s:` and call `s.set_output(port_values)` after dispatch. Existing error capture into `failed_step_index` / `error_msg` continues unchanged — the span carries the same info redundantly for the trace UI.

### 4.7 Existing `outputs/last_plan.json`

Kept as-is. Useful as a JSON-compatible fallback when SQLite is unhappy. New trace data is additive.

## 5. Configuration

New `[observability]` section in `config.toml`:

```toml
[observability]
enabled = true
keep_runs = 1000
db_path = "outputs/runs.db"        # relative to project root
queue_max = 4096                   # writer queue depth
slow_run_ms = 2000                 # log WARNING if a run exceeds this
```

`enabled = false` short-circuits the tracer to a no-op singleton — useful for benchmarks. Default on.

## 6. Phasing

1. **Phase 1 — Tracer + store.** `observability/` module, schema, span context manager, writer thread, instrumentation in daemon + transcriber + LLM router + dispatcher + graph runtime. Daemon stdout one-liners. Unit tests for tracer + store. **Gate:** trigger 5 utterances, inspect `runs.db` directly, verify span tree shape and error capture.
2. **Phase 2 — REST + CLI.** `/api/runs/*` router, `vc debug` and `vc tail` subcommands. **Gate:** all subcommands round-trip; `vc tail` shows live tree; `vc debug last --json` parses cleanly.
3. **Phase 3 — UI.** `/page/runs` + Builder overlay (static + live mode). **Gate:** human validates: open Builder, fire utterance via hotkey, watch live highlight; click historical run, see overlay paint; replay-LLM diff renders correctly.
4. **Phase 4 — Replay (full).** `replay.py` full-replay path, danger gating, replay marker in trace. **Gate:** controlled replay of a known-safe utterance; verify new run links back via `attrs.replay_of`.

Each phase ends in automated test suite green + human validation gate per ADR 0009.

## 7. Open questions

None blocking. Future considerations (out of scope here):

- Eval suite consuming this layer — fixture transcripts + plan-equivalence scoring.
- Export to OpenTelemetry / Jaeger if multi-process tracing ever matters.
- Span sampling if volume ever grows beyond comfort.
