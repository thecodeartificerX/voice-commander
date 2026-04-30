# ADR 0074: Merlin-gated deterministic verb router

## Status
Accepted

## Context
ADR 0040 made all routing LLM-only. In practice, this means every simple command ("copy", "paste", "new tab") pays ~600 ms LLM latency. A deterministic first-word router can handle the common case in ~1 ms, reducing perceived latency and removing the LM Studio dependency for the core command surface.

## Decision
- Normal mode routes through `VerbRouter.route(transcript)`.
- Saying exactly `Merlin` during an active session toggles `_merlin_mode`.
- `_merlin_mode = True` routes non-toggle utterances through `LLMRouter.route()`.
- A new voice session starts with `_merlin_mode = False`.
- Redundant one-shortcut commands (`copy`, `new_tab`, `close_window`, etc.) are removed from the command catalog; they are now handled by the verb router.
- The `rapidfuzz` dependency is retained for parameter resolution (`resolver.py`) and for the verb router's fuzzy matching.

## Supersedes
- ADR 0040: LLM-only routing replaces hybrid

## Consequences
### Positive
- ~1 ms routing for core commands vs ~600 ms LLM path.
- No LM Studio dependency for the common command surface.
- Simpler, deterministic behavior for frequently used commands.

### Negative
- Two routing paths to maintain instead of one.
- Verb router rule table must be kept in sync with the primitive catalog.
- Users must learn the "Merlin" toggle for open-ended LLM requests.
