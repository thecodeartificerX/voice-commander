# Merlin-Gated Verb Router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace unconditional LLM routing with a deterministic normal-mode verb router, while preserving the existing LLM router behind a session-scoped spoken `Merlin` toggle.

**Architecture:** Add a dedicated `VerbRouter` module that converts first-word verb matches into ordinary `Plan` objects targeting existing primitives and curated commands/workflows. `StreamingDaemon` becomes a two-branch router: normal mode uses `VerbRouter.route()`, Merlin mode uses `LLMRouter.route()`, and the spoken transcript `Merlin` toggles the session flag without changing dispatcher semantics.

**Tech Stack:** Python 3.11, `rapidfuzz`, existing `Plan`/`ToolCall`/`Dispatcher`, graph command store JSON, pytest

---

## File structure

**Create**
- `src/voice_commander/verb_router.py` — explicit normal-mode routing policy (`RouteTarget`, `SubcommandRule`, `VerbRule`, `VerbRouter`, default rule table)
- `tests/unit/test_verb_router.py` — unit tests for tokenization, first-word matching, subcommands, defaults, raw-tail fallback, and miss cases
- `tests/integration/test_merlin_router_integration.py` — daemon-level integration tests for normal-mode routing vs Merlin-mode LLM routing
- `docs/decisions/0074-merlin-gated-verb-router.md` — ADR superseding ADR 0040’s “LLM-only for every utterance” choice

**Modify**
- `src/voice_commander/daemon.py` — inject `VerbRouter`, add `_merlin_mode`, spoken toggle handling, route branching, session-reset semantics
- `src/voice_commander/config.py` — add a small `[router]` config section for top-level/subcommand thresholds if kept configurable
- `config.toml` — add `[router]` defaults if config-backed thresholds are implemented
- `src/voice_commander/web/templates/guide.html` — replace “every utterance hits LLM” copy with normal-mode/Merlin-mode behavior
- `src/voice_sprite/summary_rules.py` — summarize primitive plans produced by the verb router (`ctrl+c` → “copied”, `ctrl+t` → “opened new tab”, etc.)
- `tests/unit/test_gates.py` — replace unconditional `llm_router.route()` expectations with branch-aware expectations
- `tests/unit/test_streaming_daemon.py` — add Merlin toggle / session reset / miss-path assertions at `_process_utterance()` level
- `tests/unit/test_summary_rules.py` — cover new primitive-chain summaries
- `commands.default.json` — delete redundant built-in one-shortcut commands now represented by verb routing
- `commands.json` — keep the tracked canonical command store aligned with the pruned default catalog
- `docs/agents/technical-decisions.md` — add new routing-strategy row, mark ADR 0040 superseded
- `docs/architecture.md` — update pipeline flow, daemon contract, and routing section
- `README.md` — explain deterministic normal mode, Merlin mode, and pruned built-in command surface

---

### Task 1: Lock the docs-first architecture change

**Files:**
- Create: `docs/decisions/0074-merlin-gated-verb-router.md`
- Modify: `docs/agents/technical-decisions.md`
- Modify: `docs/architecture.md`
- Modify: `README.md`
- Modify: `src/voice_commander/web/templates/guide.html`

- [ ] **Step 1: Write the ADR and doc assertions first**

```markdown
# ADR 0074: Merlin-gated deterministic verb router

## Status
Accepted

## Decision
- Normal mode routes through `VerbRouter.route(transcript)`.
- Saying exactly `Merlin` during an active session toggles `_merlin_mode`.
- `_merlin_mode = True` routes non-toggle utterances through `LLMRouter.route()`.
- A new voice session starts with `_merlin_mode = False`.
- Redundant one-shortcut commands (`copy`, `new_tab`, `close_window`, etc.) are removed from the command catalog.

## Supersedes
- ADR 0040: LLM-only routing replaces hybrid
```

- [ ] **Step 2: Save the docs and verify the superseding references fail before code exists**

Run: `pytest tests/unit/test_gates.py tests/unit/test_streaming_daemon.py -k merlin -v`

Expected: `0 selected` or missing-test failures, confirming the Merlin-specific coverage does not exist yet.

- [ ] **Step 3: Update the architecture and README text to the new routing model**

```markdown
# docs/architecture.md snippet
Transcriber ──text──▶ gates ──▶ spoken Merlin toggle? ──▶ VerbRouter | LLMRouter ──▶ Dispatcher

# README.md snippet
Default routing is deterministic: the first spoken word selects a verb such as `copy`, `new`, `close`, `open`, `focus`, or `type`. Say `Merlin` to temporarily hand subsequent utterances to the LLM router until you say `Merlin` again or end the session.
```

- [ ] **Step 4: Update the web guide page copy**

```html
<p>
  Normal mode uses a deterministic verb router. Merlin mode is a session-scoped
  spoken toggle that re-enables <code>LLMRouter.route()</code> for open-ended requests.
</p>
```

- [ ] **Step 5: Commit**

```bash
git add docs/decisions/0074-merlin-gated-verb-router.md docs/agents/technical-decisions.md docs/architecture.md README.md src/voice_commander/web/templates/guide.html
git commit -m "docs: record merlin-gated verb router design"
```

### Task 2: Build the standalone deterministic `VerbRouter`

**Files:**
- Create: `src/voice_commander/verb_router.py`
- Create: `tests/unit/test_verb_router.py`

- [ ] **Step 1: Write failing unit tests for verb routing behavior**

```python
from voice_commander.plan import ToolCall
from voice_commander.verb_router import VerbRouter, build_default_rules


def test_copy_routes_to_ctrl_c():
    router = VerbRouter(build_default_rules())
    plan = router.route("copy")
    assert plan is not None
    assert plan.steps == (ToolCall(name="press", kwargs={"combo": "ctrl+c"}),)


def test_close_window_prefers_subcommand():
    router = VerbRouter(build_default_rules())
    plan = router.route("close window")
    assert plan.steps == (ToolCall(name="press", kwargs={"combo": "alt+f4"}),)


def test_type_passes_raw_tail():
    router = VerbRouter(build_default_rules())
    plan = router.route("type hello world")
    assert plan.steps == (ToolCall(name="type", kwargs={"text": "hello world"}),)


def test_open_without_tail_misses():
    router = VerbRouter(build_default_rules())
    assert router.route("open") is None
```

- [ ] **Step 2: Run the unit tests to verify they fail**

Run: `pytest tests/unit/test_verb_router.py -v`

Expected: `ModuleNotFoundError: No module named 'voice_commander.verb_router'`

- [ ] **Step 3: Implement the minimal router module**

```python
from dataclasses import dataclass
from rapidfuzz import fuzz

from .plan import Plan, ToolCall


@dataclass(frozen=True)
class RouteTarget:
    tool: str
    kwargs: dict[str, object]


@dataclass(frozen=True)
class SubcommandRule:
    aliases: tuple[str, ...]
    target: RouteTarget


@dataclass(frozen=True)
class VerbRule:
    name: str
    aliases: tuple[str, ...]
    default_target: RouteTarget | None = None
    subcommands: tuple[SubcommandRule, ...] = ()
    raw_tail_tool: str | None = None
    raw_tail_arg: str | None = None
    head_threshold: int = 92
    tail_threshold: int = 88
```

```python
class VerbRouter:
    def __init__(self, rules: tuple[VerbRule, ...]) -> None:
        self._rules = rules

    def route(self, transcript: str) -> Plan | None:
        text = transcript.strip()
        if not text:
            return None
        head, _, tail = text.partition(" ")
        verb = self._match_verb(head)
        if verb is None:
            return None
        tail = tail.strip()
        if not tail and verb.default_target is not None:
            return self._plan_for(verb.default_target)
        sub = self._match_subcommand(verb, tail)
        if sub is not None:
            return self._plan_for(sub.target)
        if tail and verb.raw_tail_tool and verb.raw_tail_arg:
            return Plan(
                steps=(ToolCall(name=verb.raw_tail_tool, kwargs={verb.raw_tail_arg: tail}),),
                raw_response={"router": "verb", "verb": verb.name, "tail": tail},
            )
        return None
```

- [ ] **Step 4: Add the default rule table for the spoken surface in the spec**

```python
def build_default_rules() -> tuple[VerbRule, ...]:
    return (
        VerbRule("copy", ("copy",), default_target=RouteTarget("press", {"combo": "ctrl+c"})),
        VerbRule("cut", ("cut",), default_target=RouteTarget("press", {"combo": "ctrl+x"})),
        VerbRule("paste", ("paste",), default_target=RouteTarget("press", {"combo": "ctrl+v"})),
        VerbRule("undo", ("undo",), default_target=RouteTarget("press", {"combo": "ctrl+z"})),
        VerbRule("redo", ("redo",), default_target=RouteTarget("press", {"combo": "ctrl+y"})),
        VerbRule("save", ("save",), default_target=RouteTarget("press", {"combo": "ctrl+s"})),
        VerbRule("refresh", ("refresh", "reload"), default_target=RouteTarget("press", {"combo": "ctrl+r"})),
        VerbRule("minimize", ("minimize",), default_target=RouteTarget("press", {"combo": "win+down"})),
        VerbRule("maximize", ("maximize",), default_target=RouteTarget("press", {"combo": "win+up"})),
        VerbRule("close", ("close",), default_target=RouteTarget("press", {"combo": "ctrl+w"}), subcommands=(SubcommandRule(("window",), RouteTarget("press", {"combo": "alt+f4"})),)),
        VerbRule("new", ("new",), subcommands=(SubcommandRule(("tab",), RouteTarget("press", {"combo": "ctrl+t"})), SubcommandRule(("window",), RouteTarget("press", {"combo": "ctrl+n"})))),
        VerbRule("type", ("type",), raw_tail_tool="type", raw_tail_arg="text"),
        VerbRule("open", ("open",), raw_tail_tool="open", raw_tail_arg="target"),
        VerbRule("focus", ("focus",), raw_tail_tool="focus", raw_tail_arg="target"),
    )
```

- [ ] **Step 5: Run the router unit tests and make them pass**

Run: `pytest tests/unit/test_verb_router.py -v`

Expected: `PASS` for copy/default/subcommand/raw-tail/miss tests.

- [ ] **Step 6: Commit**

```bash
git add src/voice_commander/verb_router.py tests/unit/test_verb_router.py
git commit -m "feat: add deterministic verb router"
```

### Task 3: Wire `VerbRouter` into `StreamingDaemon` and add Merlin mode

**Files:**
- Modify: `src/voice_commander/daemon.py`
- Modify: `src/voice_commander/config.py`
- Modify: `config.toml`
- Modify: `tests/unit/test_gates.py`
- Modify: `tests/unit/test_streaming_daemon.py`
- Test: `tests/integration/test_merlin_router_integration.py`

- [ ] **Step 1: Write failing daemon tests for the new branch logic**

```python
def test_normal_mode_uses_verb_router(tmp_path):
    daemon, _, _, llm_router, dispatcher = _make_daemon_with_router(tmp_path)
    daemon._verb_router.route.return_value = Plan(
        steps=(ToolCall(name="press", kwargs={"combo": "ctrl+c"}),),
        raw_response={"router": "verb"},
    )

    daemon._process_utterance(_fake_utterance("copy"))

    daemon._verb_router.route.assert_called_once_with("copy")
    llm_router.route.assert_not_called()
    dispatcher.run_plan.assert_called_once()


def test_merlin_toggle_turns_llm_mode_on_and_off(tmp_path):
    daemon, _, _, llm_router, dispatcher = _make_daemon_with_router(tmp_path)

    daemon._process_utterance(_fake_utterance("Merlin"))
    assert daemon._merlin_mode is True

    daemon._process_utterance(_fake_utterance("open spotify"))
    llm_router.route.assert_called_once_with("open spotify")

    daemon._process_utterance(_fake_utterance("Merlin"))
    assert daemon._merlin_mode is False
```

- [ ] **Step 2: Run the targeted daemon tests to verify they fail**

Run: `pytest tests/unit/test_gates.py tests/unit/test_streaming_daemon.py -k "merlin or verb_router" -v`

Expected: failing assertions because `StreamingDaemon` still calls `LLMRouter.route()` unconditionally.

- [ ] **Step 3: Extend `StreamingDaemon` state and constructor wiring**

```python
from .verb_router import VerbRouter, build_default_rules


class StreamingDaemon:
    def __init__(
        self,
        feedback: FeedbackSink,
        recorder: StreamingRecorder | None,
        transcriber: TranscriberProtocol,
        llm_router: LLMRouter,
        dispatcher: Dispatcher,
        *,
        verb_router: VerbRouter,
        registry: ToolRegistry | None = None,
        ...
    ) -> None:
        self._verb_router = verb_router
        self._merlin_mode = False
```

```python
# session open / close reset
self._session_active = True
self._speak_mode = False
self._merlin_mode = False
```

- [ ] **Step 4: Add spoken Merlin toggle detection before routing**

```python
def _is_merlin_toggle(self, transcript: str) -> bool:
    text_norm = transcript.strip().lower().rstrip(".,!?")
    return text_norm == "merlin"
```

```python
if self._is_merlin_toggle(result.text):
    self._merlin_mode = not self._merlin_mode
    logger.info("Merlin mode %s", "entered" if self._merlin_mode else "exited")
    return

if self._merlin_mode:
    plan = self._llm_router.route(result.text)
else:
    plan = self._verb_router.route(result.text)
```

- [ ] **Step 5: Add optional router config only if thresholds are not hardcoded**

```python
@dataclass(frozen=True)
class RouterConfig:
    verb_threshold: int = 92
    subcommand_threshold: int = 88


@dataclass(frozen=True)
class Config:
    ...
    router: RouterConfig = field(default_factory=RouterConfig)
```

```toml
[router]
verb_threshold = 92
subcommand_threshold = 88
```

- [ ] **Step 6: Run the daemon unit tests and make them pass**

Run: `pytest tests/unit/test_gates.py tests/unit/test_streaming_daemon.py -v`

Expected: `PASS`, including Merlin toggle tests, session reset tests, normal-mode miss path, and LLM-only Merlin branch tests.

- [ ] **Step 7: Add end-to-end daemon integration coverage**

```python
def test_merlin_session_routes_copy_then_llm_then_copy_again(tmp_path):
    daemon, llm_router, verb_router, dispatcher = build_test_daemon(tmp_path)
    verb_router.route.side_effect = [
        Plan((ToolCall("press", {"combo": "ctrl+c"}),), {"router": "verb"}),
        Plan((ToolCall("press", {"combo": "ctrl+c"}),), {"router": "verb"}),
    ]
    llm_router.route.return_value = Plan(
        (ToolCall("open", {"target": "spotify"}),),
        {"router": "llm"},
    )

    daemon._process_utterance(_fake_utterance("copy"))
    daemon._process_utterance(_fake_utterance("Merlin"))
    daemon._process_utterance(_fake_utterance("open spotify"))
    daemon._process_utterance(_fake_utterance("Merlin"))
    daemon._process_utterance(_fake_utterance("copy"))
```

- [ ] **Step 8: Commit**

```bash
git add src/voice_commander/daemon.py src/voice_commander/config.py config.toml tests/unit/test_gates.py tests/unit/test_streaming_daemon.py tests/integration/test_merlin_router_integration.py
git commit -m "feat: gate llm routing behind merlin mode"
```

### Task 4: Prune the redundant command catalog and keep summaries readable

**Files:**
- Modify: `commands.default.json`
- Modify: `commands.json`
- Modify: `src/voice_sprite/summary_rules.py`
- Modify: `tests/unit/test_summary_rules.py`

- [ ] **Step 1: Write failing summary tests for primitive plans that replace deleted commands**

```python
def test_press_ctrl_c_reads_as_copied():
    out = _outcome((ToolCall("press", {"combo": "ctrl+c"}),))
    assert RULES["press"]({"combo": "ctrl+c"}, out) == "copied"


def test_press_ctrl_t_reads_as_opened_new_tab():
    out = _outcome((ToolCall("press", {"combo": "ctrl+t"}),))
    assert RULES["press"]({"combo": "ctrl+t"}, out) == "opened new tab"
```

- [ ] **Step 2: Run the summary tests to verify they fail**

Run: `pytest tests/unit/test_summary_rules.py -v`

Expected: failures because `press` still renders generic `pressed ctrl+t` strings.

- [ ] **Step 3: Prune the redundant built-in commands from both tracked graph stores**

```json
{
  "graphs": {
    "gmail": { "name": "gmail" },
    "facebook": { "name": "facebook" },
    "search_web": { "name": "search_web" }
  }
}
```

Delete entries named:
- `new_tab`
- `new_window`
- `close_tab`
- `close_window`
- `next_tab`
- `previous_tab`
- `copy`
- `cut`
- `paste`
- `undo`
- `redo`
- `save`
- `refresh`
- `minimize`
- `maximize`

- [ ] **Step 4: Make the HUD summarize primitive shortcuts naturally**

```python
PRESS_SUMMARIES = {
    "ctrl+c": "copied",
    "ctrl+x": "cut",
    "ctrl+v": "pasted",
    "ctrl+z": "undone",
    "ctrl+y": "redone",
    "ctrl+s": "saved",
    "ctrl+r": "refreshed",
    "ctrl+t": "opened new tab",
    "ctrl+n": "opened new window",
    "ctrl+w": "closed tab",
    "alt+f4": "closed window",
    "win+down": "minimized window",
    "win+up": "maximized window",
}

"press": lambda kw, _o: PRESS_SUMMARIES.get(str(kw.get("combo", "")).lower(), f"pressed {kw.get('combo', '')}".rstrip())
```

- [ ] **Step 5: Run the summary tests and targeted catalog assertions**

Run: `pytest tests/unit/test_summary_rules.py tests/integration/test_merlin_router_integration.py -k "summary or catalog" -v`

Expected: `PASS`, including “deleted commands absent from catalog” assertions.

- [ ] **Step 6: Commit**

```bash
git add commands.default.json commands.json src/voice_sprite/summary_rules.py tests/unit/test_summary_rules.py
git commit -m "refactor: replace shortcut commands with verb-router behavior"
```

### Task 5: Final regression pass across router, daemon, and docs

**Files:**
- Test: `tests/unit/test_verb_router.py`
- Test: `tests/unit/test_gates.py`
- Test: `tests/unit/test_streaming_daemon.py`
- Test: `tests/unit/test_summary_rules.py`
- Test: `tests/integration/test_merlin_router_integration.py`

- [ ] **Step 1: Run the focused regression suite**

Run: `pytest tests/unit/test_verb_router.py tests/unit/test_gates.py tests/unit/test_streaming_daemon.py tests/unit/test_summary_rules.py tests/integration/test_merlin_router_integration.py -v`

Expected: all tests `PASS`.

- [ ] **Step 2: Run the broader project checks touched by the routing change**

Run: `pytest tests/unit/test_llm_router.py tests/unit/test_daemon_tracing.py tests/unit/test_validator.py tests/integration/test_streaming_pipeline.py -v`

Expected: all tests `PASS`; no regressions in LLM-only routing internals, tracing, validator behavior, or pipeline integration.

- [ ] **Step 3: Sanity-check the pruned command stores**

Run: `python - <<'PY'
import json
for path in ('commands.json', 'commands.default.json'):
    data = json.load(open(path, encoding='utf-8'))
    forbidden = {'new_tab','new_window','close_tab','close_window','next_tab','previous_tab','copy','cut','paste','undo','redo','save','refresh','minimize','maximize'}
    found = forbidden & set(data['graphs'])
    print(path, 'OK' if not found else sorted(found))
PY`

Expected:
- `commands.json OK`
- `commands.default.json OK`

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "test: cover merlin-gated verb routing end to end"
```

---

## Self-review

**Spec coverage:**
- Session-level Merlin mode: Task 3
- Deterministic first-word routing: Task 2
- Default/subcommand/raw-tail verb model: Task 2
- Dispatcher/Plan contract unchanged: Tasks 2–3
- Catalog pruning: Task 4
- Unit + integration coverage: Tasks 2–5
- ADR / architecture / technical decisions / user docs updates: Task 1

**Placeholder scan:** No `TODO`/`TBD` placeholders remain.

**Type consistency:** Plan uses one router contract consistently: `VerbRouter.route(transcript: str) -> Plan | None`.
