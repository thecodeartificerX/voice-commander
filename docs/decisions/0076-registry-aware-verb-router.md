# ADR 0076 — Registry-Aware VerbRouter Routing

**Status:** Accepted
**Date:** 2026-05-09
**Extends:** ADR 0074 (Merlin-gated verb router)
**Partially supersedes:** ADR 0075 (primitives-only routing claim — VerbRouter now also routes user-authored command/workflow names by registry lookup)

## Context

In the post-0075 daemon, VerbRouter only matched the 7 hardcoded primitive verbs (`click`, `scroll`, `focus X`, `open X`, `type X`, `press X`, `wait N`). User-authored commands — for example, a "copy" command graph built in the Builder UI — had no voice route in normal mode. The user had to first say "Merlin" to flip into LLM mode and then say "copy", which contradicted the expectation that authored commands should fire as immediately and deterministically as primitives.

Symptom: utterance `"copy"` → MISS even though `copy` was registered as a `command`-origin entry in the `ToolRegistry`.

## Decision

`VerbRouter` takes an optional `registry: ToolRegistry` parameter. `route(transcript)` now executes the following ordered resolution:

1. **Normalize.** Strip trailing punctuation, lowercase, and collapse whitespace via `_normalize_spoken()`.
2. **Registry lookup (new).** Attempt an exact match of the normalized transcript against every enabled registry entry where `entry.origin in {command, workflow}`. Both `entry.name` and `entry.phrases` (synonym list) participate. Names stored with underscores (`close_window`) are matched against spoken-with-spaces transcripts (`close window`) by normalizing underscores to spaces on both sides. Punctuation is stripped from both sides so `"Copy."` matches `copy`. **Longest token-count match wins**, so `close_window` beats bare `close` when the full transcript is `close window`.
3. **Primitive fallback.** Fall back to the existing primitive verb rule resolution (`click`, `scroll`, `focus X`, `open X`, `type X`, `press X`, `wait N`).
4. **Miss.** Return `None` only when neither path matches.

When `registry` is not supplied (unit-test or standalone usage), the router behaves exactly as it did under ADR 0075.

## Trade-offs / Alternatives considered

- **Multi-word command names with spaces** — rejected. The store enforces `a-z0-9_` identifiers; the underscore-as-space normalization convention achieves the same spoken-form matching without relaxing identifier rules.
- **Explicit "command group" entity with a default + variant list** — rejected. Adds schema complexity for what is naturally expressed by multi-word names combined with the longest-match rule.
- **Forcing all authored commands behind the Merlin toggle** — rejected. This defeats the purpose of authoring deterministic graphs in the Builder UI, requiring LM Studio to be running even for commands the user has explicitly defined.

## Consequences

- Authored commands fire offline; no LM Studio dependency for any registered command or workflow.
- Whisper transcription noise can be mitigated by registering synonyms on the graph entry (`phrases = ["paste", "P.A.C.T."]`).
- The LLM path is reserved strictly for Merlin mode, keeping the MoE tool list small and tool-call reliability high.
- Primitive verbs still take precedence over same-named registered commands when the registry exact-match fires first (registry lookup runs before the primitive fallback); `command`- and `workflow`-origin entries cannot accidentally shadow a user-intended primitive invocation because the primitive path is tried second and the registry lookup is exact, not fuzzy.

## Implementation pointers

- `src/voice_commander/verb_router.py` — `_match_registered_command()`, `_normalize_spoken()`.
- `src/voice_commander/daemon.py` — `VerbRouter(build_default_rules(), registry=registry)` wiring at line ~801.
- Tests: `tests/unit/test_verb_router.py` (registry-aware coverage).
