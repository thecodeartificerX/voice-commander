# ADR 0055 — Plan.strict flag for continue-on-error execution

**Status**: Accepted
**Date**: 2026-04-23
**Issue**: #12 (continue-on-error dispatcher behavior)

## Context

Issue #12 requested a way to keep executing remaining plan steps after one step
fails. The primary use case is multi-step compound commands where a non-fatal
intermediate failure (e.g., a window not found) should not abort the rest.

## Decision

Add `strict: bool = True` to the frozen `Plan` dataclass. When `strict=False`,
`Dispatcher.run_plan()` records the first failure but continues to the next
step. Only the first failure is surfaced in `PlanOutcome.failed_step_index`.

## Rationale

- `strict=True` default preserves all existing caller semantics (zero diff for
  current LLM router output, which always emits strict plans).
- The `if failed_index is None` guard keeps `PlanOutcome` SSE wire format
  unchanged — `failed_step_index` remains a single int, not a list, avoiding
  breakage of `voice_sprite` consumers (ADR-0048).
- Teaching the LLM to emit `strict=False` is deferred to a follow-up issue.

## Alternatives Rejected

- **Per-step `on_failure` flag** — more expressive but more complex; rejected as
  over-engineering for the current use case (issue #12 option 2).
- **`Dispatcher`-level config** — would apply globally to all plans; `Plan`-level
  is more precise and keeps the mode in the data, not the executor.
- **`run_plan()` parameter** — would require all callers to pass mode explicitly;
  encoding it on `Plan` lets the LLM signal intent per-utterance in future.

## Consequences

- `LLMRouter` always produces `strict=True` plans for now; teaching the LLM to
  emit `strict=False` is a separate follow-up.
- `failed_step_index` tracks first failure only; multi-failure tracking would
  break the SSE wire format and is out of scope.
