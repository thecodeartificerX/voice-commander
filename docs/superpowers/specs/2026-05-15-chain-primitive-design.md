# Chain Primitive — Design

**Date.** 2026-05-15
**Status.** Drafted; awaiting plan-writing.
**Scope.** Add a `chain` router-level meta-verb that lets the user execute multiple commands and nullary primitives in a single utterance, separated only by whitespace, with a fixed 255 ms pause between each step.

---

## 1. Motivation

Today every voice utterance produces exactly one routing decision: one registered command, one primitive verb, or a miss. To run three things in sequence the user must press Scroll Lock, speak, wait for VAD, then repeat — or hand-author a workflow graph for that specific combination.

`chain` collapses the common case ("do A, then B, then C") into one utterance without any prior authoring. It is a router-level construct, not a tool: it produces a multi-step `Plan` from a single transcript and hands it to the existing `Dispatcher`.

## 2. Voice surface

`"chain <tok1> <tok2> ... <tokN>"` — one utterance, no separator word, in one breath (VAD will end the utterance on silence as usual).

Each `<tokN>` is **either**:

1. A registered command or workflow spoken-name, **or its synonym** (multi-word allowed; longest match wins at the cursor — same semantics as `_match_registered_command` in `verb_router.py`).
2. A nullary primitive verb alias with a `default_target` in `build_default_rules()`. Today that set is exactly `click` and `scroll`.

**Forbidden inside chain** (parser rejects the whole utterance, miss-chime). Union of two reasons, deduped:

- `open`, `type`, `press`, `wait`, `focus` — arg-taking and/or picker-triggering when bare.
- `tabs` — always picker-only.
- `chain` itself — no recursion.

Rationale: separator-less parsing has no boundary for arg-taking verbs (where does `type hello world copy` end?); pickers are interactive sub-states that break chain's fire-and-forget shape; recursion is pointless and would let one chain authoring mistake explode into nested state.

**Whisper aliases.** `chain` head also accepts `chained` and `chains` (Whisper renders the spoken word inconsistently in short utterances). These are head-aliases inside the parser, not new verbs.

## 3. Examples

| Utterance | Resolves to |
|---|---|
| `chain copy paste` | `[copy, wait(255), paste]` |
| `chain click click click` | `[click, wait(255), click, wait(255), click]` |
| `chain copy close window save` (with `close_window` registered, spoken `"close window"`) | `[copy, wait(255), close_window, wait(255), save]` |
| `chain open spotify` | rejected — `open` is forbidden |
| `chain copy nonsense paste` | rejected — `nonsense` matches nothing |
| `chain copy` | rejected — fewer than 2 user steps |
| `chain chain copy paste` | rejected — `chain` forbidden inside |

## 4. Architecture

```
StreamingRecorder ─text─▶ VerbRouter.route(text)
                              │
                              ├─ if head in {"chain","chained","chains"}:
                              │     ChainParser.parse(tail)
                              │       └─▶ Plan(steps=[s1, wait*, s2, wait*, ..., sN],
                              │                strict=True,
                              │                raw_response={"router":"chain", ...})
                              │
                              └─ else: existing registered-match → verb-rule logic
```

`wait*` = `ToolCall(name="wait", kwargs={"ms": 255}, internal=True)`.

### New module: `src/voice_commander/chain.py`

```python
from dataclasses import dataclass
from .plan import Plan, ToolCall
from .registry import ToolRegistry
from .verb_router import VerbRule, _normalize_spoken

INTER_STEP_MS = 255  # module constant; intentional, not configurable

_FORBIDDEN = frozenset({"open", "type", "press", "wait", "focus", "tabs", "chain"})
_HEAD_ALIASES = frozenset({"chain", "chained", "chains"})


@dataclass(frozen=True)
class ChainParser:
    registry: ToolRegistry
    verb_rules: tuple[VerbRule, ...]

    def parse(self, tail: str) -> Plan | None:
        """Return a multi-step Plan or None on any parse failure."""
        ...
```

`VerbRouter.__init__` gains an optional `chain_parser: ChainParser | None` parameter. `route()` checks `head in _HEAD_ALIASES` first and delegates. On `None` from the parser, route also returns `None` (existing miss-chime path handles it).

### Modified: `src/voice_commander/plan.py`

Add `internal: bool = False` to `ToolCall`. Default `False` preserves all existing call sites.

### Modified: `src/voice_commander/dispatcher.py`

When a step has `step.internal is True`:

- Still call `tool.func(**kwargs)` (the sleep must run).
- **Skip** `_publish("tool_fired", ...)`.
- **Skip** `FeedbackSink` per-step notifications.
- **Still** create the tracer span (run.db keeps full timing for `vc tail` / `/page/runs` debugging).
- Tool-error path unchanged: if `wait` itself raises (it should not), failure is published as normal.

`FeedbackSink.on_plan_start(transcript, n_steps)` is called with the count of **non-internal** steps so HUD shows a coherent step total.

## 5. Parser algorithm

Input: tail string (everything after the chain head word).

1. `normalized = _normalize_spoken(tail)`. Empty → `None`.
2. `words = normalized.split()`.
3. Build the **candidate table** once per call:
   - Registered commands/workflows: for each enabled `entry` with `origin in ("command","workflow")`, add `(spoken_tokens, ToolCall(entry.name, {}))` for the name and for each `phrase` synonym. Underscores in names are converted to spaces (same `_spoken` helper the main router uses).
   - Nullary primitive verbs: for each `VerbRule` with `default_target is not None` and `name not in _FORBIDDEN`, add `(alias_tokens, ToolCall(default_target.tool, default_target.kwargs))` for every alias.
4. Sort candidates by token count descending so the longest spoken form wins at the cursor. **Tie-break at equal token count: registered command/workflow beats nullary primitive** (mirrors the priority order of the main router, which checks registered names before falling through to verb rules).
5. Walk a cursor through `words`. At each position:
   - Try every candidate in sorted order; the first whose `spoken_tokens == words[cursor:cursor+len]` wins.
   - If hit: append its `ToolCall` to `user_steps`, advance cursor by `len`.
   - If no hit: also check whether the run starting at `cursor` matches a **forbidden** alias / registered name (so the failure can be attributed clearly in logs) — either way return `None`.
6. If `len(user_steps) < 2`: return `None` (a one-step "chain" is meaningless; require the user to mean it).
7. Build `Plan.steps` by interleaving `ToolCall("wait", {"ms": 255}, internal=True)` between user steps. No trailing wait. `strict=True`. `raw_response={"router": "chain", "tokens": [s.name for s in user_steps]}`.

## 6. Execution + feedback

Reuses `Dispatcher.run_plan()` unchanged in shape:

- Each user step fires `tool_fired` → HUD line per step (existing behavior).
- Synthetic `wait` steps are `internal=True` → no HUD line, no chime, but the dispatcher still sleeps 255 ms.
- `strict=True` halts on first failure (unknown tool, raised exception). `plan_outcome.failed_step_index` points at the step. Miss-chime via existing `FeedbackSink.on_error`.
- `transcript` event fires once for the whole chain utterance (ADR 0079 ordering preserved): transcript → tool_fired × N user steps → plan_outcome on failure.
- Tracer: one `plan` root span, one `tool_call` span per step (including internal waits). `runs.db` row visible in `vc tail` and `/page/runs`.
- LLM / graph palette: chain is router-level, not a `ToolEntry`. Not in `/graph/palette`, not authorable in the Builder UI. Mirrors how the existing verb-routing layer is invisible to the graph layer.

## 7. Testing

Per `docs/testing-strategy.md` and the visual-E2E mandate in `docs/agents/visual-e2e-testing.md`.

### Unit — `tests/unit/test_chain_parser.py`

- Happy path: `"copy paste save"` → 3 user steps + 2 internal waits, strict.
- Multi-word command resolved: register `close_window` (spoken `close window`); parse `"copy close window save"` → `[copy, wait*, close_window, wait*, save]`.
- Longest-match wins: register both `close` and `close_window`; parse `"close window"` → resolves to `close_window` single step → rejected by `len<2`.
- Forbidden verb mid-chain: `"copy open paste"` → `None`.
- Unknown token mid-chain: `"copy nonsense paste"` → `None`.
- Empty / single-token tail: `None`.
- Head aliases: `chained` and `chains` resolve identically to `chain`.

### Unit — `tests/unit/test_dispatcher_internal_steps.py`

- `Plan` with one `internal=True` `wait` step + two visible steps: `tool_fired` published only twice; `on_plan_start` called with `n_steps=2`; `time.sleep` invoked once with 0.255 s (monkeypatched).
- Plan-outcome status unaffected by internal-step success/failure path.

### Integration — `tests/integration/test_chain_routing.py`

- Full `VerbRouter.route("chain copy paste")` returns expected `Plan`; fed to real `Dispatcher` with stub tools; verify call order + 255 ms inter-step timing via monkeypatched `time.sleep`.

### Visual E2E — `scripts/chain_visual_e2e.py`

Mandatory per `docs/agents/visual-e2e-testing.md`. Subprocess sprite + SSE consumer. Drive a known chain (e.g. `"chain click click"` with `click` stubbed to a no-op tool registered for the test). Assertions:

- SSE event order: `transcript` → `tool_fired{name=click}` → `tool_fired{name=click}` → `plan_outcome{status=ok}`. **No `tool_fired{name=wait}`** between them.
- PrintWindow screenshot of HUD: only two `click` HUD lines render in the chat-log overlay; no `wait` line.
- Total wall time ≥ 255 ms (one inter-step delay observable).

Pattern after `scripts/picker_visual_e2e.py`.

## 8. Out of scope

- Configurable inter-step delay (settings.toml knob, per-utterance override).
- "Slow chain" / "fast chain" voice modifiers.
- Chains with arg-taking primitives or picker-triggers.
- Recursive chains.
- Sticky chain sub-state (queue-and-run shape). May revisit if separator-less proves frustrating in practice.
- Builder UI authoring of chains. Chain is a routing convenience, not a graph node.

## 9. Doc deliverables (shipped with implementation, not in this spec)

- `docs/decisions/0085-chain-primitive.md` — ADR.
- Append summary row to `docs/agents/technical-decisions.md`.
- Extend the long flow paragraph in `CLAUDE.md` with a chain mention.

## 10. Decisions locked during brainstorming

| Decision | Value | Rationale |
|---|---|---|
| Activation shape | One-shot, separator-less | Tersest; fits one-utterance VAD model |
| Argument-taking verbs in chain | Forbidden | No boundary in separator-less parser |
| Picker-triggers in chain | Forbidden | Pickers are interactive sub-states |
| Error policy | Strict (halt on first failure) | Matches existing `Plan.strict=True` default; avoids cascading state |
| Inter-step delay | 255 ms, hardcoded | Per request; no knob until proven needed |
| Synthetic `wait` steps in HUD | Suppressed via `ToolCall.internal=True` | HUD should show user intent, not parser scaffolding |
| Architecture | Separate `chain.py` module composed into `VerbRouter` | Keeps `verb_router.py` lean; testable in isolation |
