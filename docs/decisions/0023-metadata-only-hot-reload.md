# ADR 0023: Hot reload is metadata-only — phrases, description, category, enabled

**Status:** Accepted
**Date:** 2026-04-20

## Context

Once tool metadata moves to sidecar TOMLs (ADR 0021), users expect phrase edits made via the web UI to take effect immediately — without restarting the daemon. Two levels of reload are possible:

1. **Metadata-only reload** — re-read the TOML files and update the in-memory `ToolEntry` fields (`phrases`, `description`, `category`, `enabled`) in place. Function references are unchanged.
2. **Full module reimport** — call `importlib.reload()` on the tool module, re-execute module-level code, and re-register all tool functions from scratch. This picks up changes to Python source (new functions, changed logic).

## Decision

Implement metadata-only hot reload. Full module reimport is explicitly deferred.

`reload_metadata()` on `ToolRegistry`:

1. Acquires the registry `threading.Lock`.
2. For each registered `ToolEntry`, resolves the sidecar TOML path from the module's `__file__` attribute.
3. Re-parses the TOML.
4. Updates `entry.phrases`, `entry.description`, `entry.category`, and `entry.enabled` in place.
5. Releases the lock.

The web UI calls `POST /tools/{name}/reload` (or the reload is triggered automatically after a phrase PUT/DELETE). The `Matcher` reads `registry.all_entries()` on every match call, so it sees updated phrases immediately after the lock is released — no matcher restart required.

Full `importlib.reload()` is intentionally not implemented now. It will be added as `rescan_tools()` in a future phase when the use case (adding a new tool without restarting) is validated.

## Consequences

### Positive
- Safe: no module re-execution, no risk of double-registration, no stale references.
- Fast: TOML parse is microseconds; lock hold time is negligible.
- Covers the dominant use case — phrase editing — which accounts for ~95% of expected metadata changes.
- The `Matcher` picks up new phrases on the next utterance with no additional wiring.

### Negative
- Adding a new tool function still requires a daemon restart. This is documented in the UI with a banner: "Restart the daemon to pick up new tools."
- Removing a tool function from Python without restarting leaves a stale `ToolEntry` with a dangling function reference until restart. Acceptable — the function still works; the registry just has a ghost entry.
- Developers iterating on tool logic (not just phrases) still need to restart. This is the same workflow as before the web UI, so no regression.

### Neutral
- `importlib.reload()` has well-documented pitfalls on CPython: module-level side effects re-execute (e.g. global state initialised on import), existing references to objects defined in the old module version are not updated, and decorator re-execution can cause double-registration. Deferring full reimport is the conservative and correct choice at this stage.

## Alternatives considered

### Full `importlib.reload()` now
Tempting but unsafe without extensive guard rails. The `@tool()` decorator would need to detect re-registration and overwrite rather than append. Module-level state (e.g. `winsound` handles, subprocess references) would re-initialise. Any code holding a reference to an old tool function would not see the new version. Deferred until the use case is validated and the guard rails are designed.

### File-system watcher (watchdog) for automatic reload
`watchdog` could trigger `reload_metadata()` automatically when a TOML file changes. Deferred: adds a dependency and a background thread for a minor UX improvement. The web UI already triggers reload on every edit — manual file edits outside the UI are an edge case.

## References
- ADR 0021: sidecar TOML per tool
- ADR 0024: bare decorator migration
- `src/voice_commander/registry.py` — `reload_metadata()` implementation
- Python `importlib.reload` caveats: https://docs.python.org/3/library/importlib.html#importlib.reload
