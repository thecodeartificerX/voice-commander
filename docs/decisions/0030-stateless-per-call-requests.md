# ADR 0030: Stateless Per-Utterance LLM Requests

**Status:** Accepted
**Date:** 2026-04-21

## Context

A multi-turn conversational router would send the growing message history with each request, letting the LLM reference prior utterances to resolve pronouns ("paste it"), handle corrections ("no, the other tab"), or accumulate context across commands. This is the natural model for a chat assistant.

Voice Commander's use case is different. The user speaks isolated imperative commands. Each utterance is self-contained: "open new tab", "focus browser then type hello", "close tab". There are no pronouns referring to previous commands, no multi-turn negotiations, and no state accumulated across the session that the LLM needs to reason about.

Maintaining conversation history introduces cost without benefit in this context:

1. **Growing prompt tokens** — each call includes all prior turns. On a small model with a tight latency budget, the growing prefix quickly pushes past the point where the response fits in the 600 ms window.
2. **Stale context interference** — old commands in the history may confuse the model when a new, unrelated command arrives.
3. **Cache invalidation** — the `messages` array changes every call, so even if LM Studio's prefix KV cache worked perfectly (R1 §5 notes it is unreliable for some architectures), only the system prompt + tools prefix could be reused, not the message history.

R1 §5 confirms: LM Studio prefix KV cache reuse applies to the *unchanging* prefix (system prompt + tools array). The user message changes per utterance regardless. A stateless design maximises the portion of the prompt that is stable and therefore cache-eligible.

## Decision

`LLMRouter.route(transcript)` sends a fresh two-message request every call: one static `system` message and one `user` message containing only the current transcript. No prior messages, no tool results, no conversation history. The request shape is:

```json
{
  "model": "<configured model_id>",
  "messages": [
    {"role": "system", "content": "<short static system prompt>"},
    {"role": "user",   "content": "<transcript>"}
  ],
  "tools": [ /* derived from registry — stable per daemon run */ ],
  "tool_choice": "required",
  "temperature": 0,
  "stream": false
}
```

The `tools` array is built once at daemon startup from the registry and reused unchanged across all calls. The `system` message is also static. Together these form the stable prefix that LM Studio can (when its backend supports it) cache across calls, reducing first-token latency to near-zero for subsequent requests.

## Consequences

### Positive

- Prompt size is constant regardless of session length. Latency does not grow across a long session.
- The stable `system + tools` prefix maximises prefix KV cache opportunities for models and backends where cache reuse works (full-attention models on llama.cpp — R1 §5).
- Simpler code: no session state to track, no message list to append to, no expiry policy to implement.
- Clean failure semantics: if the router returns `None`, the only state to discard is the in-flight HTTP request. Nothing is corrupted.

### Negative

- The router cannot resolve cross-utterance references ("paste it" after a copy command) or handle corrections without the user restating the full intent. This is a deliberate scope restriction (spec §Non-goals: "multi-turn conversational context across utterances").
- R1 §5 notes that for hybrid-attention models (Qwen3.5, sliding-window), prefix KV cache reuse is silently disabled in LM Studio, forcing full prompt recompute per request. Stateless design is still optimal here — it simply doesn't benefit from the cache, but it doesn't suffer more than a stateful design would.

### Neutral

- Future phases may introduce limited context windows (e.g., "last N commands" as a hint in the system prompt). This would be an extension to the stateless model, not a replacement. The architecture supports it via the configurable system prompt without changing the single-user-message structure.

## Alternatives considered

### Rolling window of last N utterances
Rejected for MVP. Adds complexity (window management, expiry, privacy implications of retaining audio-derived text), grows the prompt, and provides uncertain benefit for isolated command phrases. Can be added in Phase 6+ if user research shows a need for cross-utterance references.

### Full conversation history (unbounded)
Rejected. Prompt grows without bound over a long session. Latency becomes unpredictable and eventually exceeds the 600 ms budget.

## References

- Spec §Non-goals, §LLM path, §Latency model — `docs/superpowers/specs/2026-04-21-llm-router-design.md`
- R1 §5 — LM Studio prefix KV cache reuse: limitations and practical impact
- ADR 0026 — hybrid routing; LLM path description
- ADR 0028 — LM Studio endpoint; warmup ping rationale
