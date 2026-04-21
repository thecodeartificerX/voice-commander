# ADR 0026: Hybrid Routing — rapidfuzz First, LLM on Escalation

**Status:** Accepted
**Date:** 2026-04-21

## Context

Phase 4 ships a rapidfuzz-only router. A single utterance maps to a single zero-argument tool function via fuzzy string matching. This works well for fixed command phrases ("copy", "new tab") but fails for two classes of user intent:

1. **Natural phrasing** — "focus the browser and open a new tab" does not match any single phrase in the registry, even with a loose threshold.
2. **Argument-bearing commands** — "open readme in the projects folder" requires slot-filling that string distance cannot provide.

The obvious fix is to run every utterance through an LLM. But an LLM call costs ~500 ms–2 s even on a fast local GPU (R2, R1 §5), while the existing rapidfuzz path costs ~1 ms. Replacing rapidfuzz with an LLM would degrade the 99 % of utterances that are simple fixed commands.

The alternative is a hybrid: let rapidfuzz handle the hot path at its current latency, and escalate only the utterances that do not reach a high-confidence match. This preserves the existing latency guarantee and unlocks natural-language routing for complex commands, without requiring a new thread or a streaming response model.

## Decision

Introduce a two-stage pipeline inside the existing pipeline worker thread:

1. `Matcher.match(transcript)` runs first. If the best score is at or above `[matching].threshold` (recommended 95 when the LLM router is enabled — see ADR 0027), the matched tool is dispatched immediately. No LLM call is made.
2. If the score is below threshold, `LLMRouter.route(transcript)` is called. The router returns a `Plan` (ordered list of `ToolCall` objects) or `None` on any error. A `Plan` is handed to `Dispatcher.run_plan`. `None` triggers the miss chime.

No second threshold is introduced. The single `[matching].threshold` config knob controls the split point. The LLM path is opt-in via `[llm_router].enabled = false` (default); when disabled, the pipeline is unchanged from the Phase 4 behaviour.

## Consequences

### Positive

- Common single-word commands retain ~1 ms routing latency. No regression on the hot path.
- Natural chained utterances and argument-bearing commands are handled without retraining any model.
- Feature is strictly additive: the default config keeps `enabled = false`, so the branch can be merged without changing behaviour for users who have not opted in.
- Single worker thread; no new concurrency. Synchronous HTTP on the pipeline thread is acceptable because the pipeline already serialises per utterance (ADR 0010).
- Failure modes are bounded: LM Studio offline or slow returns `None`, which the existing miss path already handles.

### Negative

- LLM path adds 500 ms–2 s per escalated utterance. Acceptable for natural commands, but users who escalate frequently on slow hardware will notice.
- Adds a new external runtime dependency (LM Studio running locally). The daemon must survive LM Studio being absent.
- Two code paths to test and maintain.

### Neutral

- The split point is a single config knob. Operators tuning the threshold for one path implicitly tune the other.

## Alternatives considered

### LLM for every utterance
Rejected. Replacing rapidfuzz adds 500× latency to every command. Unacceptable for common single-word commands where the user expects near-instant response.

### A second, lower LLM threshold (three tiers)
Rejected. Three tiers (hot / medium / LLM) add complexity without clear benefit. The binary split at one threshold is simpler to reason about and to tune.

### Async LLM call with speculative rapidfuzz execution
Rejected. Running both paths in parallel and picking the first result would require an async model on the pipeline thread, add race conditions, and potentially fire the wrong tool. The sequential fallback model is correct and simple.

## References

- Spec §Architecture, §Routing behaviour — `docs/superpowers/specs/2026-04-21-llm-router-design.md`
- ADR 0010 — threading model (pipeline worker serialises per utterance)
- ADR 0027 — threshold tightening when LLM router is enabled
- R1 §5 — LM Studio prefix KV cache and latency expectations
- R2 §4 — Gemma 4 E4B steady-state latency on consumer GPU
