# ADR 0064 — Graph runtime supersedes linear workflow steps

**Date:** 2026-04-26
**Status:** Accepted

## Context

The current `WorkflowDef.steps[]` model is linear: a workflow is an ordered list of tool calls, each with fixed arguments. It cannot express branching (if the clipboard contains X, do Y; otherwise do Z), looping, or typed-data wiring (pass the return value of `get_active_window_title` as an argument to the next node).

The LLMRouter emits a flat `Plan` of named tool calls. `Dispatcher.run_plan()` walks the plan list in order, applying per-tool `settle_ms` delays. Both of these interfaces are stable and relied upon by the sprite / HUD pipeline (ADR 0048, ADR 0051, ADR 0055). The `PlanOutcome` wire format is consumed by the EventBus and the sprite; changing it would break those consumers.

## Decision

Introduce a `GraphRuntime` class that walks a canonical graph (ADR 0063) as a DAG: it resolves data-wire dependencies, executes nodes in topological order, records return values, and propagates typed data between connected ports.

The public-facing interfaces are **not changed**:

- `LLMRouter.route()` continues to return a flat `Plan` of named tool calls.
- `Dispatcher.run_plan()` continues to accept a `Plan` and return a `PlanOutcome`.
- Each graph is registered in the tool registry as a `ToolEntry` whose `.func` closure delegates to `GraphRuntime.run(graph, kwargs)`.

From `LLMRouter`'s and `Dispatcher`'s perspective, a graph-backed command is indistinguishable from a primitive tool. The LLM never sees internal graph structure; it sees only the graph's top-level name and description.

The alternative — having the LLM author DAGs at runtime — was rejected because small MoEs (e.g. Gemma 4 E4B) are unreliable at generating valid graph structures under real-time latency constraints. The flat `Plan` of named calls remains the LLM's output contract.

## Consequences

- Workflow execution is no longer always a single path through `Dispatcher`; a single `ToolCall` in the plan can internally execute an entire DAG.
- Per-step `settle_ms` is now per-node within `GraphRuntime`, not at the dispatcher level. `Dispatcher.run_plan()` sees the entire graph execution as a single tool call and applies only the graph-level `settle_ms` (if any).
- `PlanOutcome` wire format is unchanged — sprite and HUD compatibility is preserved.
- `WorkflowDef.steps[]` (the old linear model) is superseded; existing linear workflows should be migrated to single-branch graphs or left in place until they are next edited.
- `template.py`'s `{placeholder}` string substitution is superseded by typed input wires in the graph schema; the module is retained for backward compatibility but marked deprecated.
