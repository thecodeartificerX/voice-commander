# ADR 0041: Drop rapidfuzz Dependency

**Status:** Accepted
**Date:** 2026-04-21

## Context

With LLM-only routing (ADR 0040), `rapidfuzz` is no longer used anywhere in the codebase. The `Matcher` subsystem it powered has been deleted. No other module imports or calls into `rapidfuzz`.

`rapidfuzz` is a C++ extension (`>=3.9.0`). It requires a native wheel at install time. On a platform without a pre-built wheel it would require a C++ compiler. Even with wheels available, it is a non-trivial dependency to carry: it adds ~6 MB to the install footprint, shows up in security scans, and must be updated when new Python releases ship incompatible ABI tags.

## Decision

1. Remove `rapidfuzz` from `pyproject.toml` dependencies.
2. Delete `src/voice_commander/matcher.py`.
3. Remove `phrases` keys from all tool TOML sidecars (they were only consumed by `flat_phrases()`).
4. Remove `flat_phrases()` and `flat_phrases_enabled()` from `ToolRegistry`.
5. Remove the `ToolEntry.phrases` field population — the field remains as an empty tuple for backward compatibility with any external code that reads `ToolEntry`, but it is never populated from TOML.

## Consequences

### Positive

- One fewer native C++ dependency. Simpler install, smaller footprint, no ABI concerns on new Python releases.
- `pyproject.toml` and `uv.lock` are both simplified.
- Tool TOML sidecars are simpler — only `[tool]`, `[tool.args.*]`, `settle_ms`, and `llm_only` remain.
- `ToolRegistry` public API shrinks; fewer methods to test and document.

### Negative

- `ToolEntry.phrases` becomes a permanently-empty field. Code that previously populated it (the `@tool(phrases=[...])` decorator path) now ignores the `phrases` argument. This is a source-level change but not a breaking runtime change.
- Any external tooling or scripts that read `ToolEntry.phrases` expecting phrase data will find an empty tuple. No such tooling is known to exist outside this repo.

### Neutral

- The `llm_only` flag on `ToolEntry` is retained (see ADR 0040 for revised meaning).
- The `@tool` decorator signature may retain a `phrases` parameter for a transition period, but it is ignored and emits a deprecation log at registration time.

## Related

- ADR 0040 — LLM-only routing (the decision that makes this dependency drop possible)
- ADR 0005 — original rapidfuzz matching decision (superseded by ADR 0040)
