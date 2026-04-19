# ADR 0006: module-global tool registry via @tool decorator

**Status:** Accepted
**Date:** 2026-04-19

## Context

Voice Commander needs a mechanism for each tool (e.g. `OpenBrowserTool`, `TypeTextTool`) to declare the voice phrases that should trigger it, and for the matching engine to discover all registered tools at daemon start-up. The system must be modular — adding a new tool should require touching only one file — and it must be testable in isolation without running the full daemon. The registry must survive the daemon's multi-threaded architecture: tools register at import time (single-threaded module initialisation), and the registry is read-only thereafter.

## Decision

We use a module-global `_REGISTRY: dict[str, Callable]` in `voice_commander/registry.py` populated entirely at import time via a `@tool(phrases=[...])` decorator. Each tool module applies the decorator to its handler function, which inserts the function into the global registry keyed by every phrase string it declares. The daemon calls `registry.discover()` at start-up, which programmatically imports every submodule under `voice_commander/tools/`, triggering all `@tool` decorations as a side effect. Test files can directly import individual tool modules and inspect the registry without starting the daemon.

## Consequences

### Positive
- Adding a new tool requires only creating a new file in `voice_commander/tools/` and applying `@tool(phrases=[...])` — zero changes to any existing file.
- Phrases are co-located with the code that executes them, eliminating drift between declaration and implementation.
- Module-level registration happens at import time in the main thread before any listener or worker threads start, so no locking is required on the registry dict.
- Tools are individually importable and unit-testable without the daemon infrastructure.

### Negative
- The module-global registry is a form of global mutable state; tests that register tools in one test can inadvertently affect subsequent tests if the registry is not cleared between runs. Mitigated by providing a `registry.clear()` helper and using it in test fixtures.
- `discover()` uses `importlib` to enumerate submodules; if a tool module has a top-level import error, the entire discover step fails. This surfaces bugs quickly but means a single broken tool can prevent the daemon from starting.
- Phrase strings are plain Python string literals with no validation at decoration time; typos are caught only when the matching engine fails to fire.

### Neutral
- The decorator pattern is idiomatic Python and immediately recognisable to contributors familiar with Flask routes or pytest fixtures.
- The registry is a plain `dict`; if the tool count grows to thousands, a more sophisticated data structure (trie, inverted index) could replace the backing store without changing the decorator API.

## Alternatives considered

### Per-tool YAML manifests
Each tool ships a companion `<tool_name>.yaml` file declaring its phrases, and a loader reads all YAML files at start-up. This separates declaration from implementation, which adds friction: every new tool requires creating and maintaining two files, the YAML schema must be validated, and an out-of-sync manifest causes a silent miss. Keeping phrases in code is strictly simpler and less error-prone.

### Central registry.yaml
A single `voice_commander/registry.yaml` maps phrase strings to fully-qualified Python callable paths. This is a common pattern in plugin systems but has two drawbacks for this project: it becomes a merge-conflict hotspot as multiple tools are added in parallel, and phrases inevitably drift from the function signatures they reference because changes require editing two separate files. The decorator approach eliminates both problems by making phrase registration a property of the function itself.

## References
- Spec: [../superpowers/specs/2026-04-19-voice-commander-design.md](../superpowers/specs/2026-04-19-voice-commander-design.md)
- Python `importlib` documentation: https://docs.python.org/3/library/importlib.html
- PEP 318 — Decorators for Functions and Methods: https://peps.python.org/pep-0318/
