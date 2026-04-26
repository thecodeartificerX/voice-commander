# ADR 0063 — Canonical graph schema with lib-isolated adapter

**Date:** 2026-04-26
**Status:** Accepted

## Context

Drawflow's native `editor.export()` format intermingles semantic graph data (nodes, connections, types) with UI hints (pixel positions, class names, HTML fragments). Storing this verbatim would couple the on-disk format to a specific version of a specific frontend library, making the files unreadable to humans without running a browser and fragile to any future library swap.

The project also needs graph files to be hand-editable: a power user or agent should be able to open a `.json` workflow file, understand its structure without a reference to Drawflow internals, and make changes in a text editor. This is consistent with the project's broader principle that configuration is human-readable (ADR — `config.toml` as the single source of truth).

## Decision

Define a **canonical graph JSON schema** with the following top-level shape:

```json
{
  "schema_version": 1,
  "name": "string",
  "kind": "command | workflow",
  "llm_visible": true,
  "nodes": [...],
  "edges": [...],
  "inputs":  [...],
  "outputs": [...]
}
```

A thin adapter module, `commands/graph_drawflow.py`, is the only place in the codebase that knows Drawflow's format. It exports two pure functions:

- `to_drawflow(canonical: dict) -> dict` — converts canonical graph to Drawflow's `editor.import()` shape.
- `from_drawflow(drawflow: dict) -> dict` — converts Drawflow's `editor.export()` shape to canonical.

The server-side web routes call `to_drawflow` when serving a graph to the editor and `from_drawflow` when receiving a save from the client. `builder.js` (client-side) works exclusively with Drawflow's native format; the canonical ↔ Drawflow translation happens in Python, never in the browser. The runtime, the CLI, the commander skill, and the test suite all work exclusively with the canonical schema.

The alternative — saving Drawflow JSON verbatim — was rejected because it locks the data to the library, embeds HTML fragments in what should be a pure-data file, and makes every future library swap a data migration rather than a UI change.

## Consequences

- Roundtrip fixed-point tests are mandatory: `from_drawflow(to_drawflow(g)) == g` for a representative suite of graphs. These live in `tests/unit/test_graph_drawflow.py`.
- A future swap to a different node-editor library requires a new adapter module and a frontend rewrite, but involves no data migration — existing canonical JSON files are untouched.
- The canonical schema is human-readable and hand-editable without any tooling.
- Drawflow's port-naming convention (`input_1`, `output_1`, etc.) is the only Drawflow-specific detail; it is mapped to/from the canonical `inputs`/`outputs` arrays inside the adapter and never surfaces elsewhere.
- `schema_version` allows future non-breaking additions; breaking changes increment the version and require a migration script.
