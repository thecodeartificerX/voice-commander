# ADR 0062 — Drawflow vendored as the node-graph editor

**Date:** 2026-04-26
**Status:** Accepted

## Context

The node-graph builder feature requires a visual canvas where users can author commands and workflows by connecting node blocks with wires. The editor must run inside the existing web UI — a FastAPI + HTMX application (ADR 0020, ADR 0022). ADR 0022 explicitly forbids SPA toolchains: no React, Vue, Vite, esbuild, or `node_modules` in this repository. Any candidate node-graph library must be usable as a plain `<script src="...">` include with no build step.

The web UI is served by the embedded uvicorn daemon thread. Static files are already served from `web/static/`. A vendored JS file drops into that directory without introducing any new serving infrastructure.

Several JavaScript node-graph libraries were evaluated: React Flow, LiteGraph.js, Rete.js v2 vanilla, pure SVG hand-rolled, and Drawflow.

## Decision

Use **Drawflow** (github.com/jerosoler/Drawflow) as the node-graph editor, vendored as a single `drawflow.min.js` + `drawflow.min.css` pair under `web/static/`. No npm, no build step, no node toolchain. The files are served directly by the existing static-file handler.

The vendoring is a deliberate, recorded bump: when an upstream release is needed, the two files are downloaded and the version is noted in a comment at the top of each file and in the relevant ADR amendment.

Alternatives considered and rejected:

- **React Flow** — requires Vite or esbuild and a `node_modules` tree; directly violates ADR 0022. Rejected.
- **LiteGraph.js** — heavier API surface designed for ComfyUI-style deep-learning graphs; overkill for voice-command authoring. Rejected.
- **Rete.js v2 vanilla** — modular but requires more glue code and has a steeper learning curve with less documentation for vanilla-JS setups. Rejected.
- **Pure SVG hand-rolled** — cheapest dependency cost, but produces the worst UX and would consume significant implementation time. Rejected.

## Decision

Drawflow wins because it has zero runtime dependencies, a minimal imperative API (`addNode`, `addConnection`, `import`, `export`), MIT licence, and an active maintenance history. Its `export()` produces a serialisable JSON object, which the canonical schema adapter (ADR 0063) translates to/from the project's own graph format.

## Consequences

- A vendored JS upgrade is a deliberate bump (download, update comment, amend this ADR), not a silent `npm update`.
- Loss of Drawflow upstream means the project owns the fork; the vendored copy is frozen until a conscious upgrade decision.
- No build step: `drawflow.min.js` and `drawflow.min.css` are served verbatim from `web/static/`.
- Both files must be vendored together; the CSS controls port and node chrome that the JS depends on.
- Drawflow's port-naming convention leaks only into the adapter module (ADR 0063); the rest of the codebase sees only the canonical schema.
