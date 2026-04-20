# ADR 0024: Migrate `@tool(phrases=[...])` to bare `@tool()` decorator

**Status:** Accepted
**Date:** 2026-04-20

## Context

The original `@tool(phrases=[...])` decorator served a dual purpose: it registered the function with `ToolRegistry` and embedded the phrase list inline. Once phrases migrate to sidecar TOMLs (ADR 0021), the decorator no longer needs to carry metadata — but the existing four tool modules all use the argument-bearing form. A clean migration path is needed.

Two options exist:

1. **Keep `@tool(phrases=[...])` as a deprecated alias** — the decorator still accepts `phrases` but ignores them if a sidecar TOML is present. Dual code paths, ambiguous source of truth.
2. **Replace with bare `@tool()`** — the decorator registers only the function name and module path. Metadata comes exclusively from TOML. A migration script generates initial TOMLs from the current decorator args.

## Decision

Replace `@tool(phrases=[...])` with bare `@tool()` across all tool modules.

The new decorator contract:

```python
@tool()
def copy() -> None:
    """Copy selected text to clipboard."""
    ...
```

`@tool()` registers `(function, module.__file__)` into an internal pre-bind list. No phrases, no description, no category — those live in the sidecar TOML.

At daemon startup, after all tool modules are imported:

1. `ToolMetadataStore.load_all(tools_pkg_path)` scans for `*.toml` files in the tools directory and parses each one into a dict keyed by function name.
2. `registry.bind_metadata(store)` iterates the pre-bind list, looks up each function name in the store, and creates a fully-populated `ToolEntry`. A function present in Python but absent from the TOML raises `RegistryBindError` with a clear message identifying the missing entry.

Migration is a one-time script (`scripts/migrate_phrases_to_toml.py`) that:

1. Imports each tool module.
2. Uses `ast.parse` to extract `phrases=[...]` arguments from `@tool(...)` call nodes.
3. Writes the corresponding sidecar TOML.
4. Prints a diff of what would be written before committing — operator confirms before files are created.

After the script runs, decorator arguments are stripped from the Python source.

## Consequences

### Positive
- Single source of truth for phrases: the sidecar TOML. No ambiguity.
- Decorator is simpler — it carries no data, only marks the function for registration.
- Mismatch detection at startup (`RegistryBindError`) catches the common developer error of adding a Python function without a matching TOML entry.
- Future tool modules need only a bare `@tool()` and a companion `.toml` — the pattern is uniform.

### Negative
- Migration script must be run once against existing installs. On first startup after the upgrade, if the script has not been run, the daemon will refuse to start with a `RegistryBindError` listing the unbound functions.
- Developers adding a new tool must remember to create both the Python function with `@tool()` and the TOML entry. The startup binding check is the safety net, but the error is only caught at runtime.
- `ast.parse`-based phrase extraction in the migration script is brittle against non-literal `phrases` arguments (e.g. `phrases=MY_PHRASES` where `MY_PHRASES` is a module-level variable). None of the existing tools use this pattern; it is noted in the script's output as a manual-review item.

### Neutral
- The `@tool()` parentheses are retained (rather than bare `@tool`) for forward compatibility: future keyword arguments (`priority`, `requires_focus`, etc.) can be added without changing the decorator syntax at call sites.

## Alternatives considered

### Keep `@tool(phrases=[...])` as deprecated alias, TOML takes precedence
Dual code paths persist indefinitely. Developers are uncertain which source of truth applies. Any tooling that reads phrases (the web UI, the migration script, tests) must handle both cases. Rejected: the ambiguity cost compounds over time.

### TOML only, no decorator at all — use module naming convention
Auto-discover all functions in `tools/*.py` that match a naming pattern (e.g. not prefixed with `_`). No decorator needed. Rejected: too implicit — any helper function in a tool module would be registered unless carefully prefixed. The explicit `@tool()` opt-in is safer and more readable.

## References
- ADR 0006: original tool decorator registry
- ADR 0021: sidecar TOML per tool
- ADR 0023: metadata-only hot reload
- `scripts/migrate_phrases_to_toml.py` — migration script
- `src/voice_commander/registry.py` — `bind_metadata()` implementation
