# ADR 0065 — Typed return values on window/process primitives

**Date:** 2026-04-26
**Status:** Accepted

## Context

The graph runtime (ADR 0064) needs source values to flow through data wires. A data wire connects an output port of one node to an input port of another. Without return values from pipeline primitives, every data wire would carry `None`, making typed-data wiring useless for the most common use case: passing a window handle from a `focus` call to a subsequent `close_window` call, or chaining the resolved target of an `open` call.

Currently `focus`, `open`, and `last` all return `None`. Their Python signatures are typed `-> None`. The rest of the system never inspects their return values because `Dispatcher.run_plan()` discards them after firing each tool.

## Decision

Change the return type of `focus`, `open`, and `last` to return their resolved window handle as `hwnd: int`:

- `focus(target)` → returns the `hwnd` of the focused window, as verified by `GetForegroundWindow()` (ADR 0037).
- `open(target)` → returns the `hwnd` of the opened application window after focus settle.
- `last()` → returns the `hwnd` of the window that was activated.

Existing void-style callers (the `Dispatcher.run_plan()` path) are unaffected because Python silently discards unused return values. No existing call site needs to change.

Wrapping return values in a separate `ToolResult` value object was considered and rejected: it adds ceremony for the only three primitives that currently need typed returns, and the graph runtime can park a plain `int` in its `_record_returns` map without any wrapper class.

## Consequences

- The signature/TOML drift validator (ADR 0036) gains a `returns` rule: if a Python signature declares a non-`None` return type, the sidecar TOML may optionally document it with a `[tool.returns]` sub-table; missing entries are warned, not errored, to avoid breaking existing tools.
- `GraphRuntime._record_returns` parks `hwnd` values in a node-keyed dict, making them available to downstream data-wire consumers.
- No breaking changes for existing callers — Python silently discards return values when the caller does not capture them.
- Future primitives that produce values useful to downstream nodes should declare their return type explicitly; the convention is established here.
- `close` and `close_window` are not changed; they produce no value useful for downstream wiring.
