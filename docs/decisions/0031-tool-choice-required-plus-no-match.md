# ADR 0031: `tool_choice="required"` Combined with a `no_match` Escape Primitive

**Status:** Accepted
**Date:** 2026-04-21

## Context

When the LLM router receives an utterance it cannot confidently map to any tool, the response must be unambiguous: either call a known tool correctly, or signal that no match was found. The danger of a free-form response is that the model emits prose ("I'm sorry, I don't understand") which the parser cannot interpret as a tool call, silently drops, and then the daemon has no action to take and no feedback to give the user.

Two mechanisms are available to prevent this:

1. **`tool_choice="required"`** — the LLM endpoint parameter that forces the model to emit at least one tool call. If the model would have generated text, the backend overrides it and forces a tool-call structure. R1 §2 confirms LM Studio honours this parameter; for llama.cpp (GGUF) backends, grammar-based constraint sampling enforces the JSON structure even when the model resists.

2. **`no_match(reason: str)` tool** — a dedicated tool that the model calls when none of the real tools is appropriate. It is the model's sanctioned escape hatch under `tool_choice="required"`. Without it, the model is forced to hallucinate a plausible-looking tool call for an utterance it cannot route.

These two mechanisms are complementary, not redundant. `tool_choice="required"` ensures the response is always a tool call. `no_match` ensures the model has a valid tool to call when it cannot route the utterance. Together they close the loop: no response is ever ambiguous.

R2 §3 notes that for Gemma 4 E4B, `tool_choice="required"` is emulated via system prompt rather than enforced by an explicit API parameter. The `no_match` tool is therefore even more important as a structured escape valve: the system prompt instructs the model to call `no_match` when uncertain, giving it a well-defined way to express uncertainty without violating the schema.

## Decision

Every LLM router request includes:

1. `"tool_choice": "required"` in the request body.
2. `no_match` tool in the `tools` array, defined as:
   - Name: `no_match`
   - Signature: `(reason: str)` — a brief human-readable string explaining why no tool matched.
   - Flagged `llm_only = true` in TOML — invisible to rapidfuzz, never callable on the hot path.
   - Flagged as a required primitive in the startup validator (ADR 0036). The validator fails if `no_match` is absent or not `llm_only`.

The dispatcher short-circuits on `no_match`: if the first step of a `Plan` is a `no_match` call, it skips all subsequent steps (there should be none), logs `reason`, and fires `feedback.on_miss(transcript, reason=reason)`.

## Consequences

### Positive

- Every LLM response is parseable as a tool call. No silent parse failures caused by prose responses.
- The model has a clean way to express "I don't know" without hallucinating a tool name.
- `reason` is logged, providing human-readable audit trail for misses. Operators can inspect logs to identify utterances that should be added to the tool set.
- Grammar-based constraint sampling on llama.cpp backends means even a non-compliant model output is coerced into valid JSON. `no_match` is always a valid choice for that coercion.

### Negative

- `no_match` must remain in the tools array at all times, consuming one slot in the model's tool schema. With a budget of ~25 tools for accuracy (R2 §5), this leaves one fewer slot for real tools.
- If the model calls `no_match` inappropriately (the utterance was actually routeable), the user gets a miss chime instead of the correct action. Mitigation: quality of the system prompt and model selection. The `reason` field provides diagnostic data to improve both.
- R2 §3 notes that for Gemma 4, `tool_choice="required"` is system-prompt-level enforcement, not hard backend enforcement. On models without grammar-based sampling, a sufficiently confused model could still produce non-tool output. The response parser treats any non-`tool_calls` response as equivalent to `no_match` (miss path).

### Neutral

- The `no_match` escape hatch is part of the tool registry. The startup validator enforces its presence and `llm_only` flag. If the commander skill accidentally removes or misconfigures it, the daemon refuses to start (ADR 0036).

## Alternatives considered

### `tool_choice="auto"` with prose detection
Rejected. Requires the response parser to detect whether the response is a tool call or text, and handle the text case. More complex, more fragile. `tool_choice="required"` plus `no_match` is cleaner.

### Sentinel tool call name (`__no_match__`)
Rejected in favour of a properly registered `no_match` tool with a TOML entry and `llm_only = true`. A sentinel that bypasses the registry would be invisible to the validator and the web UI.

### Post-hoc confidence score on tool-call arguments
Rejected. No standard confidence score is available in the OpenAI `tool_calls` response structure. Inspecting argument plausibility would be heuristic and brittle.

## References

- Spec §LLM path (steps 5, 7), §Primitive toolset, §Startup validation rules (rule 7) — `docs/superpowers/specs/2026-04-21-llm-router-design.md`
- R1 §2 — `tool_choice="required"` support across models and backends
- R2 §3 — Gemma 4 tool-choice behaviour (system-prompt enforcement)
- ADR 0032 — one-shot plan execution; `no_match` short-circuit
- ADR 0036 — startup validator; required primitives check
