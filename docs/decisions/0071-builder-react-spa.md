# ADR 0071 — Builder React SPA + n8n-Style Runs Panel

**Date:** 2026-04-28
**Status:** Accepted
**Closes:** Issue #93 (Builder runs panel renders raw JSON blob)

---

## Context

Two problems were collapsed into one design decision:

1. **Issue #93** — `page_builder.html` swapped `/api/runs` JSON directly into the DOM via `hx-swap="innerHTML"`. Users saw a raw JSON blob with no filter, no detail expansion, no live tail, and no link to the failing node on canvas.

2. **HTMX ceiling** — Drawflow is already 100% client-side JS; `builder.js` kept growing. The n8n-style runs panel demanded real client state (live SSE merge + client-side filter + drawer expansion + node↔row two-way highlight). Layering more `hx-trigger="sse:..."` and OOB swaps on top of Drawflow's imperative API would produce an unmaintainable tangle.

---

## Decision

Replace the entire `/page/builder` page with a **React 18.3 + Vite 5 + TypeScript SPA** (`web/builder-ui/`), built to `src/voice_commander/web/static/builder/`. Replace Drawflow with **React Flow** (OSS, free tier). Ship the **n8n-style runs panel** as a feature of the new SPA.

Add a **4-bucket runtime error taxonomy** (`program` / `wiring` / `llm` / `infra`) with a schema migration from v1 → v2 adding `error_category` columns to `runs` and `spans` tables. Surface the category in the runs panel, span tree, canvas overlay, and the copy-as-prompt export endpoint.

Big-bang cutover in a single PR: no `/v2` route, no parallel old-builder fallback.

---

## Stack

| Concern | Pick |
|---|---|
| Framework | React 18.3+ |
| Build | Vite 5+ |
| Language | TypeScript 5.x strict (`strict`, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`) |
| Styling | Tailwind CSS 3.x + shadcn/ui |
| Canvas | React Flow OSS (latest stable) |
| State | Zustand (graphStore, runsStore, uiStore) |
| Data fetching | Plain `fetch` in `api/*.ts` — no TanStack Query |
| Package manager | pnpm |

---

## Consequences

**Positive:**
- Runs panel now shows structured rows, status icons, error categories, and a span-tree drawer.
- Live tail via SSE `run.appended` — no polling.
- Copy-as-prompt produces a deterministic markdown payload for external agents.
- Canvas behaviour (drag-from-palette, wire, save) is preserved with React Flow.
- Error taxonomy gives users actionable fix guidance (fix graph vs fix tool vs fix LM Studio).
- CI enforces typecheck + lint + unit tests + build on every push.

**Negative / trade-offs:**
- Big-bang cutover carries regression risk mitigated by the manual validation checklist (§12.4 of the spec).
- SPA adds a build step (`pnpm install && pnpm build`) before the builder page works. Fresh clones show a friendly stub HTML instead of a 404.
- `/page/runs` standalone page stays jinja+htmx — intentionally excluded from this ADR's scope.

---

## Out of scope (deferred)

- Replay/scrub UI
- Breakpoints/step-debugger
- Pinned data on nodes
- React Flow Pro features
- `/page/runs` migration to React
- TanStack Query
- Client-side routing

---

## References

- Issue #93
- ADR 0067 — `llm_visible` flag (toolbar toggle)
- ADR 0069 — Builder visual taxonomy (CSS vars carried over to `globals.css`)
- ADR 0070 — Observability span tree (schema v1 → v2 extends this)
- Spec: `docs/superpowers/specs/2026-04-28-builder-react-spa-runs-panel-design.md`
