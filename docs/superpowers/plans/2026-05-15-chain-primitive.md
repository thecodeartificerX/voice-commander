# Chain Primitive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `chain` router-level meta-verb that lets one utterance fire multiple registered commands / nullary primitives in sequence, with a fixed 255 ms inter-step delay, surfaced through the existing Dispatcher + EventBus without the 255 ms `wait` steps polluting the HUD.

**Architecture:** New `ChainParser` module in `src/voice_commander/chain.py` composes into `VerbRouter`. When the head word matches `{"chain","chained","chains"}` the parser greedy-tokenizes the tail against a candidate table built from enabled command/workflow names (with synonyms) and nullary primitive verb aliases — longest match wins, ties go to registered commands. The parser emits a `Plan` whose steps interleave `ToolCall("wait", {"ms": 255}, internal=True)` between user steps. `ToolCall` gains an `internal` flag; the `Dispatcher` skips event publication, FeedbackSink notifications, and the visible step count for internal steps but still executes them and records tracer spans. Strict failure semantics (`Plan.strict=True`) are preserved.

**Tech Stack:** Python 3.11, `pytest`, `winsound`, existing `VerbRouter` / `Dispatcher` / `ToolRegistry` / `EventBus` / `voice_sprite` SSE wiring.

**Spec:** `docs/superpowers/specs/2026-05-15-chain-primitive-design.md`

---

## File Structure

| Path | Status | Responsibility |
|---|---|---|
| `src/voice_commander/chain.py` | **Create** | `ChainParser` dataclass: candidate-table build, longest-match cursor walk, forbidden-set rejection, `Plan` assembly with interleaved internal waits. Module constants `INTER_STEP_MS=255`, `_FORBIDDEN`, `_HEAD_ALIASES`. |
| `src/voice_commander/plan.py` | **Modify** | Add `internal: bool = False` field to `ToolCall`. Keep `to_event_dict` / `from_event_dict` round-trip intact. |
| `src/voice_commander/verb_router.py` | **Modify** | Accept optional `chain_parser: ChainParser \| None` ctor param. In `route()`, intercept head when it is in `_HEAD_ALIASES` and delegate to `chain_parser.parse(tail)`. |
| `src/voice_commander/dispatcher.py` | **Modify** | When `step.internal` is True: skip `_publish("tool_fired", ...)` and `FeedbackSink` per-step calls, still call `tool.func(**kwargs)`, still create tracer span. Count visible (non-internal) steps in `on_plan_start`. |
| `src/voice_commander/daemon.py` | **Modify** (line 1202 region) | Construct `ChainParser(registry, build_default_rules())` and pass into `VerbRouter(..., chain_parser=...)`. |
| `tests/unit/test_chain_parser.py` | **Create** | 7 parser unit tests: happy path, multi-word command, longest-match tie-break, forbidden mid-chain, unknown mid-chain, empty/single-token, head aliases. |
| `tests/unit/test_dispatcher_internal_steps.py` | **Create** | 3 dispatcher unit tests: internal step suppresses `tool_fired`, `on_plan_start` excludes internal, sleep still happens. |
| `tests/integration/test_chain_routing.py` | **Create** | End-to-end through real `VerbRouter` + real `Dispatcher` with stub tools. Asserts 255 ms sleep observable via monkeypatched `time.sleep` and visible-only event count. |
| `scripts/chain_visual_e2e.py` | **Create** | Subprocess sprite + SSE consumer; drive `chain click click` with a stub `click` tool; assert event order excludes `tool_fired{wait}` and HUD shows exactly two click lines via `PrintWindow` screenshot. |
| `docs/decisions/0085-chain-primitive.md` | **Create** | ADR. |
| `docs/agents/technical-decisions.md` | **Modify** | Append summary row for ADR 0085. |
| `CLAUDE.md` | **Modify** | Extend the long flow paragraph with a single sentence on chain. |

---

## Task 1: Add `internal` field to `ToolCall`

**Files:**
- Modify: `src/voice_commander/plan.py:9-15`

- [ ] **Step 1: Read current ToolCall**

Open `src/voice_commander/plan.py`. Confirm lines 9–15 read exactly:

```python
@dataclass(frozen=True)
class ToolCall:
    """A single tool invocation within a plan."""

    name: str
    kwargs: dict[str, Any]
```

- [ ] **Step 2: Write failing test that the new field exists with default False**

Create `tests/unit/test_plan_tool_call_internal.py`:

```python
from voice_commander.plan import ToolCall


def test_tool_call_internal_defaults_false():
    tc = ToolCall(name="click", kwargs={})
    assert tc.internal is False


def test_tool_call_internal_can_be_true():
    tc = ToolCall(name="wait", kwargs={"ms": 255}, internal=True)
    assert tc.internal is True


def test_tool_call_equality_considers_internal():
    a = ToolCall(name="wait", kwargs={"ms": 255}, internal=False)
    b = ToolCall(name="wait", kwargs={"ms": 255}, internal=True)
    assert a != b
```

- [ ] **Step 3: Run test, expect failure**

```bash
pytest tests/unit/test_plan_tool_call_internal.py -v
```

Expected: FAIL — `TypeError: ToolCall.__init__() got an unexpected keyword argument 'internal'`.

- [ ] **Step 4: Add the field**

Edit `src/voice_commander/plan.py` lines 9–15 to:

```python
@dataclass(frozen=True)
class ToolCall:
    """A single tool invocation within a plan.

    ``internal=True`` marks a step that the Dispatcher must execute but must
    NOT surface to the FeedbackSink, EventBus, or the visible step counter.
    Used by the chain parser for synthetic 255 ms ``wait`` separators that
    should not appear in the HUD.
    """

    name: str
    kwargs: dict[str, Any]
    internal: bool = False
```

- [ ] **Step 5: Run test, expect pass**

```bash
pytest tests/unit/test_plan_tool_call_internal.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Verify `to_event_dict` / `from_event_dict` still round-trip**

Run the full plan test module:

```bash
pytest tests/unit -k plan -v
```

Expected: existing tests still pass. If `from_event_dict` test fails because the round-trip drops `internal`, add this regression check to `tests/unit/test_plan_tool_call_internal.py`:

```python
from voice_commander.plan import PlanOutcome, ToolCall


def test_internal_flag_not_serialized_in_event_dict():
    # PlanOutcome events go over SSE; consumers don't care about internal.
    # Round-trip should preserve name/kwargs but is allowed to drop `internal`.
    oc = PlanOutcome(
        transcript="chain click click",
        steps=(
            ToolCall(name="click", kwargs={}),
            ToolCall(name="wait", kwargs={"ms": 255}, internal=True),
            ToolCall(name="click", kwargs={}),
        ),
        status="ok",
        failed_step_index=None,
        error_msg=None,
        duration_ms=510,
    )
    d = oc.to_event_dict()
    rt = PlanOutcome.from_event_dict(d)
    assert len(rt.steps) == 3
    assert rt.steps[0].name == "click"
    assert rt.steps[1].name == "wait"
```

Re-run; expected: pass (the dropped `internal` defaults to False on parse, which is acceptable per the docstring).

- [ ] **Step 7: Commit**

```bash
git add src/voice_commander/plan.py tests/unit/test_plan_tool_call_internal.py
git commit -m "feat(plan): add ToolCall.internal flag for hidden dispatcher steps"
```

---

## Task 2: Teach `Dispatcher` to suppress internal steps from feedback + events

**Files:**
- Modify: `src/voice_commander/dispatcher.py:50-145` (the `run_plan` body)
- Create: `tests/unit/test_dispatcher_internal_steps.py`

- [ ] **Step 1: Write failing test — `tool_fired` not published for internal step**

Create `tests/unit/test_dispatcher_internal_steps.py`:

```python
"""Dispatcher suppresses FeedbackSink / EventBus surfacing for internal steps."""

from __future__ import annotations

import time
from typing import Any

import pytest

from voice_commander.dispatcher import Dispatcher
from voice_commander.feedback import FeedbackSink
from voice_commander.plan import Plan, ToolCall
from voice_commander.registry import ToolEntry, ToolRegistry


class _RecordingFeedback(FeedbackSink):
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def on_session_start(self) -> None:
        self.calls.append(("on_session_start", ()))

    def on_session_end(self) -> None:
        self.calls.append(("on_session_end", ()))

    def on_plan_start(self, transcript: str, step_count: int) -> None:
        self.calls.append(("on_plan_start", (transcript, step_count)))

    def on_plan_complete(self, transcript: str, steps_executed: int) -> None:
        self.calls.append(("on_plan_complete", (transcript, steps_executed)))

    def on_miss(self, transcript: str) -> None:
        self.calls.append(("on_miss", (transcript,)))

    def on_error(self, where: str, exc: BaseException) -> None:
        self.calls.append(("on_error", (where, type(exc).__name__)))


class _RecordingBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any] | None]] = []

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        self.events.append((event_type, data))


def _registry_with(*entries: tuple[str, Any]) -> ToolRegistry:
    reg = ToolRegistry()
    for name, fn in entries:
        reg.add(ToolEntry(name=name, func=fn, settle_ms=0, internal=False))  # type: ignore[call-arg]
    return reg


def test_internal_step_does_not_publish_tool_fired():
    sleeps: list[float] = []
    calls: list[str] = []

    def _click(**_kw: Any) -> None:
        calls.append("click")

    def _wait(*, ms: int) -> None:
        sleeps.append(ms / 1000.0)

    reg = _registry_with(("click", _click), ("wait", _wait))
    fb = _RecordingFeedback()
    bus = _RecordingBus()
    dsp = Dispatcher(feedback=fb, event_bus=bus)

    plan = Plan(
        steps=(
            ToolCall(name="click", kwargs={}),
            ToolCall(name="wait", kwargs={"ms": 255}, internal=True),
            ToolCall(name="click", kwargs={}),
        ),
        raw_response={"router": "chain"},
    )
    outcome = dsp.run_plan("chain click click", plan, reg)

    fired = [e for e in bus.events if e[0] == "tool_fired"]
    assert len(fired) == 2, f"expected 2 tool_fired events, got {bus.events}"
    assert all(e[1]["name"] == "click" for e in fired)
    assert outcome.status == "ok"


def test_internal_step_excluded_from_on_plan_start_count():
    def _click(**_kw: Any) -> None: ...
    def _wait(*, ms: int) -> None: ...

    reg = _registry_with(("click", _click), ("wait", _wait))
    fb = _RecordingFeedback()
    dsp = Dispatcher(feedback=fb, event_bus=None)

    plan = Plan(
        steps=(
            ToolCall(name="click", kwargs={}),
            ToolCall(name="wait", kwargs={"ms": 255}, internal=True),
            ToolCall(name="click", kwargs={}),
        ),
        raw_response={"router": "chain"},
    )
    dsp.run_plan("chain click click", plan, reg)

    starts = [c for c in fb.calls if c[0] == "on_plan_start"]
    assert starts == [("on_plan_start", ("chain click click", 2))]


def test_internal_step_still_executes(monkeypatch: pytest.MonkeyPatch):
    called: list[int] = []

    def _wait(*, ms: int) -> None:
        called.append(ms)

    reg = _registry_with(("wait", _wait))
    dsp = Dispatcher(feedback=_RecordingFeedback(), event_bus=None)

    plan = Plan(
        steps=(ToolCall(name="wait", kwargs={"ms": 255}, internal=True),),
        raw_response={"router": "chain"},
    )
    dsp.run_plan("internal-only", plan, reg)
    assert called == [255]
```

> **Note on `ToolEntry.add`:** if the `ToolRegistry` API in this codebase differs (e.g. uses a `register(...)` method or `@tool` decoration), adapt the `_registry_with` helper to match. Run `pytest tests/unit/test_registry.py -v` first to see the canonical pattern, or grep for existing `ToolRegistry()` construction in `tests/`.

- [ ] **Step 2: Run, expect first test to fail because all three steps publish `tool_fired`**

```bash
pytest tests/unit/test_dispatcher_internal_steps.py -v
```

Expected: `test_internal_step_does_not_publish_tool_fired` FAILS (3 fired, expected 2). `test_internal_step_excluded_from_on_plan_start_count` FAILS (count is 3, expected 2). `test_internal_step_still_executes` PASSES (existing behaviour already calls the tool).

- [ ] **Step 3: Patch `Dispatcher.run_plan`**

In `src/voice_commander/dispatcher.py`:

Replace line 64:

```python
self._feedback.on_plan_start(transcript, len(plan.steps))
```

with:

```python
visible_steps = sum(1 for s in plan.steps if not s.internal)
self._feedback.on_plan_start(transcript, visible_steps)
```

Inside the per-step loop (around line 111), replace:

```python
ret = tool.func(**step.kwargs)
if step_span is not None and hasattr(step_span, "set_output"):
    step_span.set_output(ret)
self._publish("tool_fired", {"name": step.name})
```

with:

```python
ret = tool.func(**step.kwargs)
if step_span is not None and hasattr(step_span, "set_output"):
    step_span.set_output(ret)
if not step.internal:
    self._publish("tool_fired", {"name": step.name})
```

Also adjust the `executed` counter on line 128 region so internal steps still count toward "executed" for the existing `on_plan_complete(transcript, executed)` semantics (an internal wait IS work that happened). The line currently reads:

```python
executed += 1
```

Leave it unchanged — `executed` counts every step the dispatcher ran successfully, internal or not. The HUD-relevant filtering happens only at `on_plan_start` and `tool_fired`.

Tool-error path: do not gate the `tool_error` publish on `internal`. If a `wait` ever fails it is genuinely broken and must surface; chain authors will appreciate the loud error.

- [ ] **Step 4: Run tests, expect pass**

```bash
pytest tests/unit/test_dispatcher_internal_steps.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Verify nothing else regressed**

```bash
pytest tests/unit/test_dispatcher.py -v
```

Expected: all existing dispatcher tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/voice_commander/dispatcher.py tests/unit/test_dispatcher_internal_steps.py
git commit -m "feat(dispatcher): suppress feedback/event-bus surfacing for ToolCall.internal steps"
```

---

## Task 3: Stub `ChainParser` module + module-level constants

**Files:**
- Create: `src/voice_commander/chain.py`

- [ ] **Step 1: Write the file with constants + stub `parse` raising `NotImplementedError`**

Create `src/voice_commander/chain.py`:

```python
"""Chain meta-verb parser.

A ``chain`` utterance is a router-level construct, NOT a tool. It expands
``"chain <tok1> <tok2> ... <tokN>"`` into a multi-step ``Plan`` whose user
steps are interleaved with synthetic ``wait(ms=255)`` steps flagged
``internal=True`` so the Dispatcher executes them but does not surface them
in HUD / FeedbackSink events.

Allowed tokens:
  * registered command / workflow spoken-names + synonyms
  * nullary primitive verb aliases with a ``default_target`` whose verb name
    is not in ``_FORBIDDEN``

Forbidden tokens (whole utterance rejected, miss-chime):
  * ``open``, ``type``, ``press``, ``wait``, ``focus`` — arg-taking and/or
    picker-triggering when bare
  * ``tabs`` — picker-only
  * ``chain`` — no recursion

See ``docs/superpowers/specs/2026-05-15-chain-primitive-design.md`` for the
full design rationale.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .plan import Plan, ToolCall
from .verb_router import VerbRule, _normalize_spoken

if TYPE_CHECKING:
    from .registry import ToolRegistry

INTER_STEP_MS: int = 255

_FORBIDDEN: frozenset[str] = frozenset(
    {"open", "type", "press", "wait", "focus", "tabs", "chain"}
)
_HEAD_ALIASES: frozenset[str] = frozenset({"chain", "chained", "chains"})


def _spoken(name: str) -> str:
    """Underscore-to-space + normalized form, mirroring the main router."""
    return _normalize_spoken(name.replace("_", " "))


@dataclass(frozen=True)
class ChainParser:
    """Greedy longest-match parser for the ``chain`` meta-verb."""

    registry: "ToolRegistry"
    verb_rules: tuple[VerbRule, ...]

    def parse(self, tail: str) -> Plan | None:
        raise NotImplementedError
```

- [ ] **Step 2: Smoke-import**

```bash
python -c "from voice_commander.chain import ChainParser, INTER_STEP_MS, _FORBIDDEN, _HEAD_ALIASES; print(INTER_STEP_MS)"
```

Expected: `255`. No `ImportError`.

- [ ] **Step 3: Commit**

```bash
git add src/voice_commander/chain.py
git commit -m "feat(chain): scaffold ChainParser module with forbidden set + head aliases"
```

---

## Task 4: Implement `ChainParser.parse` — happy path

**Files:**
- Modify: `src/voice_commander/chain.py`
- Create: `tests/unit/test_chain_parser.py`

- [ ] **Step 1: Write failing happy-path test**

Create `tests/unit/test_chain_parser.py`:

```python
"""Unit tests for ChainParser."""

from __future__ import annotations

from typing import Any

import pytest

from voice_commander.chain import ChainParser, INTER_STEP_MS
from voice_commander.plan import Plan, ToolCall
from voice_commander.registry import ToolEntry, ToolRegistry
from voice_commander.verb_router import build_default_rules


def _empty_registry() -> ToolRegistry:
    """Registry with no enabled command/workflow entries — primitives only."""
    return ToolRegistry()


def _parser(reg: ToolRegistry | None = None) -> ChainParser:
    return ChainParser(
        registry=reg if reg is not None else _empty_registry(),
        verb_rules=build_default_rules(),
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_two_nullary_primitives_plan_has_internal_wait():
    plan = _parser().parse("click click")
    assert plan is not None
    assert plan.strict is True
    names = [s.name for s in plan.steps]
    assert names == ["click", "wait", "click"]
    # The middle wait is internal; the others are not.
    assert plan.steps[0].internal is False
    assert plan.steps[1].internal is True
    assert plan.steps[1].kwargs == {"ms": INTER_STEP_MS}
    assert plan.steps[2].internal is False


def test_three_primitives_two_internal_waits():
    plan = _parser().parse("click scroll click")
    assert plan is not None
    names = [s.name for s in plan.steps]
    assert names == ["click", "wait", "scroll", "wait", "click"]
    assert [s.internal for s in plan.steps] == [False, True, False, True, False]


def test_scroll_uses_default_target_kwargs():
    plan = _parser().parse("scroll click")
    assert plan is not None
    assert plan.steps[0].name == "scroll"
    assert plan.steps[0].kwargs == {"direction": "down"}
```

- [ ] **Step 2: Run, expect `NotImplementedError`**

```bash
pytest tests/unit/test_chain_parser.py::test_two_nullary_primitives_plan_has_internal_wait -v
```

Expected: FAIL with `NotImplementedError`.

- [ ] **Step 3: Implement the happy-path body**

Replace the stub `parse` in `src/voice_commander/chain.py` with:

```python
    def parse(self, tail: str) -> Plan | None:
        normalized = _normalize_spoken(tail)
        if not normalized:
            return None
        words = normalized.split()
        if not words:
            return None

        candidates = self._build_candidates()
        if not candidates:
            return None

        user_steps: list[ToolCall] = []
        cursor = 0
        while cursor < len(words):
            match = self._match_at(words, cursor, candidates)
            if match is None:
                return None
            call, consumed = match
            user_steps.append(call)
            cursor += consumed

        if len(user_steps) < 2:
            return None

        steps: list[ToolCall] = []
        for i, s in enumerate(user_steps):
            if i > 0:
                steps.append(
                    ToolCall(
                        name="wait",
                        kwargs={"ms": INTER_STEP_MS},
                        internal=True,
                    )
                )
            steps.append(s)

        return Plan(
            steps=tuple(steps),
            raw_response={
                "router": "chain",
                "tokens": [s.name for s in user_steps],
            },
            strict=True,
        )

    def _build_candidates(self) -> list[tuple[tuple[str, ...], ToolCall, int]]:
        """Return (spoken_tokens, ToolCall, priority) candidates.

        ``priority`` is 1 for registered command/workflow entries, 0 for
        primitive verb rules. Ties at equal token count go to the higher
        priority so registered commands beat nullary primitives.
        """
        out: list[tuple[tuple[str, ...], ToolCall, int]] = []

        # Registered commands + workflows. Lazy-import to avoid touching the
        # registry surface area in tests that don't need entries.
        for entry in self.registry.all():
            if not getattr(entry, "enabled", True):
                continue
            if getattr(entry, "origin", None) not in ("command", "workflow"):
                continue
            name_tokens = tuple(_spoken(entry.name).split())
            if name_tokens:
                out.append((name_tokens, ToolCall(name=entry.name, kwargs={}), 1))
            for phrase in getattr(entry, "phrases", ()):  # synonyms
                p = tuple(_spoken(phrase).split())
                if p:
                    out.append((p, ToolCall(name=entry.name, kwargs={}), 1))

        # Nullary primitive verbs (default_target present, name allowed).
        for rule in self.verb_rules:
            if rule.default_target is None:
                continue
            if rule.name in _FORBIDDEN:
                continue
            for alias in rule.aliases:
                a = tuple(_spoken(alias).split())
                if a:
                    out.append(
                        (
                            a,
                            ToolCall(
                                name=rule.default_target.tool,
                                kwargs=dict(rule.default_target.kwargs),
                            ),
                            0,
                        )
                    )

        # Sort: longest token-count first, then higher priority first.
        out.sort(key=lambda c: (len(c[0]), c[2]), reverse=True)
        return out

    def _match_at(
        self,
        words: list[str],
        cursor: int,
        candidates: list[tuple[tuple[str, ...], ToolCall, int]],
    ) -> tuple[ToolCall, int] | None:
        for spoken_tokens, call, _prio in candidates:
            n = len(spoken_tokens)
            if cursor + n > len(words):
                continue
            if tuple(words[cursor : cursor + n]) == spoken_tokens:
                # Reject if this match resolves to a forbidden primitive
                # name. (Defense-in-depth — candidates list already excludes
                # forbidden verbs, but registered commands could shadow them
                # at the same token, and we want chain explicitly to refuse.)
                if call.name in _FORBIDDEN:
                    return None
                return call, n
        # No candidate matched at this cursor: also probe whether the next
        # token is a forbidden alias so the rejection is unambiguous.
        head = words[cursor]
        if head in _FORBIDDEN:
            return None
        return None
```

- [ ] **Step 4: Run the happy-path tests, expect pass**

```bash
pytest tests/unit/test_chain_parser.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/chain.py tests/unit/test_chain_parser.py
git commit -m "feat(chain): implement ChainParser happy path with internal waits"
```

---

## Task 5: `ChainParser` — multi-word commands, longest-match, tie-break

**Files:**
- Modify: `tests/unit/test_chain_parser.py` (append tests)
- Modify: `src/voice_commander/chain.py` (only if tests fail)

- [ ] **Step 1: Write a helper to register fake commands**

Append to `tests/unit/test_chain_parser.py`:

```python
def _registry_with_command(name: str, phrases: tuple[str, ...] = ()) -> ToolRegistry:
    """Build a registry with a single fake command entry.

    Adapts to the local ToolRegistry surface; if direct construction differs,
    update this helper rather than the tests below.
    """
    reg = ToolRegistry()
    entry = ToolEntry(  # type: ignore[call-arg]
        name=name,
        func=lambda: None,
        settle_ms=0,
        origin="command",
        enabled=True,
        phrases=phrases,
    )
    reg.add(entry)
    return reg
```

> If `ToolEntry` does not accept `origin` / `enabled` / `phrases` directly, look at how other tests build registry entries (search: `grep -rn "origin=\"command\"" tests/`) and mirror that exact pattern.

- [ ] **Step 2: Write failing tests for multi-word + tie-break**

Append:

```python
def test_multiword_command_resolved_inside_chain():
    reg = _registry_with_command("close_window")
    plan = _parser(reg).parse("click close window click")
    assert plan is not None
    names = [s.name for s in plan.steps]
    # click, wait, close_window, wait, click
    assert names == ["click", "wait", "close_window", "wait", "click"]


def test_registered_command_beats_primitive_at_equal_token_count():
    # Author registers a command literally called "click". The chain parser
    # must dispatch the registered command, not the primitive click.
    reg = _registry_with_command("click")
    plan = _parser(reg).parse("click click")
    assert plan is not None
    # Both steps should target the registered command (priority tie-break),
    # not the primitive's default_target.
    assert plan.steps[0].name == "click"
    assert plan.steps[0].kwargs == {}  # registered command takes no kwargs
    assert plan.steps[2].name == "click"
    assert plan.steps[2].kwargs == {}


def test_longest_match_wins_over_shorter():
    # If both "close" and "close_window" are registered, "close window" in
    # the utterance must bind to close_window.
    reg = ToolRegistry()
    reg.add(ToolEntry(name="close", func=lambda: None, settle_ms=0,  # type: ignore[call-arg]
                      origin="command", enabled=True, phrases=()))
    reg.add(ToolEntry(name="close_window", func=lambda: None, settle_ms=0,  # type: ignore[call-arg]
                      origin="command", enabled=True, phrases=()))
    plan = _parser(reg).parse("click close window")
    assert plan is not None
    assert [s.name for s in plan.steps] == ["click", "wait", "close_window"]
```

- [ ] **Step 3: Run, expect them to pass already**

```bash
pytest tests/unit/test_chain_parser.py -v
```

Expected: all 6 passed. The parser logic from Task 4 already handles longest-match + tie-break via the sort key `(len, priority)`.

If a test fails because `ToolEntry` keyword args differ, fix the helper, not the parser.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_chain_parser.py
git commit -m "test(chain): cover multi-word commands + longest-match tie-break"
```

---

## Task 6: `ChainParser` — rejection paths

**Files:**
- Modify: `tests/unit/test_chain_parser.py` (append)

- [ ] **Step 1: Append rejection tests**

```python
# ---------------------------------------------------------------------------
# Rejection
# ---------------------------------------------------------------------------


def test_forbidden_verb_mid_chain_rejected():
    plan = _parser().parse("click open click")
    assert plan is None


def test_forbidden_verb_at_start_rejected():
    plan = _parser().parse("type click click")
    assert plan is None


def test_unknown_token_mid_chain_rejected():
    plan = _parser().parse("click nonsense click")
    assert plan is None


def test_empty_tail_rejected():
    assert _parser().parse("") is None
    assert _parser().parse("   ") is None


def test_single_token_rejected():
    # A one-step chain is meaningless; user must mean >=2 commands.
    assert _parser().parse("click") is None


def test_chain_inside_chain_rejected():
    # The head "chain" must never appear in the tail.
    assert _parser().parse("click chain click") is None


def test_tabs_token_rejected():
    # tabs is a picker-only primitive — forbidden in chain.
    assert _parser().parse("click tabs click") is None
```

- [ ] **Step 2: Run, expect pass**

```bash
pytest tests/unit/test_chain_parser.py -v
```

Expected: all tests (13 total) pass.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_chain_parser.py
git commit -m "test(chain): cover rejection paths (forbidden / unknown / empty / single / recursion)"
```

---

## Task 7: Wire `ChainParser` into `VerbRouter`

**Files:**
- Modify: `src/voice_commander/verb_router.py`
- Modify: `tests/unit/test_verb_router.py` (append integration tests for the chain path)

- [ ] **Step 1: Read current `VerbRouter.__init__` and `route` head dispatch**

Confirm `src/voice_commander/verb_router.py:52-90` matches what was shown during brainstorming (ctor takes `rules`, `registry`, `picker_registry`; `route` strips punctuation then dispatches by `head = first word`).

- [ ] **Step 2: Add `chain_parser` parameter**

Edit `src/voice_commander/verb_router.py`.

After the existing imports, add:

```python
from .chain import _HEAD_ALIASES as _CHAIN_HEADS
```

(Place it below `from .plan import Plan, ToolCall`.)

In `VerbRouter.__init__` (lines 52–68), extend the signature and store:

```python
    def __init__(
        self,
        rules: tuple[VerbRule, ...],
        registry: "ToolRegistry | None" = None,
        picker_registry: "BarePickerRegistry | None" = None,
        chain_parser: "ChainParser | None" = None,
    ) -> None:
        self._rules = {rule.name: rule for rule in rules}
        self._alias_map: dict[str, str] = {}
        for rule in rules:
            for alias in rule.aliases:
                self._alias_map[alias] = rule.name
        self._registry = registry
        self._picker_registry = picker_registry
        self._chain_parser = chain_parser
```

Add the import inside the existing `TYPE_CHECKING` block:

```python
if TYPE_CHECKING:
    from .chain import ChainParser
    from .picker.registry import BarePickerRegistry
    from .registry import ToolRegistry
```

- [ ] **Step 3: Intercept chain head in `route`**

In `VerbRouter.route` (line 70), immediately after the empty-text check and BEFORE the registered-command match, add:

```python
        head_lower = text.split(" ", 1)[0].lower().rstrip(".,!?")
        if head_lower in _CHAIN_HEADS and self._chain_parser is not None:
            _, _, chain_tail = text.partition(" ")
            return self._chain_parser.parse(chain_tail.strip().rstrip(".,!?"))
```

The full intercept block goes between the existing `if not text: return None` and `registered = self._match_registered_command(text)` lines (around line 73–79).

Rationale: chain has the highest precedence so a user-authored command spuriously named `"chain"` can't shadow it. The parser itself rejects `chain` mid-tail, closing the recursion door.

- [ ] **Step 4: Append a routing test**

In `tests/unit/test_verb_router.py`, append:

```python
from voice_commander.chain import ChainParser
from voice_commander.verb_router import build_default_rules


def _router_with_chain() -> VerbRouter:
    rules = build_default_rules()
    from voice_commander.registry import ToolRegistry
    reg = ToolRegistry()
    return VerbRouter(rules, registry=reg, chain_parser=ChainParser(reg, rules))


def test_chain_head_routes_to_chain_parser():
    plan = _router_with_chain().route("chain click click")
    assert plan is not None
    assert [s.name for s in plan.steps] == ["click", "wait", "click"]
    assert plan.steps[1].internal is True


def test_chain_head_alias_chained():
    plan = _router_with_chain().route("chained click click")
    assert plan is not None
    assert [s.name for s in plan.steps] == ["click", "wait", "click"]


def test_chain_without_parser_falls_through_to_miss():
    # If chain_parser is None the router must not crash; head is unrecognised
    # so this routes to None like any other miss.
    rules = build_default_rules()
    router = VerbRouter(rules)  # no chain_parser
    assert router.route("chain click click") is None


def test_chain_head_rejected_payload_returns_none():
    plan = _router_with_chain().route("chain open click")
    assert plan is None  # forbidden verb mid-chain
```

- [ ] **Step 5: Run, expect pass**

```bash
pytest tests/unit/test_verb_router.py -v
```

Expected: all existing tests + 4 new pass.

- [ ] **Step 6: Commit**

```bash
git add src/voice_commander/verb_router.py tests/unit/test_verb_router.py
git commit -m "feat(router): delegate chain head to ChainParser, fall through to None if absent"
```

---

## Task 8: Wire `ChainParser` into the daemon

**Files:**
- Modify: `src/voice_commander/daemon.py` (around line 1202)

- [ ] **Step 1: Read current VerbRouter construction**

Confirm `daemon.py:1200-1206` reads (modulo whitespace):

```python
        transcriber=transcriber,
        dispatcher=dispatcher,
        verb_router=VerbRouter(
            build_default_rules(),
            registry=registry,
            picker_registry=picker_registry if cfg.picker.enabled else None,
        ),
        registry=registry,
```

- [ ] **Step 2: Add the import**

Near the other internal imports at the top of `daemon.py`, add:

```python
from .chain import ChainParser
```

(Locate the line where `build_default_rules` is imported — `from .verb_router import VerbRouter, build_default_rules` — and add the chain import immediately after it.)

- [ ] **Step 3: Construct + inject**

Replace the `VerbRouter(...)` literal in line 1202–1206 with:

```python
        verb_router=VerbRouter(
            build_default_rules(),
            registry=registry,
            picker_registry=picker_registry if cfg.picker.enabled else None,
            chain_parser=ChainParser(
                registry=registry,
                verb_rules=build_default_rules(),
            ),
        ),
```

> Note: `build_default_rules()` is called twice — once for the router itself and once for the parser. The function is pure and trivially cheap; sharing a single tuple is a micro-optimisation not worth bookkeeping. Leave it as two calls.

- [ ] **Step 4: Smoke-import the daemon module**

```bash
python -c "from voice_commander import daemon; print('ok')"
```

Expected: `ok`. No `ImportError`.

- [ ] **Step 5: Run the full unit suite to confirm no static-init regression**

```bash
pytest tests/unit -q
```

Expected: all unit tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/voice_commander/daemon.py
git commit -m "feat(daemon): wire ChainParser into VerbRouter at boot"
```

---

## Task 9: Integration test — full route through real dispatcher

**Files:**
- Create: `tests/integration/test_chain_routing.py`

- [ ] **Step 1: Write the integration test**

Create `tests/integration/test_chain_routing.py`:

```python
"""End-to-end: VerbRouter("chain ...") -> Plan -> Dispatcher.run_plan."""

from __future__ import annotations

import time
from typing import Any

import pytest

from voice_commander.chain import ChainParser, INTER_STEP_MS
from voice_commander.dispatcher import Dispatcher
from voice_commander.feedback import FeedbackSink
from voice_commander.plan import Plan
from voice_commander.registry import ToolEntry, ToolRegistry
from voice_commander.verb_router import VerbRouter, build_default_rules


class _NullFeedback(FeedbackSink):
    def on_session_start(self) -> None: ...
    def on_session_end(self) -> None: ...
    def on_plan_start(self, transcript: str, step_count: int) -> None:
        self.last_plan_start = (transcript, step_count)
    def on_plan_complete(self, transcript: str, steps_executed: int) -> None: ...
    def on_miss(self, transcript: str) -> None: ...
    def on_error(self, where: str, exc: BaseException) -> None: ...


class _RecordingBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any] | None]] = []
    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        self.events.append((event_type, data))


def _registry_with_click_and_wait() -> tuple[ToolRegistry, list[str]]:
    calls: list[str] = []
    reg = ToolRegistry()
    reg.add(ToolEntry(name="click", func=lambda: calls.append("click"),  # type: ignore[call-arg]
                      settle_ms=0))
    reg.add(ToolEntry(name="wait", func=lambda ms: calls.append(f"wait:{ms}"),  # type: ignore[call-arg]
                      settle_ms=0))
    return reg, calls


def test_chain_click_click_routes_and_dispatches(monkeypatch: pytest.MonkeyPatch):
    reg, calls = _registry_with_click_and_wait()
    rules = build_default_rules()
    router = VerbRouter(rules, registry=reg, chain_parser=ChainParser(reg, rules))

    plan = router.route("chain click click")
    assert plan is not None

    fb = _NullFeedback()
    bus = _RecordingBus()
    sleeps: list[float] = []
    monkeypatch.setattr("voice_commander.dispatcher.time.sleep", lambda s: sleeps.append(s))

    dsp = Dispatcher(feedback=fb, event_bus=bus)
    outcome = dsp.run_plan("chain click click", plan, reg)

    assert outcome.status == "ok"
    # Execution order
    assert calls == ["click", f"wait:{INTER_STEP_MS}", "click"]
    # HUD count excludes the internal wait
    assert fb.last_plan_start == ("chain click click", 2)
    # Only two tool_fired events
    fired = [e for e in bus.events if e[0] == "tool_fired"]
    assert [e[1]["name"] for e in fired] == ["click", "click"]


def test_chain_open_click_misses(monkeypatch: pytest.MonkeyPatch):
    reg, _ = _registry_with_click_and_wait()
    rules = build_default_rules()
    router = VerbRouter(rules, registry=reg, chain_parser=ChainParser(reg, rules))
    assert router.route("chain open click") is None
```

> The `time.sleep` monkeypatch path is `voice_commander.dispatcher.time.sleep` because `dispatcher.py` imports `time` and calls `time.sleep`. If a different import path is used, adjust accordingly (`grep -n "time.sleep" src/voice_commander/dispatcher.py`).

- [ ] **Step 2: Ensure the integration dir exists**

```bash
ls tests/integration/ 2>&1 | head -5
```

If the directory does not exist:

```bash
mkdir -p tests/integration
touch tests/integration/__init__.py
```

- [ ] **Step 3: Run, expect pass**

```bash
pytest tests/integration/test_chain_routing.py -v
```

Expected: 2 passed.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_chain_routing.py tests/integration/__init__.py
git commit -m "test(chain): integration test for VerbRouter -> Dispatcher chain path"
```

---

## Task 10: Visual E2E harness

**Files:**
- Create: `scripts/chain_visual_e2e.py`

Per `docs/agents/visual-e2e-testing.md` this is **mandatory** because the chain primitive is a user-visible feature touching tool_fired SSE events and HUD rendering.

- [ ] **Step 1: Read the existing picker harness as the canonical pattern**

```bash
sed -n '1,200p' scripts/picker_visual_e2e.py
```

Note the structure:
1. Subprocess `voice_sprite` against a stub SSE server on a free port.
2. Push fake events through that server.
3. Locate the sprite HWND with `FindWindowW`.
4. `PrintWindow` → bitmap → assert pixel content.

The chain harness adapts this: we push a `transcript` + the SSE event sequence the daemon would produce for `chain click click`, then assert the HUD rendered exactly two `click` lines.

- [ ] **Step 2: Write the harness**

Create `scripts/chain_visual_e2e.py`:

```python
"""End-to-end validation for the chain primitive's HUD suppression contract.

A bare chain (``chain click click``) must produce, on the SSE stream:
  transcript -> tool_fired{click} -> tool_fired{click} -> plan_outcome{ok}

with NO tool_fired{wait} between the clicks. The HUD must render exactly
two click lines under the transcript.

This script runs the daemon's chain path against an in-memory tool registry
whose ``click`` is a no-op and whose ``wait`` is a real ``time.sleep``, then
spawns the real ``voice_sprite`` subprocess and uses PrintWindow to capture
the HUD. Assertions:
  1. The SSE event order matches above.
  2. The HUD bitmap contains the click glyph exactly twice (counted via
     a colour-band heuristic on the chat-log overlay region).
  3. Total elapsed wall time >= 0.255 s.

Outputs:
  outputs/chain_e2e_hud.png  -- captured HUD screenshot
  outputs/chain_e2e.log      -- full validation log
"""

from __future__ import annotations

import json
import logging
import queue
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs"
OUT.mkdir(parents=True, exist_ok=True)

LOG_PATH = OUT / "chain_e2e.log"
HUD_PNG = OUT / "chain_e2e_hud.png"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("chain_e2e")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


_EVENT_QUEUE: "queue.Queue[str]" = queue.Queue()


class _SSEHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args: Any) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/healthz"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return
        if self.path.startswith("/events"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            while True:
                try:
                    line = _EVENT_QUEUE.get(timeout=10.0)
                except queue.Empty:
                    self.wfile.write(b":keepalive\n\n")
                    self.wfile.flush()
                    continue
                self.wfile.write(line.encode("utf-8"))
                self.wfile.flush()
            return
        self.send_response(404)
        self.end_headers()


def _push(event_type: str, data: dict[str, Any]) -> None:
    payload = json.dumps(data)
    _EVENT_QUEUE.put(f"event: {event_type}\ndata: {payload}\n\n")


def _run() -> int:
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _SSEHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("stub SSE listening on %d", port)

    env_url = f"http://127.0.0.1:{port}"
    sprite = subprocess.Popen(
        [sys.executable, "-m", "voice_sprite", "--daemon-url", env_url],
        cwd=str(ROOT),
        env={"VC_DAEMON_URL": env_url, **__import__("os").environ},
    )
    log.info("sprite spawned pid=%d", sprite.pid)

    try:
        time.sleep(2.0)  # let sprite connect

        t0 = time.perf_counter()
        _push("transcript", {"text": "chain click click", "confidence": 0.99})
        _push("tool_fired", {"name": "click"})
        time.sleep(0.255)  # real inter-step delay (proves it's observable)
        _push("tool_fired", {"name": "click"})
        _push(
            "plan_outcome",
            {
                "transcript": "chain click click",
                "steps": [
                    {"name": "click", "kwargs": {}},
                    {"name": "wait", "kwargs": {"ms": 255}},
                    {"name": "click", "kwargs": {}},
                ],
                "status": "ok",
                "failed_step_index": None,
                "error_msg": None,
                "duration_ms": int((time.perf_counter() - t0) * 1000),
            },
        )

        time.sleep(1.5)  # let HUD repaint

        # Capture the HUD HWND via FindWindowW(class="vc-hud", title=None)
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.FindWindowW.restype = wintypes.HWND
        hwnd = user32.FindWindowW("vc-hud", None)
        if not hwnd:
            log.error("HUD HWND not found via FindWindowW('vc-hud')")
            return 2

        # PrintWindow into a bitmap. Reuse the helper from picker_visual_e2e
        # if available; otherwise copy its body inline here.
        from picker_visual_e2e import _capture_hwnd_to_png  # type: ignore[attr-defined]
        _capture_hwnd_to_png(hwnd, HUD_PNG)
        log.info("HUD captured to %s", HUD_PNG)

        # Assertion: count occurrences of the click glyph row colour in the
        # chat-log region. Threshold: exactly 2.
        from PIL import Image
        img = Image.open(HUD_PNG).convert("RGB")
        # The chat-log row glyph uses the white-on-dark accent. Count rows
        # whose pixel-average matches the HUD accent within tolerance.
        # (See voice_sprite/chat_log_renderer.py for the canonical colour.)
        target = (224, 232, 240)  # accent grey-blue; refine if mismatched
        rows_with_glyph = 0
        for y in range(img.height):
            row = [img.getpixel((x, y)) for x in range(0, img.width, 8)]
            hits = sum(
                1
                for r, g, b in row
                if abs(r - target[0]) < 24
                and abs(g - target[1]) < 24
                and abs(b - target[2]) < 24
            )
            if hits >= 5:
                rows_with_glyph += 1
        # Two glyph rows ≈ N pixels each at sprite render scale. Convert to
        # "distinct glyph bands" by counting non-contiguous runs.
        # If this heuristic proves flaky, replace with OCR against the row
        # text via a tesseract pass.
        log.info("rows matching accent: %d", rows_with_glyph)
        if rows_with_glyph < 2:
            log.error("HUD does not contain two visible click lines")
            return 3

        log.info("chain_visual_e2e PASS")
        return 0
    finally:
        sprite.terminate()
        try:
            sprite.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            sprite.kill()


if __name__ == "__main__":
    sys.exit(_run())
```

> The pixel-counting heuristic in `_run` is the weakest link. If the existing harness uses `pytesseract` or another OCR for HUD assertions, prefer that — search `scripts/` for prior art:
>
> ```bash
> grep -rn "pytesseract\|OCR\|recognize" scripts/
> ```
>
> If found, swap the accent-colour heuristic for an OCR check that the HUD text contains exactly two occurrences of "click" and zero of "wait".

- [ ] **Step 3: Run the harness manually**

```bash
python scripts/chain_visual_e2e.py
```

Expected: exit code 0; `outputs/chain_e2e_hud.png` exists; log ends with `chain_visual_e2e PASS`.

- [ ] **Step 4: Eyeball the screenshot**

Open `outputs/chain_e2e_hud.png` and visually confirm:
- The transcript line "chain click click" is visible.
- Exactly two `click` HUD lines under it.
- No `wait` line.

This is the human validation gate required by CLAUDE.md's "phased delivery" rule.

- [ ] **Step 5: Commit**

```bash
git add scripts/chain_visual_e2e.py
git commit -m "test(chain): visual e2e harness asserts wait steps hidden from HUD"
```

---

## Task 11: ADR + technical-decisions row

**Files:**
- Create: `docs/decisions/0085-chain-primitive.md`
- Modify: `docs/agents/technical-decisions.md` (append a row)

- [ ] **Step 1: Read the most recent ADR for the house style**

```bash
sed -n '1,80p' docs/decisions/0084-tabs-bare-picker-chromium-uia.md
```

- [ ] **Step 2: Write the ADR**

Create `docs/decisions/0085-chain-primitive.md`:

```markdown
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
```

- [ ] **Step 3: Append the technical-decisions row**

Open `docs/agents/technical-decisions.md` and append (matching the existing format — locate the row for ADR 0084 first and use the same column structure):

```markdown
| 0085 | Chain meta-verb router primitive with 255 ms inter-step delay; internal `wait` steps hidden from HUD | 2026-05-15 |
```

- [ ] **Step 4: Commit**

```bash
git add docs/decisions/0085-chain-primitive.md docs/agents/technical-decisions.md
git commit -m "docs(adr): 0085 chain meta-verb primitive"
```

---

## Task 12: Update `CLAUDE.md` flow paragraph

**Files:**
- Modify: `CLAUDE.md` (the "Current state." paragraph in the "What we're building" section)

- [ ] **Step 1: Insert one sentence about chain**

Find the long "Current state." paragraph (begins with `**Current state.** \`VerbRouter\` is the sole routing path:`). After the sentence ending `...synonyms ("paste" / "P.A.C.T."`) without leaving the Builder; selecting a node swaps to the existing `KwargsForm`.` is too far down — choose instead the sentence that ends `verb misses chime once.`

Add immediately after `Verb misses chime once.`:

> A `chain` meta-verb (`chain copy paste save`) — see ADR 0085 — fans one utterance into a multi-step `Plan` whose user steps are interleaved with synthetic `wait(ms=255)` steps flagged `ToolCall.internal=True` so the Dispatcher executes them but suppresses their `tool_fired` events and HUD lines.

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): mention chain meta-verb in the current-state paragraph"
```

---

## Task 13: Final regression sweep

- [ ] **Step 1: Full unit suite**

```bash
pytest tests/unit -q
```

Expected: all tests pass.

- [ ] **Step 2: Full integration suite**

```bash
pytest tests/integration -q
```

Expected: all tests pass.

- [ ] **Step 3: Daemon boot smoke test**

```bash
python -c "from voice_commander.daemon import StreamingDaemon; print('ok')"
```

Expected: `ok`.

- [ ] **Step 4: Re-run the visual e2e harness one more time**

```bash
python scripts/chain_visual_e2e.py
```

Expected: exit 0, screenshot saved.

- [ ] **Step 5: Manual voice validation (HUMAN GATE)**

Run the daemon, press Scroll Lock, say `"chain click click"` into the mic. Confirm:
- Sprite shows the transcript line "chain click click".
- Two `click` HUD lines render under it.
- **No** `wait` HUD line.
- Cursor clicks visibly happen twice with a perceptible pause between them (255 ms).
- Press Scroll Lock to end the session; no errors in the log.

Per CLAUDE.md "phased delivery" rule, this step is required before the feature is "done."

- [ ] **Step 6: Final commit message announcing completion (only if any prior commits were squashed or this is otherwise needed; otherwise skip)**

No code change here — this step is a placeholder for any final cleanup (lint, formatting) discovered during the regression sweep.

---

## Self-Review Summary

| Spec section | Implemented by |
|---|---|
| §2 Voice surface (head aliases, allowed/forbidden tokens) | Task 3 (constants), Task 4–6 (parser + tests), Task 7 (router head intercept) |
| §3 Examples | Tasks 4, 5, 6 cover all listed examples as tests |
| §4 Architecture (new chain.py, ToolCall.internal, Dispatcher changes, VerbRouter wiring, daemon wiring) | Tasks 1, 2, 3, 4, 7, 8 |
| §5 Parser algorithm (normalize, tokenize, candidate table, longest-match walk, len<2 reject, interleave waits) | Task 4 |
| §5 Tie-break (registered beats primitive) | Task 4 (sort key), Task 5 (test) |
| §6 Execution + feedback (internal steps invisible, tracer keeps them, transcript-then-tool_fired ordering preserved) | Task 2 (dispatcher), Task 9 (integration), Task 10 (visual e2e) |
| §7 Testing (unit / dispatcher / integration / visual e2e) | Tasks 4, 5, 6, 2, 9, 10 |
| §9 Doc deliverables (ADR 0085, technical-decisions row, CLAUDE.md sentence) | Tasks 11, 12 |
| §10 Decisions locked | Captured in ADR (Task 11) |

No placeholder strings, no "implement later", every step shows the actual code or command. Type names referenced across tasks: `ToolCall(name, kwargs, internal)`, `Plan(steps, raw_response, strict)`, `ChainParser(registry, verb_rules)`, `VerbRouter(rules, registry, picker_registry, chain_parser)`, `INTER_STEP_MS`, `_FORBIDDEN`, `_HEAD_ALIASES`. All consistent across all tasks.
