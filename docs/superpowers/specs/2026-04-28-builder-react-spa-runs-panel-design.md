# Builder React SPA + n8n-Style Runs Panel — Design Spec

**Date:** 2026-04-28
**Status:** Drafted, awaiting user review
**Closes:** #93 (Builder runs panel renders raw JSON blob)
**Scope:** Replace the entire `/page/builder` UI with a React + Vite + TypeScript SPA. Replace Drawflow with React Flow. Ship the n8n-style runs panel (rows, side drawer, filters, SSE live tail, copy-as-prompt) as a feature inside the new SPA. Add a 4-bucket runtime error taxonomy and surface it in the UI. Big-bang cutover in a single PR; no `/v2` route, no parallel old-builder fallback.

**Out of scope:** Replay/scrub UI, breakpoints/step-debugger, pinned data on nodes, React Flow Pro features, migrating `/page/runs` standalone or any other HTMX page to React, changing daemon/graph runtime semantics, changing the canonical graph JSON file format on disk.

---

## 1. Problem

Two problems collapsed into one design:

1. **Issue #93 — runs panel unreadable.** `web/templates/page_builder.html:38` swaps `/api/runs` JSON straight into the DOM via `hx-swap="innerHTML"`. The Builder UI shows a raw JSON blob in its right rail. Users cannot tell which runs failed, which graph ran, what error fired, or which node blew up. There is no filter, no detail expansion, no live tail, no link back to the failing node on canvas.
2. **HTMX has hit its ceiling for the Builder.** Drawflow is already 100% client-side JS; `builder.js` keeps growing; the n8n-style runs panel demands real client state (live SSE merge + client-side filter + drawer expansion + node↔row two-way highlight). Layering more `hx-trigger="sse:..."` and OOB swaps on top of Drawflow's imperative API will produce a tangle. The roadmap (replay scrubber, breakpoints, kwargs inspector, future visual debugger) is squarely in SPA territory.

The decision: do the rewrite now, ship the runs panel as one of its features.

## 2. Non-goals

- **No staged migration / no `/page/builder/v2`.** Big bang cutover replaces the route in the same PR.
- **No project-wide React adoption.** Settings, prompt inspector, `/page/runs` standalone, and every other current HTMX page stay HTMX. Only `/page/builder` becomes an SPA.
- **No graph JSON format change.** Canonical command/workflow JSON files under `commands/` keep their current schema. The new SPA reads and writes the same shape; React Flow's in-memory model is mapped to/from canonical JSON at the serialization boundary.
- **No new graph features.** Branch, foreach, llm_visible, perception primitives — all unchanged.
- **No new daemon behaviour.** Backend changes are limited to the spans schema (one new column), one classifier helper, one new SSE event, and one new export endpoint.
- **No React Flow Pro.** Free OSS only for v1. Pro can land later if the panel demands it.
- **No replay UI.** `/api/runs/{id}/replay-llm` and `/replay-full` exist (per ADR 0070) but no React surface in this spec.
- **No `/page/runs` migration.** It stays jinja+htmx and shares zero code with the SPA.

## 3. Architecture

### 3.1 Component map

```
┌──────────── browser ─────────────────────────────────────────────┐
│                                                                  │
│  /page/builder  →  GET → FastAPI returns web/static/builder/     │
│                                       index.html                 │
│                                                                  │
│  React + Vite SPA (web/builder-ui/ → built to web/static/builder)│
│  ├─ App shell (toolbar + canvas + side rails)                    │
│  ├─ Canvas (React Flow) ← graphSerialize ↔ canonical JSON        │
│  ├─ Palette (drag source for tools/commands/workflows)           │
│  ├─ PropertiesPane (right pane on node select)                   │
│  ├─ RunsPanel (~280px right rail, n8n-style log)                 │
│  ├─ RunDrawer (~480px slide-out, span tree + error context)      │
│  └─ Stores (zustand): graph, runs, ui                            │
│                                                                  │
│  Data sources:                                                   │
│    REST  /api/graphs, /api/commands, /api/workflows,             │
│          /api/tools, /api/runs[*], /api/prompt                   │
│    SSE   /events  ─ subscribes to: run.appended (new),           │
│                     trace.run_started, trace.span_ended          │
└──────────────────────────────────────────────────────────────────┘
                              ▲
                              │ unchanged below
┌──────────── FastAPI (daemon process) ────────────────────────────┐
│  Routers (existing):                                             │
│    web/app.py             page routes, static mount              │
│    web/admin.py / api.py  /api/* endpoints                       │
│    observability/api.py   /api/runs/*                            │
│  New:                                                            │
│    GET /api/runs/{id}/export.md     copy-as-prompt payload       │
│    GET /api/runs?category=…         filter by error_category     │
│    SSE event "run.appended"         emitted on run completion    │
│    Spans schema_version 1→2 + error_category column              │
│    GraphRuntime / Dispatcher / LLMRouter classify on raise       │
└──────────────────────────────────────────────────────────────────┘
```

### 3.2 Data flow — happy path (graph executes, runs panel updates)

1. Daemon executes utterance → tracer writes Run + Spans to `outputs/runs.db` → emits `trace.run_completed` and `run.appended` to EventBus.
2. EventBus → existing `/events` SSE channel → browser `EventSource`.
3. SPA `runsStore` listens for `run.appended`; payload is the full run JSON (matches `/api/runs?limit=1` shape). Store prepends to `runs[]`, applies current filter predicate, triggers re-render.
4. New row appears at top of `RunsPanel`. If row matches `runsStore.selectedRunId`, drawer auto-refreshes. If row is in `RunOverlay.activeRunId`, canvas node statuses update.

### 3.3 Data flow — drawer open and copy-as-prompt

1. User clicks a row → `runsStore.setSelectedRunId(id)` + `uiStore.setDrawerOpen(true)`.
2. `RunDrawer` mounts; on mount fetches `/api/runs/{id}` (run + spans) via `api/runs.ts`. Result cached in `runsStore.detailById[id]`.
3. `SpanTree` renders nested span list. Each span row shows status badge, type, name, duration, error category chip if present, expandable `attrs`/`output`/`traceback` blocks.
4. User clicks "Copy as prompt" in drawer header. `markdownExport.ts` reads cached run+spans from store, builds a deterministic markdown block (see §6.4), writes to clipboard via `navigator.clipboard.writeText`. Toast confirms.
5. Optional: same payload also exposed as `GET /api/runs/{id}/export.md` for CLI/agent consumption.

### 3.4 Data flow — node↔row highlight (existing semantics preserved)

1. User clicks row → `RunOverlay` reads spans, computes per-node status map (`{ node_id → "ok"|"error"|"skipped" }`), applies CSS classes / data-attributes to React Flow node wrappers.
2. User clicks a node on canvas while drawer is open → drawer scrolls/expands the matching span (by `attrs.node_id`).

### 3.5 Threading and lifecycle

- One `EventSource('/events')` per loaded SPA instance. Reconnect with exponential backoff on close (`api/sse.ts`). No polling — SSE is sole source of new-run truth. Initial list comes from `GET /api/runs?limit=50` on app mount.
- React Flow document is held entirely in `graphStore` (zustand). Save = serialize via `graphSerialize.ts` → `PUT /api/graphs/{name}`. Save is debounced (500ms) once dirty flag flips and "auto-save" toggle is on; otherwise explicit `Cmd+S` / Save button.
- Drawer detail fetch is on-demand; runs list payload is intentionally lean (no spans) to keep SSE chunks small. Spans only loaded when a row is selected.

## 4. Stack picks (locked)

| Concern | Pick | Notes |
|---|---|---|
| Framework | **React 18.3+** | Hooks, concurrent features, Suspense for data |
| Build | **Vite 5+** | Fast HMR, ESM-native, minimal config |
| Language | **TypeScript 5.x, strict** | `strict: true`, `noUncheckedIndexedAccess: true`, `exactOptionalPropertyTypes: true` |
| Styling | **Tailwind CSS 3.x + shadcn/ui** | Copy-paste Radix primitives, full theme control |
| Canvas | **React Flow (free OSS, latest stable)** | Custom node + edge components per category |
| State | **Zustand** | Single store per concern (graph, runs, ui). No Redux, no Context Provider tree. |
| Data fetching | **Plain `fetch` wrapped in `api/*.ts`**, results pushed into Zustand stores. No TanStack Query (per user decision). | Async actions in stores handle loading/error state. |
| Routing | **None** — single page, no client routes | Drawer/panel state lives in `uiStore` |
| Forms | **react-hook-form + zod** | Properties pane node-config forms |
| Icons | **lucide-react** | Matches shadcn defaults |
| Toasts | **sonner** (shadcn-recommended) | Save success, copy success, errors |
| Testing — unit | **Vitest + @testing-library/react + jsdom** | |
| Testing — e2e | **Playwright** | Reuses existing test infra (issue #80) |
| Lint/format | **ESLint (typescript-eslint, react-hooks, import) + Prettier** | Pre-commit via existing hooks |
| Package manager | **pnpm** | Lockfile committed |

## 5. Repo layout

### 5.1 New tree

```
web/builder-ui/
├── package.json
├── pnpm-lock.yaml
├── tsconfig.json
├── tsconfig.node.json
├── vite.config.ts
├── tailwind.config.ts
├── postcss.config.js
├── .eslintrc.cjs
├── .prettierrc
├── .gitignore                    # ignores dist, node_modules
├── index.html                    # Vite entry; references /static/builder/assets/* in prod
├── playwright.config.ts
├── vitest.config.ts
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── api/
│   │   ├── client.ts             # fetch wrapper, base URL handling
│   │   ├── graphs.ts
│   │   ├── runs.ts
│   │   ├── tools.ts
│   │   ├── prompt.ts
│   │   └── sse.ts
│   ├── store/
│   │   ├── graphStore.ts
│   │   ├── runsStore.ts
│   │   └── uiStore.ts
│   ├── canvas/
│   │   ├── Canvas.tsx            # React Flow root
│   │   ├── nodes/
│   │   │   ├── ToolNode.tsx          # generic tool/command/workflow node
│   │   │   ├── BranchNode.tsx        # control.branch
│   │   │   ├── ForeachNode.tsx       # control.foreach
│   │   │   ├── PerceptionNode.tsx    # ocr / clipboard / window-title / cursor-pos
│   │   │   └── index.ts              # nodeTypes map for React Flow
│   │   ├── edges/
│   │   │   ├── ControlEdge.tsx       # ok / error / true / false
│   │   │   ├── DataEdge.tsx          # value wires between ports
│   │   │   └── index.ts              # edgeTypes map
│   │   └── overlays/
│   │       └── RunOverlay.tsx        # paints span status onto nodes
│   ├── palette/
│   │   ├── Palette.tsx
│   │   └── PaletteItem.tsx
│   ├── properties/
│   │   ├── PropertiesPane.tsx
│   │   ├── KwargsForm.tsx        # zod-validated dynamic form
│   │   └── PortEditor.tsx        # add/remove output ports for branch nodes
│   ├── runs/
│   │   ├── RunsPanel.tsx
│   │   ├── RunsFilterBar.tsx
│   │   ├── RunRow.tsx
│   │   ├── RunDrawer.tsx
│   │   ├── SpanTree.tsx
│   │   ├── SpanRow.tsx
│   │   └── CopyAsPromptButton.tsx
│   ├── toolbar/
│   │   ├── Toolbar.tsx           # save, validate, llm-visible toggle, prompt inspector trigger
│   │   └── PromptInspectorDialog.tsx
│   ├── components/ui/            # shadcn-generated: button, dialog, input, select, tabs, tooltip, scroll-area, separator, badge, sheet, switch, toast
│   ├── lib/
│   │   ├── graphSerialize.ts     # React Flow ↔ canonical JSON
│   │   ├── errorCategory.ts      # category → label, color, icon, description
│   │   ├── timeFormat.ts         # "2m ago" relative
│   │   ├── markdownExport.ts     # build copy-as-prompt payload
│   │   └── cn.ts                 # tailwind class merge helper (shadcn standard)
│   ├── styles/
│   │   └── globals.css           # tailwind directives + CSS vars matching project dark theme
│   └── types/
│       ├── graph.ts              # canonical graph JSON types (mirror Python pydantic)
│       └── run.ts                # RunRecord, SpanRecord, ErrorCategory types
└── tests/
    ├── unit/
    │   ├── graphSerialize.test.ts
    │   ├── errorCategory.test.ts
    │   ├── markdownExport.test.ts
    │   ├── runsStore.test.ts
    │   └── components/
    │       ├── RunRow.test.tsx
    │       ├── RunsFilterBar.test.tsx
    │       └── SpanTree.test.tsx
    └── e2e/
        ├── builder-load.spec.ts
        ├── runs-panel-live.spec.ts
        ├── runs-panel-filter.spec.ts
        ├── runs-drawer.spec.ts
        ├── copy-as-prompt.spec.ts
        └── canvas-edit-save.spec.ts
```

### 5.2 Build artifact

- `pnpm build` in `web/builder-ui/` → outputs to `../static/builder/{index.html, assets/[name]-[hash].js, assets/[name]-[hash].css}`.
- `vite.config.ts` sets `base: "/static/builder/"`, `build.outDir: "../static/builder"`, `build.emptyOutDir: true`.
- `web/static/builder/` is **gitignored**. Never commit build output. CI builds it; local dev gets it via `pnpm install && pnpm build` or runs `pnpm dev` (HMR).

### 5.3 FastAPI integration

- Existing `web/app.py` route `GET /page/builder`: replace the jinja `TemplateResponse("page_builder.html")` call with `FileResponse("web/static/builder/index.html")`. One-line change.
- `web/static/` is already mounted via `StaticFiles`; new `builder/` subdirectory is served automatically.
- If `web/static/builder/index.html` is missing on startup (fresh clone, no build run yet), `GET /page/builder` returns a friendly HTML stub: "Builder UI not built yet. Run `pnpm install && pnpm build` in `web/builder-ui/`." This avoids a confusing 404.

### 5.4 Dev workflow

- **Backend:** `python -m voice_commander.daemon` runs on `:8765` (existing).
- **Frontend dev:** `cd web/builder-ui && pnpm dev` runs Vite on `:5173`. `vite.config.ts` configures `server.proxy`:
  ```ts
  proxy: {
    '/api':    { target: 'http://localhost:8765', changeOrigin: true },
    '/events': { target: 'http://localhost:8765', changeOrigin: true, ws: false },
    '/page':   { target: 'http://localhost:8765', changeOrigin: true },
  }
  ```
- Open `http://localhost:5173/` — Vite serves `index.html`, mounts the React app, and proxies all API/SSE calls to the daemon. HMR works.
- **Production:** `pnpm build` once after install; FastAPI serves the bundle from `/static/builder/` and the daemon on the same port. No Node process at runtime.

### 5.5 Task runner shortcuts

The repo currently has no `Makefile` or `justfile`. Create a top-level `Makefile` in this PR with these targets (Python tasks already use `python -m …`; this Makefile becomes the canonical entry point for both Python and Node tasks going forward):

| Command | Action |
|---|---|
| `make builder-install` | `cd web/builder-ui && pnpm install --frozen-lockfile` |
| `make builder-dev` | `cd web/builder-ui && pnpm dev` |
| `make builder-build` | `cd web/builder-ui && pnpm build` |
| `make builder-test` | `cd web/builder-ui && pnpm test --run` |
| `make builder-e2e` | `cd web/builder-ui && pnpm test:e2e` |
| `make builder-lint` | `cd web/builder-ui && pnpm lint && pnpm typecheck` |

### 5.6 CI

New job `builder-ui` in `.github/workflows/ci.yml`:

```yaml
builder-ui:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: pnpm/action-setup@v3
      with: { version: 9 }
    - uses: actions/setup-node@v4
      with: { node-version: 22, cache: 'pnpm', cache-dependency-path: web/builder-ui/pnpm-lock.yaml }
    - run: pnpm install --frozen-lockfile
      working-directory: web/builder-ui
    - run: pnpm typecheck
      working-directory: web/builder-ui
    - run: pnpm lint
      working-directory: web/builder-ui
    - run: pnpm test --run
      working-directory: web/builder-ui
    - run: pnpm build
      working-directory: web/builder-ui
    - uses: actions/upload-artifact@v4
      with:
        name: builder-ui-dist
        path: web/static/builder/
```

E2E (Playwright) job runs separately on a matrix that boots the daemon and points Playwright at the built SPA — extends the existing Playwright infrastructure landing in issue #80.

## 6. Runs panel — detailed UI spec

### 6.1 Panel chrome (always visible right rail, ~280px)

Vertical stack:

```
┌─────────────────────────────────┐
│ Runs                       ⟳ ✕ │ ← header: title, refresh, collapse
├─────────────────────────────────┤
│ [●all] [✓ok] [⊘miss] [⚠wir]    │ ← status pills (multi-select)
│ [⚙prog] [🤖llm] [📡infra]       │
│ ┌──────────────────────────┐    │ ← search input (debounced 300ms)
│ │ 🔎 search transcript...  │    │
│ └──────────────────────────┘    │
├─────────────────────────────────┤
│ ● 2m ago · 1.2s · 4 steps   a3f│ ← row 1
│ "open spotify and play..."      │
├─────────────────────────────────┤
│ ✗ 3m ago · 0.8s · 2 steps   b7c│ ← row 2 (failed)
│ "play that song from yesterday" │
│ ✗ wiring: branch.compare        │ ← error-aware third line
│   missing 'right' kwarg         │
├─────────────────────────────────┤
│ ✓ 5m ago · 0.4s · 1 step    c1f│
│ "copy"                          │
├─────────────────────────────────┤
│ ...                             │
└─────────────────────────────────┘
```

- Filter pills: `all` (default on), `ok`, `miss`, plus the four error categories (`wiring`, `program`, `llm`, `infra`). Multi-select; `all` toggles others off.
- Search: substring match against `run.transcript`; client-side over loaded list. No server round-trip.
- Initial fetch: `GET /api/runs?limit=50`. Older runs lazy-loaded on scroll-bottom via `GET /api/runs?limit=50&before=<oldest_started_at>` (new `before` query param — see §10).
- Live tail: SSE `run.appended` prepends. Cap in-memory list at 200; when length exceeds 200, trim the oldest entries off the end of `runs[]`. Trimming is invisible to the user since trimmed rows fall below the visible scroll viewport (older entries reload on scroll-bottom).

### 6.2 Row layout (dense two-line, error-aware)

```tsx
<RunRow>
  <Line1>
    <StatusIcon status={run.status} category={run.error_category} />
    <Relative ts={run.started_at} />     { /* "2m ago" */ }
    <Sep />
    <Duration ms={run.duration_ms} />    { /* "1.2s" */ }
    <Sep />
    <StepCount n={run.step_count} />     { /* "4 steps" */ }
    <RunIdShort id={run.run_id} />       { /* right-aligned, monospace, 3 chars */ }
  </Line1>
  <Line2 truncate>
    "{run.transcript}"
  </Line2>
  {run.status === 'error' && (
    <Line3>
      <CategoryChip category={run.error_category} />
      <ErrorSummary>{run.error_summary}</ErrorSummary>
    </Line3>
  )}
</RunRow>
```

- Rows: `~64px` tall ok/miss, `~88px` tall when error line present.
- Hover: subtle background highlight, copy-id button appears top-right.
- Click: opens drawer (§6.3), highlights row, paints `RunOverlay` on canvas.
- Keyboard: ↑/↓ moves selection, Enter opens drawer, Esc closes drawer + clears selection. Selection state in `runsStore`.
- Status icon mapping:
  - `ok` → green ● (lucide `Circle` filled)
  - `miss` → yellow ⊘ (lucide `Ban`)
  - `error+wiring` → orange ⚠ (lucide `Zap`)
  - `error+program` → red ✗ (lucide `XCircle`)
  - `error+llm` → purple 🤖 (lucide `Brain`)
  - `error+infra` → gray 📡 (lucide `WifiOff`)
- Color tokens are CSS vars in `globals.css` so dark/light themes can re-skin them in one place.

### 6.3 Side drawer (~480px slide-out from right)

Built on shadcn `<Sheet>` (Radix dialog with side variant).

```
┌──────────────────────────────────────────┐
│ Run a3f9bc · ✓ ok                  ✕     │ ← header
│ "open spotify and play discover weekly"  │
│ 2m ago · 1.2s · 4 steps · graph: …       │
│ [Copy as prompt]  [Open in /page/runs ↗] │
├──────────────────────────────────────────┤
│ Span tree                                │
│ ▾ run                          1.2s ✓    │
│   ▾ transcribe                   84ms ✓  │
│   ▾ llm_call                    520ms ✓  │
│     plan: open(spotify), play(discover…) │
│   ▾ plan                        612ms ✓  │
│     ▾ tool_call: open            96ms ✓  │
│       attrs: { target: "spotify" }       │
│       return: { window: 0x0042 }         │
│     ▾ tool_call: play           512ms ✗  │
│       attrs: { query: "discover…" }      │
│       error: program · TimeoutException  │
│       message: "Spotify API timeout…"    │
│       ▸ traceback (click to expand)      │
└──────────────────────────────────────────┘
```

- Span tree uses indentation (16px per depth) and chevron toggles. Default: failed/error spans expanded, ok spans collapsed.
- Each span row: chevron, type-icon, name, duration right-aligned, status badge.
- Expanded span body: `attrs` pretty JSON, `output` pretty JSON, `error_type` + `error_msg` + collapsible `traceback`, link "Highlight node on canvas" if `attrs.node_id` is present.
- Drawer width: 480px on ≥1280px viewports, full overlay (max 95vw) below that.
- Header buttons:
  - **Copy as prompt** — see §6.4
  - **Open in /page/runs** — external link to standalone page (full table view, replay buttons)
- ESC closes; clicking outside closes; drawer state persists in `uiStore` so a refresh restores last-open run.

### 6.4 Copy-as-prompt format

Exposed two ways:
- **Client-side button** in drawer header: builds the markdown locally from cached run+spans, copies to clipboard.
- **Server endpoint** `GET /api/runs/{id}/export.md`: returns the same payload as `text/markdown`. Lets `vc debug` and external agents pull it without opening the UI.

Format (deterministic; both implementations produce byte-identical output):

```markdown
# Voice Commander run a3f9bc — error

**Transcript:** "play that song from yesterday"
**Started:** 2026-04-28T14:22:31.084Z (2 minutes ago)
**Duration:** 812ms
**Status:** error
**Error category:** wiring
**Graph:** workflow.play_recent
**Daemon PID:** 18244
**Schema:** runs.db v2

## Plan returned by LLM

(Read from the `llm_call` span's `output` field — the parsed plan the router emitted.)

```json
{ "tool": "workflow.play_recent", "args": { "query": "yesterday" } }
```

## Span tree

- ▾ run · 812ms · error
  - ✓ transcribe · 78ms · ok
  - ✓ llm_call · 410ms · ok
    - model: gemma-3-27b-it
    - tokens: 142 in / 38 out
  - ✗ plan · 318ms · error
    - ✗ graph: workflow.play_recent · 312ms · error
      - ✓ node: get_clipboard (clipboard.read) · 4ms · ok
        - output: "yesterday's playlist"
      - ✗ node: route (control.branch) · 2ms · error
        - error_type: WiringError
        - error_msg: branch node 'route' missing required kwarg 'right'
        - error_category: wiring
        - kwargs: { left: "yesterday's playlist", op: "contains" }
        - canvas node id: bf12a7

## Failure summary

The graph `workflow.play_recent` failed at node `route` (control.branch) because the `right` kwarg was not wired. This is a **wiring error** — fix the graph, not a tool.

## Repro

1. Open http://localhost:8765/page/builder
2. Load workflow `play_recent`
3. Locate node `route` (id `bf12a7`)
4. Connect a value into the `right` input port

---

_Generated 2026-04-28T14:24:50.211Z by Voice Commander Builder UI_
```

The "Failure summary" section is generated client-side from the error_category and span path; no LLM is called. Per user direction: this payload is meant to be pasted into a smart external agent (Claude/Copilot in their chat), not fed to the local LM Studio model.

### 6.5 Empty / loading / error states

- Empty (no runs ever): "No runs yet. Press Scroll Lock and speak a command."
- Empty after filter: "No runs match these filters. [Clear filters]"
- Loading initial: skeleton rows (3) with shimmer.
- SSE disconnected: red dot in header tooltip "Live updates disconnected — reconnecting in Ns".
- API fetch failure: inline error banner with retry button.

### 6.6 Accessibility

- All interactive elements keyboard-reachable.
- Focus ring visible (Tailwind `ring-2 ring-offset-2`).
- ARIA roles: filter pills `role="checkbox"`; row container `role="listbox"`, rows `role="option"`; drawer `role="dialog"`.
- Color choices verified against WCAG AA contrast on the dark theme.

## 7. Error categorization (4 buckets)

### 7.1 Categories

| Category | Meaning | Example raisers | Fix path |
|---|---|---|---|
| `program` | A tool function raised an unhandled exception during execution | `TimeoutException` from a Spotify API call inside `tools/spotify.py`; `FileNotFoundError` in `tools/files.py` | Fix the tool implementation or its dependencies |
| `wiring` | Graph structure/runtime error: missing required kwarg, dangling input port, branch produced no path, foreach over non-iterable, unknown node ref. Type-mismatch detection is **not** in scope for v1 (Python is duck-typed and there's no runtime type schema on wires today; revisit if false-`program` reports become common). | `GraphRuntime` raises `WiringError` (new) when a node's required input has no incoming wire and no literal value | Fix the graph in the Builder |
| `llm` | Router returned a malformed plan, called an unknown tool, exceeded retry budget, or LM Studio responded with non-JSON / refusal | `LLMRouter` raises `LLMPlanError` (new) | Tweak prompt template, swap model, adjust temperature |
| `infra` | LM Studio unreachable, audio device gone, GPU OOM, SQLite locked, EventBus crash, anything outside the program/graph/LLM domain | Network errors, `ConnectionRefusedError` to LM Studio, `sounddevice.PortAudioError` | Check service health, restart daemon, replug device |

`miss` is **not** an error category — it remains a separate `run.status` value (the LLM correctly chose to do nothing, or VAD triggered on noise). Misses are still surfaced in the runs panel with the yellow ⊘ icon but have no `error_category`.

### 7.2 Schema migration

- `outputs/runs.db` `spans` table: add column `error_category TEXT NULL`. Allowed values: `program`, `wiring`, `llm`, `infra`, or NULL when status≠error.
- `runs` table: add column `error_category TEXT NULL` denormalised from the deepest error span (for fast filter queries on `/api/runs?category=…`).
- Bump `schema_version` 1 → 2.
- `observability/store.py.migrate()` adds an idempotent `ALTER TABLE … ADD COLUMN IF NOT EXISTS` (SQLite emulation: check `pragma_table_info` then conditional `ALTER`). Existing rows: `error_category` stays NULL. UI treats NULL on an error row as `program` for backwards compatibility (with a small "uncategorised" badge).

### 7.3 Classifier — where errors are caught and tagged

| Module | New behaviour |
|---|---|
| `commands/graph_runtime.py` | Wrap each node execution in try/except. On `WiringError` (new exception type, raised by the runtime itself when validating kwargs against the node's schema): set `span.error_category = "wiring"`. Other exceptions inside node execution that bubble from a tool: `program`. |
| `dispatcher.py` | Wrap `run_plan`. Tool exceptions: `program`. Tool-not-found / arg-shape mismatch on the dispatcher level: `wiring`. |
| `llm_router.py` | Wrap `route`. JSON parse failure on LM Studio response, plan mentioning unknown tools, retry budget exceeded: `llm`. `httpx.ConnectError` / `httpx.ReadTimeout` to LM Studio: `infra`. |
| `daemon.py` | Top-level catch-all. Anything not classified above: `infra`. |

Implementation: a small helper `observability/errors.py.classify(exc, context)` returns a category; instrumentation seams call it before writing the span. Keeps category logic in one place; easy to unit-test.

### 7.4 Surfacing in UI

- Filter pills (one per category)
- Row icon + color
- Row error-aware third line (`{category}: {span.name} {short_msg}`)
- Drawer span row badge
- Copy-as-prompt payload prominently states the category and the appropriate fix path
- Canvas: `RunOverlay` adds `data-error-category="wiring"` on failing nodes so CSS can render a category-specific outline (e.g., orange dashed for wiring, red solid for program)

## 8. State management — Zustand stores

### 8.1 `graphStore`

```ts
interface GraphStore {
  graphId: string | null;            // current command/workflow name
  graphKind: 'command' | 'workflow' | null;
  nodes: Node[];                     // React Flow nodes
  edges: Edge[];                     // React Flow edges
  selectedNodeId: string | null;
  dirty: boolean;
  llmVisible: boolean;               // graph llm_visible flag

  load(kind, name): Promise<void>;   // GET /api/{kind}s/{name} → deserialize → set state
  save(): Promise<void>;             // serialize → PUT /api/{kind}s/{name}
  setNodes(updater): void;           // React Flow callback
  setEdges(updater): void;
  selectNode(id | null): void;
  toggleLlmVisible(): void;
}
```

Serialization lives in `lib/graphSerialize.ts`. React Flow's `Node`/`Edge` types map to canonical JSON's `nodes[]` / `edges[]` 1:1; ports/handles map directly. Test: round-trip every existing `commands/*.json` through `parse → serialize` and assert equality.

### 8.2 `runsStore`

```ts
interface RunsStore {
  runs: RunSummary[];                 // list, newest first, capped at 200
  detailById: Record<string, RunDetail>;  // lazy-loaded full run + spans
  selectedRunId: string | null;
  filters: { statuses: Status[]; categories: ErrorCategory[]; query: string };
  sseConnected: boolean;

  fetchInitial(): Promise<void>;      // GET /api/runs?limit=50
  fetchOlder(): Promise<void>;        // GET /api/runs?limit=50&before=<oldest>
  fetchDetail(runId): Promise<void>;  // GET /api/runs/{id}
  selectRun(id | null): void;
  setFilter(partial): void;
  onSseEvent(event): void;            // 'run.appended' prepends; 'trace.span_ended' updates open detail
  visibleRuns(): RunSummary[];        // selector: applies filter predicate
}
```

### 8.3 `uiStore`

```ts
interface UiStore {
  drawerOpen: boolean;
  promptInspectorOpen: boolean;
  paletteCollapsed: boolean;
  propertiesPaneCollapsed: boolean;
  theme: 'dark' | 'light';            // dark default; light future
  toast(message, kind): void;
}
```

### 8.4 SSE wiring

`api/sse.ts` exports a singleton `EventSource` manager:
- Connects on app mount; reconnects with backoff (1s, 2s, 4s, 8s, max 30s) on close.
- Routes events by `event.type` to subscribers registered via `onEvent(type, handler)`.
- `runsStore` registers handlers for `run.appended`, `trace.run_started`, `trace.span_ended`.
- Sets `runsStore.sseConnected` on open/close so UI can show indicator.

## 9. Canvas — React Flow mapping

### 9.1 Node taxonomy

Five node category families (matches existing ADR 0069 visual taxonomy):

| Category | React Flow `type` | Source on disk |
|---|---|---|
| Tool | `tool` | tools registered via `@tool` decorator |
| Command (graph-backed tool) | `command` | `commands/*.json` with `kind: command` |
| Workflow | `workflow` | `commands/*.json` with `kind: workflow` |
| Control flow | `control.branch`, `control.foreach` | built-in primitives |
| Perception | `perception.ocr`, `perception.clipboard`, `perception.window`, `perception.cursor` | built-in primitives |

Each `type` has a custom React component in `canvas/nodes/`. Color and shape come from Tailwind classes that mirror the ADR 0069 CSS custom properties so the visual identity carries over. CSS vars from the old `builder.css` move into `globals.css`.

### 9.2 Edge types

- `control` edge with `data: { kind: 'ok' | 'error' | 'true' | 'false' }`. Custom edge component renders the right color (green/red/blue/gray) and label.
- `data` edge with `data: { sourceField: string, targetField: string }`. Renders neutral color; tooltip shows `source.field → target.field`.

React Flow port handles use `id` matching the kwarg name so connectivity validates trivially. `lib/graphSerialize.ts` reads `edge.sourceHandle` / `edge.targetHandle` directly.

### 9.3 Drag-from-palette

`PaletteItem` exposes `onDragStart` setting `dataTransfer` payload `{ kind, name }`. `Canvas.tsx` `onDrop` reads it, computes drop position via `react-flow` `screenToFlowPosition`, fetches the tool/command spec from `/api/tools/{name}` (or pulls from the cached palette list which already includes the spec), uses its declared `args` schema to seed default kwargs on the new node, then dispatches into `graphStore`.

### 9.4 Run overlay

`RunOverlay` is a hook (`useRunOverlay`) consumed by `Canvas.tsx`. When `runsStore.selectedRunId` is set, it reads the cached `detailById[id]` spans, builds a per-node-id status map, and applies CSS classes via `data-run-status` and `data-error-category` attributes on each `react-flow__node` wrapper. Class names: `run-node-ok`, `run-node-error`, `run-node-skipped`, `run-node-pulse` — same names as today, so CSS is a near-direct port from `builder.css`.

## 10. Backend changes (Python)

Minimal. Listed exhaustively:

1. **`observability/store.py`**
   - Schema migration to v2 adds `error_category` to `runs` and `spans` tables.
   - `insert_span(...)` accepts `error_category` parameter.
   - `list_runs(...)` accepts `category` query filter.
2. **`observability/errors.py`** (new file)
   - `classify(exc: BaseException, *, where: str) -> Category` returns `'program' | 'wiring' | 'llm' | 'infra'`.
3. **`observability/api.py`**
   - `GET /api/runs` accepts new query params: `category` (filter by error_category) and `before` (ISO timestamp; returns runs with `started_at < before` for cursor pagination).
   - `GET /api/runs/{id}/export.md` returns the markdown payload defined in §6.4 with `Content-Type: text/markdown`.
   - **New EventBus event:** `EventBus.publish('run.appended', run_dict)` — fired from the tracer's `run_completed` write path (does not yet exist in code; ADR 0070 plans it but it is unimplemented today). Payload must contain all RunSummary fields the SPA expects: `run_id`, `started_at`, `ended_at`, `duration_ms`, `status`, `error_category`, `error_summary`, `transcript`, `step_count`, `graph`, `daemon_pid`, `schema_version`.
4. **`commands/graph_runtime.py`**
   - Define `WiringError(Exception)`.
   - Validate node kwargs at execution time; raise `WiringError` for missing required kwargs / type mismatches / dangling inputs.
   - Wrap node execution; on exception call `classify(exc, where='graph_runtime')` and pass result to span writer.
5. **`dispatcher.py`** — wrap `run_plan`; classify exceptions.
6. **`llm_router.py`**
   - Define `LLMPlanError(Exception)`.
   - Raise `LLMPlanError` on JSON parse failure, unknown tool reference, retry-budget exhausted.
   - Catch + classify network errors as `infra`.
7. **`daemon.py`** — top-level catch wraps `_process_utterance`; uncategorised goes to `infra`.
8. **`web/app.py`**
   - `GET /page/builder` returns `FileResponse("web/static/builder/index.html")` (with the friendly stub fallback).
   - Delete `page_builder.html` template usage.
9. **Static-mount config** — already correct; no change.
10. **Pruning helper** — when computing `runs.error_summary`, take the deepest error span's `error_msg` truncated to 120 chars. Stored on `runs` row; cheap UI display.

No changes to: hotkey, recorder, transcriber, VAD, sprite, HUD, all tools, Builder canonical JSON file format, EventBus core, sprite SSE consumer.

## 11. Migration of existing graphs

- Canonical JSON files under `commands/` are unchanged; React Flow ↔ canonical JSON happens at the SPA boundary in `lib/graphSerialize.ts`.
- Round-trip test: load every existing `commands/*.json`, deserialize → serialize, assert byte-equality (modulo key order, which is normalised). Add to Vitest suite.
- Old `builder.js` had a `normalizeArgs()` shim (per ADR 0069) for the dict-vs-array kwarg shape difference. The same logic moves into `graphSerialize.ts`. Same fixtures.

## 12. Testing strategy

### 12.1 Unit (Vitest, jsdom)

- `graphSerialize.test.ts` — round-trip every `commands/*.json` fixture
- `errorCategory.test.ts` — category → label/color/icon mapping is exhaustive
- `markdownExport.test.ts` — golden snapshot of payload for fixture run
- `runsStore.test.ts` — filter predicate, SSE prepend, cap-at-200 trim, selection
- Component tests:
  - `RunRow.test.tsx` — renders ok/miss/each-error-category variants; keyboard activation
  - `RunsFilterBar.test.tsx` — pill toggling, search debounce
  - `SpanTree.test.tsx` — collapse/expand, error span auto-expand
  - `Canvas.test.tsx` — palette drop creates node, edge connect validates port

### 12.2 E2E (Playwright)

Boots the daemon (or a fixture-mode FastAPI shim that serves canned `/api/runs` + `/events` responses) and the built SPA.

- `builder-load.spec.ts` — `/page/builder` loads, no console errors, palette + canvas + runs panel render
- `runs-panel-live.spec.ts` — fixture emits `run.appended` SSE; row appears at top within 500ms; row count bumps
- `runs-panel-filter.spec.ts` — toggle `wiring` pill; only wiring-error rows visible; clear filter restores
- `runs-drawer.spec.ts` — click row → drawer opens with span tree → click span → canvas node highlights
- `copy-as-prompt.spec.ts` — click button → clipboard contains expected markdown header
- `canvas-edit-save.spec.ts` — drag tool from palette → wire two nodes → save → fixture asserts PUT body matches expected canonical JSON
- `css-isolation.spec.ts` — open `/page/runs` (HTMX page) after the SPA has loaded once; assert no SPA stylesheet leaks into the document and the existing visual matches a baseline screenshot

### 12.3 Backend tests (existing pytest)

- `test_observability_errors.py` — `classify()` for known exception types
- `test_observability_schema_migration.py` — v1 → v2 migration adds columns, doesn't drop data
- `test_runs_export_md.py` — golden snapshot of `/api/runs/{id}/export.md` matches client-side format byte-for-byte
- `test_graph_runtime_wiring_error.py` — missing kwarg raises `WiringError`, span gets `error_category='wiring'`
- `test_dispatcher_program_error.py` — tool raising → span tagged `program`
- `test_llm_router_llm_error.py` — bad JSON → span tagged `llm`

### 12.4 Manual validation (human gate before PR merge)

1. Press Scroll Lock, speak a successful command — row appears live, drawer shows clean span tree.
2. Speak a known-failing command (e.g., one wired to a tool that raises) — row appears with `program` category, drawer shows traceback, copy-as-prompt produces complete payload.
3. Build a deliberately broken graph in the UI (branch missing right kwarg) — invoke it — row tagged `wiring`, summary points at the right node, clicking it highlights on canvas.
4. Stop LM Studio — speak — row tagged `infra`.
5. Reload page — last selected run still shown in drawer.
6. Filter pills work; search finds substring in transcript.
7. Save a graph; reopen; identical to what was saved.
8. Drag every tool category from palette; each renders with correct color/shape per ADR 0069.

## 13. Acceptance criteria (mirrors issue #93 + scope expansion)

- [ ] `/page/builder` serves the React SPA from `web/static/builder/index.html`.
- [ ] Drawflow library and `web/static/vendor/drawflow*` deleted; `web/static/builder.js` deleted; `web/templates/page_builder.html` deleted; old `builder.css` rules either deleted or migrated to `globals.css`.
- [ ] Builder UI shows a styled list of recent runs (no raw JSON).
- [ ] Clicking a run opens a slide-out drawer with span tree and paints overlay on canvas (existing highlight behaviour preserved).
- [ ] Live mode prepends new runs as they arrive via SSE; no polling.
- [ ] Filter bar (status pills + 4 error category pills + search) works.
- [ ] Failed runs show inline error category + short message in the row.
- [ ] Copy-as-prompt button produces a deterministic markdown payload matching `/api/runs/{id}/export.md`.
- [ ] Spans table has `error_category` column populated by classifier.
- [ ] All listed Vitest unit tests pass.
- [ ] All listed Playwright e2e tests pass.
- [ ] All listed pytest backend tests pass.
- [ ] CI builds the SPA bundle successfully and uploads as artifact.
- [ ] README updated with `pnpm install && pnpm build` step under "Install".
- [ ] ADR filed at next available number under `docs/decisions/` (e.g., `0071-builder-react-spa.md`); summary row added to `docs/agents/technical-decisions.md`.
- [ ] CLAUDE.md "Architecture at a glance" updated to mention the SPA + React Flow.
- [ ] Manual validation checklist (§12.4) signed off by human.

## 14. Risks and mitigations

| Risk | Mitigation |
|---|---|
| React Flow's edge-routing is uglier than Drawflow on dense graphs | Use `react-flow` `smoothstep` edges + `useReactFlow` `fitView`; verify on existing fixture graphs in §12.4 step 8. If unacceptable, evaluate `dagre`-layouter helper. |
| pnpm not installed on contributor machines | README explicitly says `corepack enable && corepack prepare pnpm@9 --activate` (corepack ships with Node 22). |
| Build artefact missing in production | Friendly stub HTML on `GET /page/builder` if `index.html` absent (§5.3). |
| Schema migration corrupts existing `runs.db` | Migration is `ADD COLUMN` only, idempotent; existing rows unaffected; backup `runs.db` before upgrade documented in CHANGELOG. |
| Big-bang cutover ships subtle regression in canvas behaviour | Manual validation §12.4 step 8 covers every node category; e2e `canvas-edit-save.spec.ts` covers wire-and-save round-trip. |
| Canonical JSON round-trip mismatch breaks existing graphs | Unit test loads every `commands/*.json` and asserts equality; CI gate. |
| Tailwind/shadcn CSS bleeds into other (still-htmx) pages | Build CSS is scoped to `/static/builder/`; loaded only by SPA's `index.html`. Other pages don't import it. Verified by `css-isolation.spec.ts` (§12.2) opening `/page/runs` and asserting unchanged baseline. |
| LM Studio refuses to load with new prompt? | Prompt template untouched; LLM path unchanged except for error classification on top. |
| Bundle size > 250KB gz | Acceptable for local-first single-page app. Vite tree-shakes Radix; dynamic-import the React Flow core if needed. |

## 15. Rollout

Single PR. Sequence inside the PR:

1. Add `web/builder-ui/` skeleton (config files + empty `App.tsx`); wire CI; SPA renders empty shell at `/page/builder`. Validate: SPA loads, no canvas yet.
2. Port canvas: React Flow + node/edge types + palette + properties pane; serialize/deserialize round-trip green. Validate: load every fixture graph, save, byte-equal.
3. Add backend changes (schema migration, `errors.classify`, `WiringError`, `LLMPlanError`, classifier wiring, `/api/runs/{id}/export.md`, `run.appended` payload audit).
4. Build runs panel: list, row, filters, drawer, span tree, copy-as-prompt, SSE.
5. Delete old builder assets + jinja template; flip `/page/builder` route.
6. Manual validation; ADR; CLAUDE.md; README.
7. Merge.

Each step inside the PR is a commit. CI must be green at every commit (Vitest, Playwright, pytest, lint, typecheck, build).

## 16. References

- Issue #93 (this issue)
- ADR 0067 — `llm_visible` flag (preserved as toggle in toolbar)
- ADR 0069 — Builder visual taxonomy (CSS vars carried over to `globals.css`)
- ADR 0070 — Observability span tree (consumed unchanged; only `error_category` added)
- `docs/superpowers/specs/2026-04-27-observability-pipeline-design.md` §3.10, §3.11, §UI
- `docs/architecture.md` — Architecture map (will be updated post-merge)
- React Flow docs — https://reactflow.dev
- shadcn/ui docs — https://ui.shadcn.com
- Zustand docs — https://zustand.docs.pmnd.rs
- Tailwind v3 docs — https://tailwindcss.com/docs
- Vite docs — https://vitejs.dev
