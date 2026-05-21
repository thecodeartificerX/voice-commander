# ADR 0022: Use HTMX + Jinja server-rendered templates instead of a JS SPA framework

**Status:** Accepted
**Date:** 2026-04-20

## Context

The web UI requires the following interactive surfaces:

- A card grid of all registered tools with their current enabled/disabled state.
- Inline phrase editors per tool (add, remove, reorder phrases).
- Toggle buttons for enabling/disabling individual tools.
- A live status indicator for the daemon (recording, idle, last utterance).

Two broad approaches exist: a client-side single-page application (SPA) that calls a JSON API, or server-rendered HTML with lightweight JS for interactivity.

## Decision

Use HTMX for HTML-over-the-wire interactivity, Jinja2 templates rendered by FastAPI, and vendored Tailwind CSS (CDN build, no build step).

- FastAPI serves Jinja2-rendered HTML at the page routes (`GET /`, `GET /tools/{name}`).
- Partial updates (toggle, phrase edit, reload) return HTML fragments. HTMX attributes (`hx-post`, `hx-target`, `hx-swap`) wire the fragments into the page without writing JavaScript.
- Tailwind CDN script is vendored into `src/voice_commander/web/static/` at pinned version — no npm, no bundler, no `node_modules`.
- HTMX is similarly vendored into `static/`.
- Total JS written by the project: ~0 lines. Total build steps: 0.

The six routes covered:

| Route | Method | Returns |
|---|---|---|
| `/` | GET | Full page — tool card grid |
| `/tools/{name}` | GET | Full page — tool detail |
| `/tools/{name}/toggle` | POST | HTML fragment — updated toggle button |
| `/tools/{name}/phrases` | PUT | HTML fragment — updated phrase list |
| `/tools/{name}/phrases/{phrase}` | DELETE | HTML fragment — updated phrase list |
| `/status` | GET | HTML fragment — daemon status bar |

## Consequences

### Positive
- Zero JavaScript build step. `uv sync` is the only setup command; the web UI is ready.
- Server code is ~200 LOC of straightforward FastAPI route handlers + Jinja2 templates.
- HTML-over-the-wire means the server is always the source of truth — no client-side state to synchronise.
- HTMX's declarative attribute model is readable by anyone familiar with HTML; no framework-specific idioms to learn.
- Vendored static assets mean the UI works fully offline (no CDN dependency at runtime).

### Negative
- Rich interactions (drag-to-reorder phrase list) are more awkward with HTMX than with a SPA. If drag-reorder is required, a small vanilla JS helper will be needed.
- Server round-trips for every interaction add ~1–5 ms latency on localhost. Imperceptible in practice.
- Jinja2 templates are less composable than React/Vue components for very large UIs. Acceptable at the current scale (< 10 templates).

### Neutral
- Vanilla JS + `fetch` was considered as a middle path. Rejected in favour of HTMX because HTMX eliminates the boilerplate of writing `fetch`, parsing JSON, and manually updating the DOM for every interaction — the code delta is measurable.

## Alternatives considered

### React or Vue SPA
A React or Vue frontend calling a JSON API at `/api/`. Rejected: requires a Node.js build step, a bundler (Vite/webpack), a separate dev server, and produces a larger artifact. The UI surface does not justify this complexity. The JSON API would also need to be designed and versioned separately.

### Vanilla JS + fetch
Write `fetch` calls in `<script>` blocks, update the DOM manually. Viable but more boilerplate than HTMX for the same outcome. HTMX replaces perhaps 300 lines of fetch + DOM-update JS with HTML attributes. Rejected in favour of HTMX.

## References
- ADR 0020: FastAPI embedded server
- HTMX docs: https://htmx.org/docs/
- Jinja2 docs: https://jinja.palletsprojects.com/
- Tailwind CSS CDN: https://tailwindcss.com/docs/installation/play-cdn
