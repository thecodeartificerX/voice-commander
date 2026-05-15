# ADR 0085 — Chain Meta-Verb Primitive

**Status:** Accepted
**Date:** 2026-05-15
**Spec:** `docs/superpowers/specs/2026-05-15-chain-primitive-design.md`

## Context

Every voice utterance produces exactly one routing decision today: a
registered command, a primitive verb, or a miss. Running three things in
sequence required either three Scroll Lock cycles or a hand-authored
workflow graph. Users want a way to fire several short commands in one
breath without authoring anything.

## Decision

Add a router-level meta-verb `chain` (with Whisper aliases `chained` and
`chains`). Activation shape: one-shot, separator-less.

`chain <tok1> <tok2> ... <tokN>` expands into a multi-step `Plan` whose
user steps are interleaved with synthetic `wait(ms=255)` steps marked
`ToolCall.internal=True`. The `Dispatcher` executes internal steps but
suppresses their `tool_fired` events, `FeedbackSink` per-step calls, and
visible step count so the HUD shows only user intent.

- **D1.** Allowed tokens: registered command/workflow spoken-names (with
  synonyms) and nullary primitive verb aliases with a `default_target`.
- **D2.** Forbidden tokens (whole utterance rejected, miss-chime):
  `open`, `type`, `press`, `wait`, `focus`, `tabs`, `chain`.
- **D3.** Longest-match wins at each cursor; tie-break at equal token
  count goes to registered commands over primitives.
- **D4.** Strict failure semantics — `Plan.strict=True`. Halt on first
  failure; `plan_outcome.failed_step_index` reports which step.
- **D5.** Inter-step delay is fixed at 255 ms (`chain.INTER_STEP_MS`).
  Not configurable. Revisit only if proven needed.
- **D6.** `chain` is router-level, not a `ToolEntry`. Invisible to
  `/graph/palette` and the Builder UI.

## Consequences

- New module `voice_commander.chain` (parser + constants).
- `ToolCall` gains `internal: bool = False`.
- `Dispatcher` gains a one-line check on `step.internal` before publishing
  `tool_fired` and before calling `FeedbackSink.on_plan_start`. Tracer
  spans still record internal steps so `vc tail` / `/page/runs` preserve
  full timing for debugging.
- `VerbRouter` accepts an optional `chain_parser` and delegates when the
  head word matches `{chain,chained,chains}`. The chain head has the
  highest router precedence so a user-authored command named `chain`
  cannot shadow it.
- `daemon.py` constructs the parser at boot and injects it.

## Alternatives considered

1. **Sticky chain sub-state** — say `chain`, then queue commands one
   utterance at a time, end with `go`. Rejected: separator-less in one
   breath matches the spec's terseness goal; sub-state adds a second
   picker-like state machine for marginal benefit.
2. **Delimiter word** (`chain copy then paste`) — adds parser surface
   and forces an English word inside what is otherwise just a sequence.
3. **Inline in `VerbRouter`** — rejected; `verb_router.py` already does
   several jobs and chain's longest-match walker is large enough to want
   its own file.

## Out of scope

- Configurable inter-step delay (settings.toml knob).
- Per-utterance speed modifiers ("chain fast …").
- Arg-taking primitives inside chain.
- Recursive chains.
