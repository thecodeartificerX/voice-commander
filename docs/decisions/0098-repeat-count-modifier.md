# ADR 0098 — Repeat-count modifier ("twice" / "thrice" / "N times")

**Status:** Accepted
**Date:** 2026-05-21

## Context

Users frequently want to fire the same command several times in a row — "go
down" three times to step through a list, "scroll up" twice, etc. Today the only
ways to repeat are saying the command N separate times (slow, and each utterance
pays VAD + transcription latency) or authoring a `chain` (ADR 0085) like
`chain scroll scroll scroll` (verbose, must restate the command per step).

Natural speech already has compact repeat words — "twice", "thrice", and the
open-ended "N times". Supporting them turns "go down twice" into a single
utterance that runs the command 2×.

## Decision

Add a **repeat-count modifier**: a trailing phrase on a normal-mode utterance
that fans the matched command into N sequential executions.

Recognised forms (`src/voice_commander/repeat.py :: parse_repeat_suffix`):

| Spoken | Count |
|---|---|
| `<base> once` | 1 |
| `<base> twice` | 2 |
| `<base> thrice` | 3 |
| `<base> N time` / `<base> N times` | N |

`N` is a number word `one`..`twenty` **or** a digit string (`"4"`). The base
phrase keeps its original casing so `type Hello twice` types `Hello` verbatim.

### Expansion

`expand_repeat(base_plan, count)` mirrors the chain expansion (ADR 0085): the
base plan's steps are concatenated `count` times, separated by a single
synthetic `wait(ms=255)` step flagged `ToolCall.internal=True` between
repetitions. The Dispatcher executes those waits but suppresses their
`tool_fired` events and excludes them from the visible HUD step count — so
`scroll down twice` shows two `scroll` lines, not a wait. `255 ms`
(`REPEAT_SETTLE_MS`) reuses chain's inter-step gap so Windows registers each
repeat as a discrete event instead of coalescing them. `Plan.strict` is
inherited from the base plan.

### Router wiring & precedence

`VerbRouter.route()` runs the modifier **after** the registered full-text
command match and **before** primitive routing:

1. Chain meta-verb intercept (head word `chain`) — unchanged, highest precedence.
2. Registered command / workflow full-text match — unchanged.
3. **Repeat modifier** — strip the suffix, recursively `route()` the base
   phrase, then `expand_repeat()` the result.
4. Primitive verb routing — unchanged.

Placing it after step 2 means a command *literally named* with a trailing count
word (e.g. a user command "go down twice") still wins over the repeat reading.
The base phrase is routed through the **same** `route()`, so repeat works
uniformly over primitives (`scroll down twice`), registered commands
(`go down thrice`), and underscore-named commands (`go_down` ↔ "go down").

### Rejection (→ router miss-chime)

`route()` returns `None` (the daemon then miss-chimes, identical to chain
rejection) when:

- the base phrase does not route (`frobnicate twice`);
- the base resolves to a **synthetic intercept** step — `__picker.open`
  (`focus twice`) or `__dictation.start` (`dictate twice`) — which cannot be
  sensibly repeated;
- the parsed count is outside `[1, MAX_REPEAT]` (`MAX_REPEAT = 50`), guarding
  against a mistranscribed large number firing a runaway burst of real input.

`parse_repeat_suffix` is **non-destructive**: when the suffix is absent, the
base would be empty (`twice` alone), or `<word> time(s)` has no number before it
(`end times`), it returns `None` and `route()` falls through to route the full
original text unchanged — so no existing utterance regresses.

### Punctuation tolerance

Whisper routinely glues commas and periods onto spoken words ("go down, twice",
"scroll, down", "chain, copy paste"). The registered-command path already
normalised these away, but **primitive verb-head matching, subcommand matching,
and the chain-head intercept did not** — an attached comma made the head token
(`"scroll,"`, `"chain,"`) miss its alias and the whole utterance miss-chimed.
This was a long-standing chain papercut surfaced while testing repeat (the repeat
base is re-routed through the same primitive path).

`VerbRouter.route()` now normalises the head token and the subcommand comparison
through the existing `_normalize_spoken` (strips every non-alphanumeric run):

- chain-head check: `_normalize_spoken("chain,")` → `"chain"`;
- primitive head: `_normalize_spoken("scroll,")` → `"scroll"`;
- subcommand: `_normalize_spoken("down,")` → `"down"`.

The **raw tail is left untouched**, so `type Hello, world` still types the comma
and `type Hello, world twice` repeats it verbatim. This fix benefits chain and
plain primitives directly, not just the repeat modifier.

### Scope / limitations

- `chain … twice` is **not** supported: the chain head intercept (step 1) wins
  before the repeat check, and the chain parser rejects the trailing `twice`
  token → miss. Chained-then-repeated is out of scope.
- Multi-word numbers ("twenty one times") are out of scope; only single-token
  number words `one`..`twenty` plus digit strings are parsed.

### Why a module constant, not config

`MAX_REPEAT` and `REPEAT_SETTLE_MS` are module constants in `repeat.py`,
mirroring chain's `INTER_STEP_MS` (ADR 0085) rather than introducing a `[repeat]`
config section. The values are behavioural invariants, not user-tunable knobs,
and the sibling feature set the precedent.

## Consequences

- One spoken utterance repeats any command N times; no per-step restatement, no
  workflow graph, no extra latency per repeat.
- Zero new tools, no new LLM-visible primitive, no config keys, no Dispatcher /
  daemon / sprite changes — the expansion reuses the existing internal-wait
  suppression and miss-chime paths already proven by chain.
- New pure module `voice_commander.repeat` (no I/O, no registry) is unit-tested
  in isolation; `VerbRouter` gains two import symbols and one routing block.

## Validation

- `tests/unit/test_repeat.py` — `parse_repeat_suffix` (recognised forms, casing,
  digits, non-matches, MAX cap), `expand_repeat` (separators, multi-step blocks,
  metadata, strict inheritance, synthetic-step + empty-plan rejection), and
  `VerbRouter.route` integration (primitives, registered commands, underscore
  names, picker/dictation rejection, literal-name precedence, and comma
  tolerance — `scroll, down twice`, `scroll up, thrice`, `type Hello, world
  twice` keeps its comma). `tests/unit/test_verb_router.py` adds head/subcommand
  punctuation cases (`scroll, down`, `click.`, `scroll up,`, `type, Hello,
  world`, `chain, click click`).
- `tests/integration/test_repeat_routing.py` — end-to-end through the **real**
  `Dispatcher`: `scroll down twice` invokes the counting `scroll` tool exactly
  2× (4× for "four times"), `go down thrice` invokes the registered command 3×,
  internal wait separators run but emit no `tool_fired` and are excluded from the
  HUD step count, and `frobnicate twice` does not route. This is the
  evidence-based E2E at the correct layer: the repeat modifier is a routing-time
  transform with no new pixels / focus / audio / IPC surface, so it reuses the
  dispatch + chime + HUD paths already covered by the chain visual story rather
  than adding a sprite/PrintWindow harness.

## References

- Chain meta-verb (sibling expansion + internal-wait convention): ADR 0085
- `ToolCall.internal` HUD suppression: `src/voice_commander/plan.py`,
  `src/voice_commander/dispatcher.py`
- VerbRouter routing precedence: `src/voice_commander/verb_router.py`
