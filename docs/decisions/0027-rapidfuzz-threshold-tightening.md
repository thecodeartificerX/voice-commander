# ADR 0027: Rapidfuzz Threshold Tightening — 85 → 95 When LLM Router Is Enabled

**Status:** Superseded by ADR-0040
**Date:** 2026-04-21

## Context

The Phase 4 implementation ships with `[matching].threshold = 85`. At that value, rapidfuzz accepts matches with up to ~15 % character-distance noise. This is a deliberate choice for the rapidfuzz-only router: a few false accepts are better than many misses when there is no fallback.

With the LLM router enabled (ADR 0026), the balance shifts. Low-confidence rapidfuzz matches that previously required a miss chime can now escalate to the LLM. A score of 87 against "new tab" when the user said "new tab for that" is a wrong dispatch, not a near-miss that deserves a miss chime — and with a fallback available, it should go to the LLM instead.

Keeping the threshold at 85 with the LLM enabled creates two risks:

1. **False hot-path dispatches.** Borderline matches (85–94) are ambiguous; some are correct, some are wrong. Dispatching wrong tools silently is worse than escalating to the LLM.
2. **LLM never exercised on natural commands.** If the threshold is too loose, the LLM rarely sees the utterances it is designed for — natural language and argument-bearing phrases — because rapidfuzz grabs them first.

Raising the threshold to 95 means only very-high-confidence matches stay on the hot path. Everything else goes to the LLM, which can correctly route "copy that text" and "focus the browser and open a new tab" rather than mis-matching them to single-word commands.

## Decision

Document a recommended threshold of 95 for deployments with `[llm_router].enabled = true`. The threshold is not changed in code or in `config.toml` automatically — the user sets it explicitly. The spec, README, and `config.toml` comments note the recommended pairing.

The existing `[matching].threshold` key is the single knob that controls both paths. No new config key is introduced. When `llm_router.enabled = false` (default), the threshold recommendation of 85 from Phase 4 remains appropriate and should not be changed.

## Consequences

### Positive

- Fewer wrong hot-path dispatches. The LLM handles the ambiguous range (85–94).
- LLM sees the natural-language utterances it is designed for.
- No code change required — the behaviour follows from a config value the user sets.
- Users who want aggressive hot-path coverage can still lower the threshold; the recommendation is not enforced.

### Negative

- If a user enables the LLM router but forgets to raise the threshold, they get the same hot-path behaviour as before. This is a misconfiguration risk. Mitigation: warn in structured logs at startup if `llm_router.enabled = true` and `threshold < 90`.
- A threshold of 95 means more LLM calls, which increases average latency for users with slow hardware or a loaded GPU.

### Neutral

- The threshold is user-tunable. Teams with large phrase lists and high phrase-density may need to test what value works for their vocabulary.

## Alternatives considered

### Auto-raise threshold when LLM router is enabled
Rejected. Silently changing a config knob the user set is surprising. A startup log warning plus documentation is less intrusive and keeps the user in control.

### Introduce a separate `[llm_router].escalation_threshold`
Rejected. Two thresholds on the same routing decision are confusing. The spec explicitly rules out a second threshold (§Architecture, Matcher row).

## References

- Spec §Architecture (Matcher row), §Config — `docs/superpowers/specs/2026-04-21-llm-router-design.md`
- ADR 0026 — hybrid routing design; explains why the split point matters
- ADR 0005 — original rapidfuzz threshold decision
