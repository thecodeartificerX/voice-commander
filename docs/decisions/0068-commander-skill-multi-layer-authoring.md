# ADR 0068 — Commander skill multi-layer authoring

**Date:** 2026-04-26
**Status:** Accepted

## Context

The `commander` skill today interviews the user for a single layer of authoring: pipeline primitives. It writes a Python function in `tools/primitives.py` and a sidecar `.toml` entry, then runs the startup validator to catch sig↔TOML drift at creation time (ADR 0035, ADR 0036).

With the three-layer model introduced by the node-graph builder (primitive → command → workflow), users need a way to author all three layers from the terminal without opening the web UI. The builder UI is the preferred authoring surface for commands and workflows, but it requires the daemon to be running. The skill must remain usable in offline, headless, and scripted contexts.

A "web-UI-to-skill bridge" — having the skill open a browser tab or POST to the web UI's API — was explicitly rejected (spec NG4): it introduces a circular dependency (skill needs the daemon running; daemon may be restarting), complicates testing, and breaks the skill's terminal-driven, stateless contract.

## Decision

Expand the `commander` skill to support all three layers via a branching interview:

1. **Primitive authoring** (unchanged) — Python function + sidecar TOML, written to `tools/primitives.py` and the appropriate `.toml` file. Startup validator runs post-write.
2. **Command authoring** — the skill interviews for node structure (what primitives to call, in what order, with what arguments) and writes a canonical graph JSON file directly to `commands/<name>.json`. No Python code is written.
3. **Workflow authoring** — same as command authoring, but written to `workflows/<name>.json`. The interview may additionally ask about branching logic, though v1 of the skill only authors linear single-branch workflows; complex DAG authoring is delegated to the builder UI.

For layers 2 and 3, the skill performs a direct file write of canonical JSON (ADR 0063) without invoking the web UI or the running daemon. Hot-reload picks up the new files automatically (ADR 0023).

The skill's `SKILL.md` is updated to document the three-layer interview flow and the file destinations for each layer.

## Consequences

- The skill's `SKILL.md` grows in scope but the skill stays terminal-driven and stateless; it has no runtime dependency on the daemon or the web UI.
- Hot-reload picks up new command/workflow JSON files automatically; the user does not need to restart the daemon after skill-authored commands.
- The primitive authoring path (Python + TOML + validator) is unchanged; ADR 0035 and ADR 0036 continue to apply.
- The skill does not attempt to author complex DAGs (branching, looping) via the terminal interview; users are directed to the builder UI for those. This is a deliberate scope boundary, not a gap.
- The skill's output for command/workflow layers is deterministic and idempotent: writing the same interview answers twice produces the same file. This makes skill output suitable for scripting and CI.
