# ADR 0040: LLM-Only Routing Replaces Hybrid

**Status:** Accepted
**Date:** 2026-04-21

## Context

The hybrid router (rapidfuzz first, LLM escalation on miss — ADR 0026) was designed under the assumption that rapidfuzz would handle the common case at ~1 ms latency, with the LLM reserved for natural-language and argument-bearing utterances.

In practice, the LLM router handles all utterances well, including simple single-word commands like "copy" and "new tab". The fuzzy matcher added complexity — a second code path, a config knob to tune, a C++ native dependency, and `phrases` keys in every TOML sidecar — without delivering a clear benefit once the LLM path was reliable and warm. Production logs showed that the majority of rapidfuzz "hot-path" hits were commands the LLM handled correctly in any case.

The ~600 ms overhead of the LLM path on simple commands is acceptable for a voice UX. Voice inherently has a ~100–500 ms speech-end-detection latency; adding 600 ms on top of that remains within the 1.5 s hard ceiling and is imperceptible relative to the time the user spends speaking.

## Decision

Remove rapidfuzz matching entirely. Route all transcripts through the LLM router unconditionally:

1. Delete `matcher.py`.
2. Remove the `--router-mode` CLI flag (and all related CLI routing options).
3. Remove the `[matching]` config section entirely (`config.toml` and `config.local.toml`).
4. Remove `phrases` from all tool TOML sidecars.
5. Remove `flat_phrases()` and `flat_phrases_enabled()` from `ToolRegistry`.
6. Introduce `Resolver` (ADR 0042) as the clean single entry point for routing.

The LLM router is now the only routing path. A miss is `None` returned from `LLMRouter.route()`, which fires `FeedbackSink.on_miss()`.

## Consequences

### Positive

- Architecture simplifies to one routing path: every transcript goes to the LLM router.
- Eliminates the `[matching]` config knob and the `threshold` tuning problem.
- Removes `rapidfuzz` as a C++ native dependency (see ADR 0041).
- Removes `phrases` from TOML sidecars — tool metadata only describes tool arguments and settle timing.
- One code path to test and maintain.

### Negative

- All commands now pay the ~600 ms LLM routing latency, including simple single-word commands that previously hit the rapidfuzz hot path in ~1 ms. This is accepted as a voice-UX trade-off.
- Routing now depends on LM Studio being online. A miss chime fires for every utterance when LM Studio is unreachable. See `gotchas.md` for the LM Studio availability gotcha.

### Neutral

- The `llm_only` flag on `ToolEntry` is retained (some primitives are LLM-only by design — they have parameters, not phrases). The flag now has a different meaning: it marks tools that are invisible to the registry's phrase corpus (which no longer exists for routing) rather than invisible to the fuzzy matcher.

## Supersedes

- ADR 0026 — Hybrid routing (rapidfuzz first, LLM escalation)
- ADR 0027 — Rapidfuzz threshold tightening

## Alternatives considered

### Keep hybrid, lower the routing latency target
Rejected. The complexity cost of the hybrid (two code paths, threshold tuning, rapidfuzz dependency, `phrases` in every TOML) is paid continuously. The latency benefit is marginal in the context of voice UX.

### Keep rapidfuzz as a fast-path within the Resolver
Rejected. The same complexity arguments apply. A single LLM routing path is the simpler and more maintainable architecture.

### Replace rapidfuzz with a lighter embedding-distance approach
Rejected. Any non-LLM matching approach reintroduces a parallel routing path and the attendant threshold-tuning problem. The LLM router already handles argument extraction, chaining, and disambiguation — adding a parallel path would reduce coverage, not improve it.
