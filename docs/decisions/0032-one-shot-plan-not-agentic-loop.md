# ADR 0032: One-Shot Plan Execution — No Agentic Observe-and-Replan Loop

**Status:** Accepted
**Date:** 2026-04-21

## Context

A full agentic loop works as follows: the LLM emits a tool call, the executor runs the tool and returns its result as a `tool` role message, the LLM observes the result and emits the next action, and so on until the goal is achieved. This enables adaptive plans: if focusing the browser fails, the LLM can decide to try launching it instead.

However, agentic loops multiply the number of LLM calls by the number of steps in the plan. On a local model with 500 ms–2 s per call, a three-step chain would take 1.5 s–6 s of LLM time alone, before any tool execution. For a voice-driven command launcher, where the user expects near-instant response, this is unacceptable.

The voice commands targeted by the LLM router are also structurally different from the tasks that benefit most from agentic loops. "Focus the browser, open a new tab, type hello" is a deterministic sequence: the steps are known upfront, they do not depend on each other's output, and the correct plan can be inferred from the utterance alone. There is no need to observe the result of step 1 before deciding step 2.

The one exception is error handling: if a mid-chain step fails, an agentic loop could try an alternative. The spec's chosen behaviour is to stop the chain and fire error feedback. This is simpler, predictable, and auditable — and the user can re-issue the command or say a correction.

## Decision

The LLM router returns a single response. That response contains all tool calls for the utterance as a flat ordered list in `choices[0].message.tool_calls`. The `Dispatcher.run_plan` method iterates the list sequentially, sleeping `settle_ms` between steps, and stops on the first error. No tool result is ever sent back to the LLM. No second LLM call is made within a single utterance's routing cycle.

The `Plan` and `ToolCall` dataclasses represent this one-shot structure: a `Plan` is an ordered list of `ToolCall` objects, each holding a resolved function reference and typed arguments. There is no provision for conditional branching, loops, or observe-then-act within a plan.

## Consequences

### Positive

- Latency is bounded: exactly one LLM call per utterance, regardless of plan length. The 600 ms timeout budget applies to the single call, not multiplied by plan steps.
- Implementation is simple: `Dispatcher.run_plan` is a for-loop over `ToolCall` items with a `sleep(settle_ms)` and a try/except per step.
- Deterministic: the same utterance produces the same plan on the same model load. No observation-dependent branching that changes behaviour based on transient system state.
- Testable in isolation: `run_plan` tests mock the tool functions and `time.sleep`; no LLM call needed.

### Negative

- Adaptive recovery is not possible within a single plan. If the browser is not open and `focus_browser` fails, the plan does not fall back to `launch(app="browser")`. The user must re-issue.
- Multi-step plans that depend on intermediate state (e.g., "search for X" where the search box location depends on which tab is active) cannot be correctly handled. Mitigation: keep plans simple; LLM is expected to emit fixed-sequence plans, not state-dependent programs.
- R1 §3 notes that LM Studio supports multiple tool calls in a single response (confirmed for the OpenAI-compatible endpoint). The feature is used, but only in this one-shot, non-iterative mode.

### Neutral

- `max_plan_steps` config key (default 8) caps the number of tool calls per response. A plan exceeding this cap is truncated with a log warning. This prevents runaway plans from a confused model.

## Alternatives considered

### Full agentic loop (observe-act-repeat)
Rejected. Multiplies latency by the number of steps. Unacceptable for voice command latency budget. Spec §Non-goals explicitly excludes agentic loops.

### Two-call design (plan then confirm)
Rejected. A second LLM call to confirm or refine the plan doubles minimum latency and adds no value for the deterministic command chains targeted here.

### Streaming tool calls with early execution
Rejected. Streaming tool calls arrive token-by-token (R1 §3 streaming notes). Executing a step before all steps have arrived creates ordering problems and would require an async streaming parser on the pipeline thread. The added complexity is not justified.

## References

- R1 §3 — multiple tool calls in a single LM Studio response
- ADR 0026 — hybrid routing; pipeline structure
- ADR 0033 — per-tool `settle_ms` and `wait` primitive for chain pacing
