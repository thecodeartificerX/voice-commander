# ADR 0067 — Per-graph llm_visible flag

**Date:** 2026-04-26
**Status:** Accepted

## Context

Small mixture-of-experts models such as Gemma 4 E4B degrade in tool-calling reliability past approximately 30 tools. As the node-graph builder allows users to author an arbitrary number of commands and workflows, each registered as a `ToolEntry`, the LLM's effective tool list will grow without bound unless there is a way to exclude helper graphs.

Cross-graph references are a key composability feature of the graph runtime (ADR 0064): a workflow can call another graph as a sub-graph by referencing it by name. This means a graph can be a useful building block without ever being a top-level voice command. An internal helper like `format_search_query` should be callable from other graphs but invisible to the LLM.

Tag-based contextual filtering (e.g. "show only tools relevant to the active foreground app") is a desirable future capability — documented in the spec as scope NG5 — but it introduces a foreground-app detection dependency and a tag-management UI. It is deferred to post-v1.

## Decision

Add `llm_visible: bool = True` to the canonical graph schema (ADR 0063) and propagate it to `ToolEntry`. When `llm_visible = False`:

- The graph's `ToolEntry` is registered in the full tool registry (so cross-graph `call_graph` nodes can find it by name).
- `LLMRouter.all_llm_visible()` excludes the entry from the tools array sent to the LLM.
- The builder UI shows the graph in the graph list with a visual indicator (e.g. a dimmed eye icon).

The builder UI exposes `llm_visible` as a checkbox on each graph's settings panel.

The alternative — tag-based contextual filtering — is explicitly deferred. The `llm_visible` flag is the minimal mechanism that solves the tool-count problem without introducing app-detection or tag-management complexity.

## Consequences

- The builder UI gains a `llm_visible` checkbox on the graph settings panel; the default is `true`.
- `LLMRouter.all_llm_visible()` (or equivalent filter method) is the single place in the codebase that enforces this flag; it is called when building the tools array for each LLM request.
- Internal helper graphs can be authored without affecting LLM performance or the model's effective tool count.
- `schema_version` is bumped in the canonical schema (ADR 0063) when this field is first persisted; existing graphs without the field are treated as `llm_visible = true` for backward compatibility.
- Deferred scope NG5 (foreground-app tool subsets) is the post-v1 evolution of this flag; NG5 will likely extend `llm_visible` with a richer predicate rather than replacing it.
