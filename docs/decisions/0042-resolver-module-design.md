# ADR 0042: Resolver Module Design

**Status:** Accepted
**Date:** 2026-04-21

## Context

With the `Matcher` removed (ADR 0040, ADR 0041), the daemon needs a clean entry point for routing transcripts to tools. Before the removal, `StreamingDaemon._process_utterance()` contained the routing logic directly: call `Matcher.match()`, check the score, optionally call `LLMRouter.route()`, and hand off to `Dispatcher`. That inline logic made the routing decision invisible at a glance and coupled the daemon to the router's HTTP/LLM details.

With a single routing path (LLM only), the opportunity exists to extract this into a dedicated, narrow module. The daemon should call one method and receive a `Plan | None` — it should not know whether that involves HTTP, fuzzy matching, or anything else.

## Decision

Introduce `src/voice_commander/resolver.py` with a `Resolver` class:

```python
class Resolver:
    def __init__(self, router: LLMRouter, feedback: FeedbackSink) -> None: ...
    def resolve(self, transcript: str) -> Plan | None: ...
```

**Behaviour of `resolve()`:**

1. Call `self._router.route(transcript)`.
2. If the result is `None`, call `self._feedback.on_miss(transcript, [])` and return `None`.
3. If the result is a `Plan`, return it.

**Ownership boundaries:**

- `Resolver` owns the routing decision and miss signalling.
- `LLMRouter` owns the HTTP interaction with LM Studio.
- `Dispatcher` owns plan execution.
- `StreamingDaemon._process_utterance()` calls `resolver.resolve(text)` as its sole routing entry point.

## Consequences

### Positive

- Clean separation of concerns: the daemon's pipeline worker is a simple chain — `transcribe → resolve → dispatch` — with no inline routing logic.
- `Resolver` is independently unit-testable: inject a mock `LLMRouter` and `FeedbackSink`, call `resolve()`, assert return value and feedback calls.
- Future routing strategies (e.g. a faster embedding-based pre-filter, cached responses) can be introduced by changing `Resolver` internals without touching `StreamingDaemon`.
- The miss path is guaranteed to fire: `Resolver.resolve()` is the single place where `on_miss()` is called for routing misses, eliminating the risk of a miss escaping without feedback.

### Negative

- One more file in the subsystem graph. Minimal overhead — the class is small (< 30 lines).

### Neutral

- `Resolver` does not implement retry logic. Transient LM Studio failures surface as a miss chime, consistent with the existing behaviour for timeouts and connection errors.

## Alternatives considered

### Keep routing logic inline in `StreamingDaemon`
Rejected. As routing evolves (caching, pre-filters, logging enrichment), inline logic becomes unwieldy. The daemon's job is orchestration, not routing policy.

### Make `LLMRouter` call `on_miss()` directly
Rejected. Coupling `LLMRouter` to `FeedbackSink` violates the subsystem boundary. `LLMRouter`'s contract is HTTP-in, `Plan | None`-out. Feedback is a concern of the routing layer above it.

### Combine `Resolver` and `Dispatcher` into a single `Router` class
Rejected. `Dispatcher` handles plan execution, `settle_ms` timing, and error feedback. Merging routing and execution into one class conflates two distinct responsibilities with different change drivers.
