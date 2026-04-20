# ADR 0021: Store tool metadata in sidecar TOML files next to Python modules

**Status:** Accepted
**Date:** 2026-04-20

## Context

In the MVP implementation, every tool's phrases are declared inline in the `@tool(phrases=[...])` decorator inside the Python module. Changing a phrase — the most common user customisation — requires:

1. Opening a `.py` file.
2. Editing the `phrases` list.
3. Restarting the daemon.

This is hostile to a non-technical user and makes the web UI meaningless as a phrase editor: any UI-driven edit would have to write back to Python source, which is fragile and unsafe.

A separate storage layer for tool metadata — phrases, description, category, enabled flag — is required before the web UI is built.

## Decision

Store tool metadata in sidecar TOML files, one per tool module, co-located with the Python file.

- `clipboard.py` → `clipboard.toml`
- `window.py` → `window.toml`
- `browser.py` → `browser.toml`
- `system.py` → `system.toml`

Each TOML file contains one `[[tool]]` table per function registered in the module:

```toml
[[tool]]
name = "copy"
phrases = ["copy", "copy that", "yank"]
description = "Copy selected text to clipboard."
category = "clipboard"
enabled = true
```

The `@tool()` decorator becomes bare — it registers the function name and module path only. `ToolMetadataStore.load_all()` reads all sidecar TOMLs at startup, and `registry.bind_metadata()` pairs each function with its TOML entry by name. A mismatch (function present in Python but absent from TOML, or vice versa) raises a `RegistryBindError` at startup so the error is caught early.

A one-time migration script (`scripts/migrate_phrases_to_toml.py`) reads the existing decorator arguments and writes the initial TOML files.

## Consequences

### Positive
- Phrases can be edited via the web UI without touching Python source.
- Hot reload of metadata is safe: re-read TOMLs, update `ToolEntry` fields in place, no module reimport needed (see ADR 0023).
- TOML is human-readable and diffable — users can also edit it directly in any text editor.
- The `enabled` flag moves out of code, making it easy to disable a tool without deleting it.

### Negative
- Two files per tool module instead of one. Developers must remember to update the TOML when adding a new tool function.
- The startup binding step adds a small validation pass that didn't exist before.
- Migration script must be run once against existing installs; the daemon refuses to start if metadata is missing (with a clear error message).

### Neutral
- TOML was chosen over JSON (less readable, no comments) and YAML (indentation-sensitive, surprising edge cases). TOML aligns with the existing `config.toml` choice for the project.

## Alternatives considered

### Keep phrases in the decorator; edit Python source from the UI
Rejected: writing back to Python source from a web server is unsafe, hard to parse reliably, and breaks linters and formatters.

### Single central `phrases.toml` listing all tools
Viable but rejected: co-location with the module is more discoverable. When a developer opens `clipboard.py` they immediately see `clipboard.toml` beside it. A central file grows into a maintenance bottleneck and loses the "drop a file to add a tool" ergonomic goal.

### SQLite database for metadata
Over-engineered for the current scale (< 20 tools). TOML files are version-controllable and diff cleanly in PRs. A database would be the right move if we ever support user-created tools with a richer schema.

## References
- ADR 0006: tool decorator registry (original design)
- ADR 0023: metadata-only hot reload
- ADR 0024: bare decorator migration
- `scripts/migrate_phrases_to_toml.py` — migration script
- TOML spec: https://toml.io/en/
