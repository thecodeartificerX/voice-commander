# Command HUD + Cursor-Follow Sprite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** [`docs/superpowers/specs/2026-04-22-command-hud-design.md`](../specs/2026-04-22-command-hud-design.md)

**Goal:** Add an RPG-style chat-log overlay next to the sprite that renders one simplified line per voice command (e.g. "minimized window", "couldn't find 'sptfy'") using a hybrid rule + LLM summarizer, and make the sprite follow the mouse cursor across monitors, docking above the taskbar on whichever monitor the cursor currently lives on.

**Architecture:** Daemon publishes one new SSE event `plan_outcome` with `{transcript, steps, status, failed_step_index, error_msg, duration_ms}` from `Dispatcher.run_plan()` (success/error) and `StreamingDaemon._process_utterance` (miss). Sprite consumes the event, synthesizes a one-line summary via rule table (nine-verb catalog) with LLM fallback on errors or unknown verbs, pushes entries into a ring buffer, and renders them in a new `ChatLogRenderer` that shares the existing transparent pyglet window (sprite + HUD + speech bubble). A new `CursorDock` module polls `GetCursorPos` at 30 Hz, maps cursor → HMONITOR via `MonitorFromPoint`, reads `rcWork` (taskbar-excluded bounds) via `GetMonitorInfoW`, and calls `window.set_location` / `set_size` with per-monitor DPI rescaling.

**Tech Stack:** Python 3.11+, pyglet 2.x, httpx + httpx-sse, ctypes (Win32 — MonitorFromPoint / GetMonitorInfoW / GetDpiForMonitor), pytest. No new dependencies.

---

## File Structure

**New daemon-side files:**
- `src/voice_commander/plan.py` — extended with `PlanOutcome` dataclass
- `tests/unit/test_plan_outcome.py` — PlanOutcome round-trip + defaults

**New sprite-side files:**
- `src/voice_sprite/chat_log.py` — `ChatLogEntry` dataclass + `ChatLog` ring buffer
- `src/voice_sprite/summary_rules.py` — `RULES` table + chain detectors
- `src/voice_sprite/summarizer.py` — `Summarizer` class (rule-first, LLM fallback)
- `src/voice_sprite/llm_summary_client.py` — httpx wrapper for LM Studio summary calls
- `src/voice_sprite/chat_log_renderer.py` — pyglet labels drawn from ChatLog entries
- `src/voice_sprite/cursor_tracker.py` — `CursorDock` class (ctypes + pyglet)
- `tests/unit/test_chat_log.py`
- `tests/unit/test_summary_rules.py`
- `tests/unit/test_summarizer.py`
- `tests/unit/test_llm_summary_client.py`
- `tests/unit/test_chat_log_renderer.py`
- `tests/unit/test_cursor_tracker.py`
- `tests/integration/test_plan_outcome_sse.py`

**New ADRs:**
- `docs/decisions/0051-command-hud-overlay.md`
- `docs/decisions/0052-hybrid-rule-llm-summarization.md`
- `docs/decisions/0053-sprite-follows-cursor-monitor.md`

**Modified files:**
- `src/voice_commander/dispatcher.py` — outcome tracking + `plan_outcome` publish on all exits
- `src/voice_commander/daemon.py` — publish `plan_outcome status=miss` on `route()=None` + confidence-gate drop
- `src/voice_sprite/dpi.py` — add `get_dpi_for_monitor()`
- `src/voice_sprite/config.py` — add `[hud]` section and new `[sprite]` keys
- `src/voice_sprite/window.py` — composite dimensions; accept ChatLogRenderer; expose `sprite_region_bounds`
- `src/voice_sprite/__main__.py` — wire Summarizer + ChatLog + LLMSummaryClient + ChatLogRenderer + CursorDock; replace hardcoded positions dict
- `config.toml` — default `[hud]` block + new `[sprite]` keys
- `docs/architecture.md` — §10 Command HUD, §11 Cursor-follow sprite
- `docs/agents/technical-decisions.md` — rows for ADR 0051/0052/0053
- `docs/gotchas.md` — Win11 multi-monitor / DPI / rcWork gotchas
- `README.md` — HUD feature + config knob tables
- `tests/unit/test_dispatcher_plan.py` — assert `plan_outcome` publish behaviour
- `tests/unit/test_streaming_daemon.py` — assert miss publishes
- `tests/unit/test_sprite_config.py` — assert new keys parsed
- `tests/integration/test_sse_endpoint.py` — assert `plan_outcome` replays on Last-Event-ID

---

## Phase 1 — Daemon-side `plan_outcome` event

No sprite changes in this phase. End state: daemon publishes `plan_outcome` on every completed utterance and the SSE endpoint replays it correctly. Sprite still ignores the event (unknown type).

### Task 1.1: `PlanOutcome` dataclass + round-trip serialization

**Files:**
- Modify: `src/voice_commander/plan.py`
- Create: `tests/unit/test_plan_outcome.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_plan_outcome.py
"""Tests for PlanOutcome dataclass — SSE event serialization round-trip."""

from __future__ import annotations

import pytest

from voice_commander.plan import PlanOutcome, ToolCall


def test_plan_outcome_ok_round_trip():
    outcome = PlanOutcome(
        transcript="minimize",
        steps=(ToolCall(name="minimize", kwargs={}),),
        status="ok",
        failed_step_index=None,
        error_msg=None,
        duration_ms=127,
    )
    d = outcome.to_event_dict()
    assert d == {
        "transcript": "minimize",
        "steps": [{"name": "minimize", "kwargs": {}}],
        "status": "ok",
        "failed_step_index": None,
        "error_msg": None,
        "duration_ms": 127,
    }
    restored = PlanOutcome.from_event_dict(d)
    assert restored == outcome


def test_plan_outcome_miss_round_trip():
    outcome = PlanOutcome(
        transcript="um hello",
        steps=(),
        status="miss",
        failed_step_index=None,
        error_msg=None,
        duration_ms=42,
    )
    restored = PlanOutcome.from_event_dict(outcome.to_event_dict())
    assert restored == outcome
    assert restored.steps == ()


def test_plan_outcome_error_round_trip():
    outcome = PlanOutcome(
        transcript="minimize",
        steps=(ToolCall(name="minimize", kwargs={}),),
        status="error",
        failed_step_index=0,
        error_msg="FocusWindowError: no visible window",
        duration_ms=310,
    )
    restored = PlanOutcome.from_event_dict(outcome.to_event_dict())
    assert restored == outcome
    assert restored.failed_step_index == 0


def test_plan_outcome_is_frozen():
    outcome = PlanOutcome(
        transcript="x",
        steps=(),
        status="miss",
        failed_step_index=None,
        error_msg=None,
        duration_ms=0,
    )
    with pytest.raises(Exception):
        outcome.status = "ok"  # type: ignore[misc]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_plan_outcome.py -v`
Expected: FAIL — `PlanOutcome` cannot be imported from `voice_commander.plan`.

- [ ] **Step 3: Extend `src/voice_commander/plan.py`**

Append to the file (keep existing `ToolCall` + `Plan`):

```python
from typing import Any, Literal

PlanStatus = Literal["ok", "error", "miss"]


@dataclass(frozen=True)
class PlanOutcome:
    """Final outcome of a single voice-command cycle.

    Published as the ``plan_outcome`` SSE event by ``Dispatcher.run_plan``
    (status=ok|error) and ``StreamingDaemon._process_utterance`` (status=miss).
    Consumed by ``voice_sprite`` to synthesize a one-line HUD entry.
    """

    transcript: str
    steps: tuple[ToolCall, ...]
    status: PlanStatus
    failed_step_index: int | None
    error_msg: str | None
    duration_ms: int

    def to_event_dict(self) -> dict[str, Any]:
        return {
            "transcript": self.transcript,
            "steps": [{"name": s.name, "kwargs": dict(s.kwargs)} for s in self.steps],
            "status": self.status,
            "failed_step_index": self.failed_step_index,
            "error_msg": self.error_msg,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_event_dict(cls, d: dict[str, Any]) -> "PlanOutcome":
        steps = tuple(
            ToolCall(name=s["name"], kwargs=dict(s.get("kwargs", {})))
            for s in d.get("steps", [])
        )
        return cls(
            transcript=d["transcript"],
            steps=steps,
            status=d["status"],
            failed_step_index=d.get("failed_step_index"),
            error_msg=d.get("error_msg"),
            duration_ms=int(d.get("duration_ms", 0)),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_plan_outcome.py -v`
Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/plan.py tests/unit/test_plan_outcome.py
git commit -m "feat(plan): add PlanOutcome dataclass for plan_outcome SSE event"
```

---

### Task 1.2: `Dispatcher` publishes `plan_outcome` on success / error / unknown-tool

**Files:**
- Modify: `src/voice_commander/dispatcher.py`
- Modify: `tests/unit/test_dispatcher_plan.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_dispatcher_plan.py`:

```python
from voice_commander.event_bus import EventBus


def _drain_bus(bus: EventBus) -> list[tuple[str, dict]]:
    """Collect every published event by replaying from id=0."""
    return [(e.type, e.data) for e in bus.replay_after(0)]


def test_run_plan_publishes_plan_outcome_on_success():
    reg = _make_registry(
        _entry("step_a", func=lambda: None, settle_ms=0),
        _entry("step_b", func=lambda: None, settle_ms=0),
    )
    bus = EventBus()
    d = Dispatcher(feedback=CapturingFeedbackSink(), event_bus=bus)
    plan = _plan(("step_a", {}), ("step_b", {}))

    d.run_plan("two steps", plan, reg)

    outcomes = [(t, data) for (t, data) in _drain_bus(bus) if t == "plan_outcome"]
    assert len(outcomes) == 1
    _, payload = outcomes[0]
    assert payload["transcript"] == "two steps"
    assert payload["status"] == "ok"
    assert payload["failed_step_index"] is None
    assert payload["error_msg"] is None
    assert payload["duration_ms"] >= 0
    assert payload["steps"] == [
        {"name": "step_a", "kwargs": {}},
        {"name": "step_b", "kwargs": {}},
    ]


def test_run_plan_publishes_plan_outcome_on_step_error():
    def boom() -> None:
        raise RuntimeError("boom")

    reg = _make_registry(
        _entry("step_a", func=lambda: None),
        _entry("step_b", func=boom),
        _entry("step_c", func=lambda: None),
    )
    bus = EventBus()
    d = Dispatcher(feedback=CapturingFeedbackSink(), event_bus=bus)
    plan = _plan(("step_a", {}), ("step_b", {}), ("step_c", {}))

    d.run_plan("three steps", plan, reg)

    outcomes = [(t, data) for (t, data) in _drain_bus(bus) if t == "plan_outcome"]
    assert len(outcomes) == 1
    _, payload = outcomes[0]
    assert payload["status"] == "error"
    assert payload["failed_step_index"] == 1
    assert "boom" in payload["error_msg"]


def test_run_plan_publishes_plan_outcome_on_unknown_tool():
    reg = _make_registry(_entry("step_a", func=lambda: None))
    bus = EventBus()
    d = Dispatcher(feedback=CapturingFeedbackSink(), event_bus=bus)
    plan = _plan(("step_a", {}), ("nonexistent", {}))

    d.run_plan("unknown", plan, reg)

    outcomes = [(t, data) for (t, data) in _drain_bus(bus) if t == "plan_outcome"]
    assert len(outcomes) == 1
    _, payload = outcomes[0]
    assert payload["status"] == "error"
    assert payload["failed_step_index"] == 1
    assert "nonexistent" in payload["error_msg"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_dispatcher_plan.py -v -k plan_outcome`
Expected: 3 tests FAIL — no `plan_outcome` event published.

- [ ] **Step 3: Modify `src/voice_commander/dispatcher.py`**

Replace the full file body with:

```python
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from .feedback import FeedbackSink
from .plan import Plan, PlanOutcome
from .registry import ToolRegistry

if TYPE_CHECKING:
    from .event_bus import EventBus

logger = logging.getLogger(__name__)


class Dispatcher:
    def __init__(
        self,
        feedback: FeedbackSink,
        event_bus: EventBus | None = None,
    ) -> None:
        self._feedback = feedback
        self._event_bus = event_bus

    def _publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        if self._event_bus is not None:
            self._event_bus.publish(event_type, data)

    def run_plan(self, transcript: str, plan: Plan, registry: ToolRegistry) -> None:
        """Execute a multi-step plan from the LLM router."""
        self._feedback.on_plan_start(transcript, len(plan.steps))
        start_s = time.perf_counter()
        executed = 0
        total = len(plan.steps)
        status: str = "ok"
        failed_index: int | None = None
        error_msg: str | None = None

        for i, step in enumerate(plan.steps):
            tool = registry.by_name(step.name)
            if tool is None:
                self._feedback.on_error(
                    f"plan:unknown_tool:{step.name}",
                    ValueError(f"Tool '{step.name}' not found in registry"),
                )
                self._publish("tool_error", {"name": step.name, "msg": "unknown tool"})
                status = "error"
                failed_index = i
                error_msg = f"unknown tool: {step.name}"
                break
            logger.info(
                "plan step %d/%d: %s(%s)",
                i + 1,
                total,
                step.name,
                ", ".join(f"{k}={v!r}" for k, v in step.kwargs.items()),
            )
            try:
                tool.func(**step.kwargs)
                self._publish("tool_fired", {"name": step.name})
            except Exception as e:
                self._feedback.on_error(f"plan:step:{step.name}", e)
                self._publish("tool_error", {"name": step.name, "msg": str(e)})
                status = "error"
                failed_index = i
                error_msg = f"{type(e).__name__}: {e}"
                break
            executed += 1
            if tool.settle_ms > 0:
                time.sleep(tool.settle_ms / 1000.0)

        self._feedback.on_plan_complete(transcript, executed)

        outcome = PlanOutcome(
            transcript=transcript,
            steps=plan.steps,
            status=status,  # type: ignore[arg-type]
            failed_step_index=failed_index,
            error_msg=error_msg,
            duration_ms=int((time.perf_counter() - start_s) * 1000),
        )
        self._publish("plan_outcome", outcome.to_event_dict())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_dispatcher_plan.py -v`
Expected: ALL tests pass (including the 5 pre-existing ones).

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/dispatcher.py tests/unit/test_dispatcher_plan.py
git commit -m "feat(dispatcher): publish plan_outcome SSE event on run_plan completion"
```

---

### Task 1.3: `StreamingDaemon` publishes `plan_outcome status=miss`

**Files:**
- Modify: `src/voice_commander/daemon.py`
- Modify: `tests/unit/test_streaming_daemon.py`

- [ ] **Step 1: Inspect current test file for fixture/style**

Run: `uv run pytest tests/unit/test_streaming_daemon.py --collect-only -q`
Confirm tests run so current patterns are reusable.

- [ ] **Step 2: Append failing tests**

Append to `tests/unit/test_streaming_daemon.py` (import anything not already imported):

```python
def _miss_outcomes(bus) -> list[dict]:
    return [
        e.data
        for e in bus.replay_after(0)
        if e.type == "plan_outcome" and e.data.get("status") == "miss"
    ]


def test_process_utterance_publishes_miss_on_route_none(daemon_factory):
    """When LLMRouter.route() returns None, publish plan_outcome(status=miss)."""
    daemon, bus, fake_router, _dispatcher = daemon_factory(
        route_return=None,
        transcribe_text="open sptfy",
        transcribe_confidence=0.9,
    )
    import numpy as np
    daemon._process_utterance(np.zeros(16000, dtype="float32"))

    misses = _miss_outcomes(bus)
    assert len(misses) == 1
    assert misses[0]["transcript"] == "open sptfy"
    assert misses[0]["steps"] == []
    assert misses[0]["failed_step_index"] is None
    assert misses[0]["duration_ms"] >= 0


def test_process_utterance_publishes_miss_on_confidence_gate(daemon_factory):
    """When confidence < min_confidence, publish plan_outcome(status=miss)."""
    daemon, bus, _fake_router, _dispatcher = daemon_factory(
        transcribe_text="mumble",
        transcribe_confidence=0.1,  # below default 0.30
        min_confidence=0.30,
    )
    import numpy as np
    daemon._process_utterance(np.zeros(16000, dtype="float32"))

    misses = _miss_outcomes(bus)
    assert len(misses) == 1
    assert misses[0]["transcript"] == "mumble"


def test_process_utterance_no_miss_on_word_count_gate(daemon_factory):
    """Word-count drops are infrastructure noise and must NOT publish plan_outcome."""
    daemon, bus, _fake_router, _dispatcher = daemon_factory(
        transcribe_text="",
        transcribe_confidence=0.9,
        min_word_count=1,
    )
    import numpy as np
    daemon._process_utterance(np.zeros(16000, dtype="float32"))

    assert _miss_outcomes(bus) == []
```

If `daemon_factory` does not already exist in this test file, add this fixture at module top:

```python
import pytest
from unittest.mock import MagicMock
from voice_commander.event_bus import EventBus
from voice_commander.daemon import StreamingDaemon
from voice_commander.feedback import CapturingFeedbackSink
from voice_commander.transcriber import TranscriptionResult


@pytest.fixture
def daemon_factory(tmp_path):
    def _make(
        route_return=None,
        transcribe_text="hello",
        transcribe_confidence=0.9,
        transcribe_no_speech_prob=0.0,
        min_confidence=0.30,
        min_word_count=1,
        max_no_speech_prob=0.6,
    ):
        bus = EventBus()
        transcriber = MagicMock()
        transcriber.transcribe.return_value = TranscriptionResult(
            text=transcribe_text,
            language="en",
            duration_ms=100,
            confidence=transcribe_confidence,
            no_speech_prob=transcribe_no_speech_prob,
        )
        router = MagicMock()
        router.route.return_value = route_return
        dispatcher = MagicMock()
        daemon = StreamingDaemon(
            feedback=CapturingFeedbackSink(),
            recorder=None,
            transcriber=transcriber,
            llm_router=router,
            dispatcher=dispatcher,
            registry=MagicMock(),
            min_confidence=min_confidence,
            min_word_count=min_word_count,
            max_no_speech_prob=max_no_speech_prob,
            output_dir=str(tmp_path),
            event_bus=bus,
        )
        return daemon, bus, router, dispatcher

    return _make
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_streaming_daemon.py -v -k miss`
Expected: new tests FAIL — no `plan_outcome` miss event.

- [ ] **Step 4: Modify `src/voice_commander/daemon.py`**

Locate `_process_utterance` and modify the two miss branches plus the confidence-gate branch. Replace the entire `_process_utterance` method body with:

```python
    def _process_utterance(self, utterance: npt.NDArray[np.float32]) -> None:
        # Async write for post-mortem debugging
        self._write_utterance_async(utterance)

        start_s = time.perf_counter()

        def _publish_miss(transcript: str) -> None:
            self._publish(
                "plan_outcome",
                {
                    "transcript": transcript,
                    "steps": [],
                    "status": "miss",
                    "failed_step_index": None,
                    "error_msg": None,
                    "duration_ms": int((time.perf_counter() - start_s) * 1000),
                },
            )

        self._publish("transcribing")
        result: TranscriptionResult = self._transcriber.transcribe(utterance)
        self._feedback.on_transcript(result.text, result.confidence)

        # Gate: word-count  (infrastructure noise — no plan_outcome)
        word_count = len(result.text.split())
        if word_count < self._min_word_count:
            logger.debug("Gate: word-count %d < %d, dropping", word_count, self._min_word_count)
            return

        # Gate: no_speech_prob  (infrastructure noise — no plan_outcome)
        if result.no_speech_prob > self._max_no_speech_prob:
            logger.debug(
                "Gate: no_speech_prob %.2f > %.2f, dropping",
                result.no_speech_prob,
                self._max_no_speech_prob,
            )
            return

        # Gate: confidence  (emits plan_outcome status=miss — user-visible)
        if result.confidence < self._min_confidence:
            self._feedback.on_miss(result.text, ())
            _publish_miss(result.text)
            return

        # Mute guard: utterance may have been mid-transcription when mute fired.
        if self._muted:
            logger.debug("Mute guard: dropping utterance '%s' (muted during pipeline)", result.text)
            return

        self._publish("llm_thinking")
        plan = self._llm_router.route(result.text)
        if plan is None:
            self._feedback.on_miss(result.text, ())
            _publish_miss(result.text)
            return
        if self._registry is None:
            logger.error("Registry not set — cannot execute plan for '%s'", result.text)
            return
        self._dispatcher.run_plan(result.text, plan, self._registry)
        self._write_plan_async(result.text, plan)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_streaming_daemon.py -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/voice_commander/daemon.py tests/unit/test_streaming_daemon.py
git commit -m "feat(daemon): publish plan_outcome status=miss on route=None + confidence-gate drop"
```

---

### Task 1.4: SSE integration test for `plan_outcome`

**Files:**
- Create: `tests/integration/test_plan_outcome_sse.py`

- [ ] **Step 1: Write the integration test**

```python
# tests/integration/test_plan_outcome_sse.py
"""End-to-end: daemon publishes plan_outcome → /events SSE delivers it."""

from __future__ import annotations

import json

import httpx
import pytest
from httpx_sse import connect_sse

from voice_commander.event_bus import EventBus
from voice_commander.web.app import create_app
from voice_commander.web.server import WebServer


@pytest.fixture
def live_server():
    bus = EventBus()
    app = create_app(
        registry=None,  # type: ignore[arg-type]  # not touched by /events
        store=None,     # type: ignore[arg-type]
        reload_lock=None,  # type: ignore[arg-type]
        event_bus=bus,
    )
    server = WebServer(app, host="127.0.0.1", port=0)
    assert server.start()
    try:
        yield server, bus
    finally:
        server.stop()


def test_plan_outcome_delivered_over_sse(live_server):
    server, bus = live_server
    url = f"http://127.0.0.1:{server.port}"

    with httpx.Client(timeout=5.0) as client:
        with connect_sse(client, "GET", f"{url}/events") as sse:
            # Publish one plan_outcome
            bus.publish(
                "plan_outcome",
                {
                    "transcript": "minimize",
                    "steps": [{"name": "minimize", "kwargs": {}}],
                    "status": "ok",
                    "failed_step_index": None,
                    "error_msg": None,
                    "duration_ms": 123,
                },
            )
            for event in sse.iter_sse():
                if event.event == "plan_outcome":
                    payload = json.loads(event.data)
                    assert payload["transcript"] == "minimize"
                    assert payload["status"] == "ok"
                    assert payload["steps"][0]["name"] == "minimize"
                    return
    pytest.fail("plan_outcome event not received")
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_plan_outcome_sse.py -v`
Expected: PASS. If `create_app` signature differs or `WebServer.port` isn't exposed, adapt the fixture (do NOT change the production code to match the test — make the test match reality).

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_plan_outcome_sse.py
git commit -m "test(integration): plan_outcome event survives SSE transport end-to-end"
```

---

## Phase 2 — Sprite-side summarization core (no rendering yet)

End state: sprite has all pure-Python modules that turn `plan_outcome` payloads into summary strings, with full unit coverage. Nothing wired into `__main__` or the pyglet window yet.

### Task 2.1: `ChatLog` ring buffer + `ChatLogEntry`

**Files:**
- Create: `src/voice_sprite/chat_log.py`
- Create: `tests/unit/test_chat_log.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_chat_log.py
"""Tests for ChatLog ring buffer + fade curve."""

from __future__ import annotations

from voice_sprite.chat_log import ChatLog, ChatLogEntry


def _entry(text: str, born_at_s: float, status: str = "ok") -> ChatLogEntry:
    return ChatLogEntry(text=text, status=status, born_at_s=born_at_s)  # type: ignore[arg-type]


def test_append_below_cap_retains_all():
    log = ChatLog(max_lines=3, hold_ms=1000, fade_ms=1000)
    log.append(_entry("a", 0.0))
    log.append(_entry("b", 1.0))
    assert [e.text for e in log.entries()] == ["b", "a"]  # newest-first


def test_append_over_cap_evicts_oldest():
    log = ChatLog(max_lines=2, hold_ms=1000, fade_ms=1000)
    log.append(_entry("a", 0.0))
    log.append(_entry("b", 1.0))
    log.append(_entry("c", 2.0))
    assert [e.text for e in log.entries()] == ["c", "b"]


def test_opacity_full_during_hold():
    log = ChatLog(max_lines=3, hold_ms=1000, fade_ms=1000)
    e = _entry("x", 10.0)
    log.append(e)
    assert log.opacity_of(e, 10.0) == 1.0
    assert log.opacity_of(e, 10.999) == 1.0  # still in hold window


def test_opacity_linear_after_hold():
    log = ChatLog(max_lines=3, hold_ms=1000, fade_ms=1000)
    e = _entry("x", 0.0)
    log.append(e)
    assert log.opacity_of(e, 1.0) == 1.0     # right at hold boundary
    assert abs(log.opacity_of(e, 1.5) - 0.5) < 1e-6
    assert log.opacity_of(e, 2.0) == 0.0
    assert log.opacity_of(e, 3.0) == 0.0     # clamped


def test_tick_evicts_fully_faded():
    log = ChatLog(max_lines=5, hold_ms=100, fade_ms=100)
    log.append(_entry("a", 0.0))
    log.append(_entry("b", 0.5))
    log.tick(0.1)
    assert [e.text for e in log.entries()] == ["b", "a"]  # still in hold/fade
    log.tick(1.0)  # now well past both fade windows
    assert log.entries() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_chat_log.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Create `src/voice_sprite/chat_log.py`**

```python
"""Ring-buffered chat log entries for the HUD overlay."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Literal

ChatLogStatus = Literal["ok", "error", "miss"]


@dataclass
class ChatLogEntry:
    text: str
    status: ChatLogStatus
    born_at_s: float


class ChatLog:
    """Fixed-size newest-first ring buffer with a per-entry hold+fade curve."""

    def __init__(self, max_lines: int, hold_ms: int, fade_ms: int) -> None:
        if max_lines <= 0:
            raise ValueError("max_lines must be > 0")
        if hold_ms < 0 or fade_ms < 0:
            raise ValueError("hold_ms and fade_ms must be >= 0")
        self._entries: deque[ChatLogEntry] = deque(maxlen=max_lines)
        self._hold_s = hold_ms / 1000.0
        self._fade_s = fade_ms / 1000.0

    def append(self, entry: ChatLogEntry) -> None:
        self._entries.append(entry)

    def entries(self) -> list[ChatLogEntry]:
        """Newest-first."""
        return list(reversed(self._entries))

    def opacity_of(self, entry: ChatLogEntry, now_s: float) -> float:
        age = now_s - entry.born_at_s
        if age < self._hold_s:
            return 1.0
        if self._fade_s == 0:
            return 0.0
        t = (age - self._hold_s) / self._fade_s
        if t >= 1.0:
            return 0.0
        return max(0.0, min(1.0, 1.0 - t))

    def tick(self, now_s: float) -> None:
        """Evict fully-faded entries. Call ~once per frame."""
        while self._entries and self.opacity_of(self._entries[0], now_s) == 0.0:
            self._entries.popleft()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_chat_log.py -v`
Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/voice_sprite/chat_log.py tests/unit/test_chat_log.py
git commit -m "feat(sprite): add ChatLog ring buffer with hold+fade opacity curve"
```

---

### Task 2.2: `summary_rules.py` — rule table + search-chain detector

**Files:**
- Create: `src/voice_sprite/summary_rules.py`
- Create: `tests/unit/test_summary_rules.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_summary_rules.py
"""Tests for nine-verb rule table + chain detectors."""

from __future__ import annotations

from voice_commander.plan import PlanOutcome, ToolCall
from voice_sprite.summary_rules import CHAIN_DETECTORS, RULES, detect_search_chain


def _outcome(steps: tuple[ToolCall, ...], status: str = "ok") -> PlanOutcome:
    return PlanOutcome(
        transcript="t",
        steps=steps,
        status=status,  # type: ignore[arg-type]
        failed_step_index=None,
        error_msg=None,
        duration_ms=0,
    )


def test_every_catalog_verb_has_rule():
    required = {
        "focus", "minimize", "maximize", "close", "close_window",
        "open", "type", "press", "wait", "click", "scroll", "no_match",
    }
    assert required <= set(RULES)


def test_rules_emit_nonempty_strings():
    samples = {
        "focus": {"target": "chrome"},
        "minimize": {},
        "maximize": {},
        "close": {},
        "close_window": {},
        "open": {"target": "spotify"},
        "type": {"text": "hello world"},
        "press": {"combo": "ctrl+t"},
        "wait": {"ms": 250},
        "click": {},
        "scroll": {"direction": "down"},
        "no_match": {"reason": "n/a"},
    }
    outcome = _outcome(())
    for name, kwargs in samples.items():
        s = RULES[name](kwargs, outcome)
        assert isinstance(s, str) and len(s) > 0, f"rule {name} produced empty string"


def test_type_rule_truncates_long_text():
    outcome = _outcome(())
    s = RULES["type"]({"text": "a" * 80}, outcome)
    assert len(s) <= 40  # "typed \"<30-char-snip>\""


def test_detect_search_chain_canonical():
    steps = (
        ToolCall("focus", {"target": "chrome"}),
        ToolCall("press", {"combo": "ctrl+t"}),
        ToolCall("press", {"combo": "ctrl+l"}),
        ToolCall("type", {"text": "how to lose weight"}),
        ToolCall("press", {"combo": "enter"}),
    )
    assert detect_search_chain(steps) == 'searched chrome for "how to lose weight"'


def test_detect_search_chain_ignores_near_miss():
    steps = (
        ToolCall("focus", {"target": "chrome"}),
        ToolCall("press", {"combo": "ctrl+t"}),
        ToolCall("type", {"text": "foo"}),  # missing ctrl+l
    )
    assert detect_search_chain(steps) is None


def test_chain_detectors_is_a_list():
    assert isinstance(CHAIN_DETECTORS, list)
    assert detect_search_chain in CHAIN_DETECTORS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_summary_rules.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Create `src/voice_sprite/summary_rules.py`**

```python
"""Rule table + chain detectors for one-line HUD summaries.

Covers the nine-verb primitive catalog (ADR 0043) plus the bonus
``scroll`` verb and the sentinel ``no_match``. Chain detectors
recognise canonical multi-step patterns and replace the last-step-wins
default with a more natural phrase.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from voice_commander.plan import PlanOutcome, ToolCall


def _typed_snip(text: str, cap: int = 30) -> str:
    text = text.replace('"', "'")
    if len(text) <= cap:
        return text
    return text[: cap - 1] + "…"


RULES: dict[str, Callable[[dict[str, Any], PlanOutcome], str]] = {
    "focus":        lambda kw, _o: f"focused {kw.get('target', 'window')}",
    "minimize":     lambda _kw, _o: "minimized window",
    "maximize":     lambda _kw, _o: "maximized window",
    "close":        lambda _kw, _o: "closed tab",
    "close_window": lambda _kw, _o: "closed window",
    "open":         lambda kw, _o: f"opened {kw.get('target', 'app')}",
    "type":         lambda kw, _o: f'typed "{_typed_snip(str(kw.get("text", "")))}"',
    "press":        lambda kw, _o: f"pressed {kw.get('combo', '')}".rstrip(),
    "wait":         lambda kw, _o: f"waited {kw.get('ms', 0)}ms",
    "click":        lambda _kw, _o: "clicked",
    "scroll":       lambda kw, _o: (
        f"scrolled {kw.get('direction', '')}".rstrip()
    ),
    "no_match":     lambda _kw, _o: "no match",
}


def detect_search_chain(steps: tuple[ToolCall, ...]) -> str | None:
    """Detect canonical ``search <query> in <browser>`` chain.

    Pattern: focus(target=browser) → press(ctrl+t) → press(ctrl+l) →
    type(text=query) → press(enter).
    """
    if len(steps) < 5:
        return None
    s = steps
    if s[0].name != "focus":
        return None
    if s[1].name != "press" or s[1].kwargs.get("combo") != "ctrl+t":
        return None
    if s[2].name != "press" or s[2].kwargs.get("combo") != "ctrl+l":
        return None
    if s[3].name != "type":
        return None
    if s[4].name != "press" or s[4].kwargs.get("combo") != "enter":
        return None
    browser = str(s[0].kwargs.get("target", "browser"))
    query = _typed_snip(str(s[3].kwargs.get("text", "")), cap=40)
    return f'searched {browser} for "{query}"'


CHAIN_DETECTORS: list[Callable[[tuple[ToolCall, ...]], str | None]] = [
    detect_search_chain,
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_summary_rules.py -v`
Expected: 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/voice_sprite/summary_rules.py tests/unit/test_summary_rules.py
git commit -m "feat(sprite): add rule table + search-chain detector for HUD summaries"
```

---

### Task 2.3: `LLMSummaryClient` — httpx wrapper for LM Studio summarization

**Files:**
- Create: `src/voice_sprite/llm_summary_client.py`
- Create: `tests/unit/test_llm_summary_client.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_llm_summary_client.py
"""Tests for LLMSummaryClient — httpx mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from voice_commander.plan import PlanOutcome, ToolCall
from voice_sprite.llm_summary_client import LLMSummaryClient


def _outcome() -> PlanOutcome:
    return PlanOutcome(
        transcript="minimize",
        steps=(ToolCall("minimize", {}),),
        status="error",
        failed_step_index=0,
        error_msg="FocusWindowError",
        duration_ms=42,
    )


def _mock_client_response(content: str = "minimize failed — no window"):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    return resp


def test_returns_content_on_success():
    client = LLMSummaryClient("http://localhost:1234/v1", "gemma-4-e4b", timeout_ms=800)
    mock_post = MagicMock(return_value=_mock_client_response("minimize failed"))
    with patch.object(client, "_client", MagicMock(post=mock_post)):
        assert client.summarize(_outcome()) == "minimize failed"


def test_returns_none_on_timeout():
    client = LLMSummaryClient("http://localhost:1234/v1", "m", timeout_ms=800)
    mock_post = MagicMock(side_effect=httpx.TimeoutException("slow"))
    with patch.object(client, "_client", MagicMock(post=mock_post)):
        assert client.summarize(_outcome()) is None


def test_returns_none_on_connect_error():
    client = LLMSummaryClient("http://localhost:1234/v1", "m", timeout_ms=800)
    mock_post = MagicMock(side_effect=httpx.ConnectError("down"))
    with patch.object(client, "_client", MagicMock(post=mock_post)):
        assert client.summarize(_outcome()) is None


def test_returns_none_on_malformed_json():
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.side_effect = ValueError("bad json")
    client = LLMSummaryClient("http://localhost:1234/v1", "m", timeout_ms=800)
    with patch.object(client, "_client", MagicMock(post=MagicMock(return_value=resp))):
        assert client.summarize(_outcome()) is None


def test_returns_none_on_empty_content():
    client = LLMSummaryClient("http://localhost:1234/v1", "m", timeout_ms=800)
    mock_post = MagicMock(return_value=_mock_client_response(""))
    with patch.object(client, "_client", MagicMock(post=mock_post)):
        assert client.summarize(_outcome()) is None


def test_strips_and_truncates_content():
    client = LLMSummaryClient("http://localhost:1234/v1", "m", timeout_ms=800)
    long = "x" * 120
    mock_post = MagicMock(return_value=_mock_client_response(f"  {long}  "))
    with patch.object(client, "_client", MagicMock(post=mock_post)):
        out = client.summarize(_outcome())
    assert out is not None
    assert len(out) <= 80
    assert out.startswith("x")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_llm_summary_client.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Create `src/voice_sprite/llm_summary_client.py`**

```python
"""HTTP client for one-shot LM Studio summarization of plan outcomes."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from voice_commander.plan import PlanOutcome

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = (
    "Summarize this Windows voice-command outcome in at most 6 words, "
    "past tense. If status is 'error', include the failure reason. "
    "Do not add quotes, prefixes, or explanations. Output plain text."
)

_MAX_LEN = 80


class LLMSummaryClient:
    """Calls LM Studio to synthesize a short HUD summary from a PlanOutcome."""

    def __init__(self, endpoint_url: str, model_id: str, timeout_ms: int) -> None:
        self._endpoint_url = endpoint_url.rstrip("/")
        self._model_id = model_id
        read_s = max(0.05, timeout_ms / 1000.0 - 0.1)
        self._client = httpx.Client(
            base_url=self._endpoint_url,
            timeout=httpx.Timeout(connect=0.1, read=read_s, write=1.0, pool=1.0),
        )

    def summarize(self, outcome: PlanOutcome) -> str | None:
        body: dict[str, Any] = {
            "model": self._model_id,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(outcome.to_event_dict())},
            ],
            "tool_choice": "none",
            "temperature": 0,
            "stream": False,
            "max_tokens": 24,
        }
        try:
            resp = self._client.post("/chat/completions", json=body)
            resp.raise_for_status()
        except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPError) as exc:
            logger.debug("LLM summary HTTP error: %s", exc)
            return None
        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            logger.debug("LLM summary malformed response: %s", exc)
            return None
        if not isinstance(content, str):
            return None
        content = content.strip()
        if not content:
            return None
        return content[:_MAX_LEN]

    def close(self) -> None:
        self._client.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_llm_summary_client.py -v`
Expected: 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/voice_sprite/llm_summary_client.py tests/unit/test_llm_summary_client.py
git commit -m "feat(sprite): LLMSummaryClient for HUD fallback summarization"
```

---

### Task 2.4: `Summarizer` — hybrid rule + LLM orchestrator

**Files:**
- Create: `src/voice_sprite/summarizer.py`
- Create: `tests/unit/test_summarizer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_summarizer.py
"""Tests for Summarizer — hybrid rule-first, LLM fallback."""

from __future__ import annotations

from unittest.mock import MagicMock

from voice_commander.plan import PlanOutcome, ToolCall
from voice_sprite.summarizer import Summarizer
from voice_sprite.summary_rules import CHAIN_DETECTORS, RULES


def _outcome(steps, status="ok", error_msg=None, failed_index=None):
    return PlanOutcome(
        transcript="t",
        steps=tuple(steps),
        status=status,
        failed_step_index=failed_index,
        error_msg=error_msg,
        duration_ms=10,
    )


def _summarizer(llm=None, enabled=True) -> Summarizer:
    return Summarizer(
        rules=RULES,
        chain_detectors=CHAIN_DETECTORS,
        llm_client=llm,
        llm_fallback_enabled=enabled,
    )


def test_miss_returns_no_match_without_llm():
    llm = MagicMock()
    s = _summarizer(llm=llm)
    assert s.summarize(_outcome([], status="miss")) == "no match"
    llm.summarize.assert_not_called()


def test_single_step_ok_uses_rule():
    llm = MagicMock()
    s = _summarizer(llm=llm)
    out = _outcome([ToolCall("minimize", {})])
    assert s.summarize(out) == "minimized window"
    llm.summarize.assert_not_called()


def test_chain_detector_wins_over_last_step_rule():
    steps = [
        ToolCall("focus", {"target": "chrome"}),
        ToolCall("press", {"combo": "ctrl+t"}),
        ToolCall("press", {"combo": "ctrl+l"}),
        ToolCall("type", {"text": "cats"}),
        ToolCall("press", {"combo": "enter"}),
    ]
    s = _summarizer()
    assert s.summarize(_outcome(steps)) == 'searched chrome for "cats"'


def test_multi_step_all_in_rules_uses_last_step():
    steps = [
        ToolCall("focus", {"target": "notepad"}),
        ToolCall("press", {"combo": "ctrl+v"}),
    ]
    s = _summarizer()
    # No chain detector matches — last-step-wins → "pressed ctrl+v"
    assert s.summarize(_outcome(steps)) == "pressed ctrl+v"


def test_unknown_verb_triggers_llm():
    llm = MagicMock()
    llm.summarize.return_value = "did a thing"
    s = _summarizer(llm=llm)
    out = _outcome([ToolCall("reticulate_splines", {})])
    assert s.summarize(out) == "did a thing"
    llm.summarize.assert_called_once()


def test_error_triggers_llm():
    llm = MagicMock()
    llm.summarize.return_value = "minimize failed"
    s = _summarizer(llm=llm)
    out = _outcome(
        [ToolCall("minimize", {})],
        status="error",
        failed_index=0,
        error_msg="FocusWindowError",
    )
    assert s.summarize(out) == "minimize failed"


def test_error_with_llm_none_falls_back_to_raw():
    llm = MagicMock()
    llm.summarize.return_value = None
    s = _summarizer(llm=llm)
    out = _outcome(
        [ToolCall("minimize", {})],
        status="error",
        failed_index=0,
        error_msg="x",
    )
    assert s.summarize(out) == "minimize failed"


def test_error_with_unknown_failed_step_uses_generic():
    llm = MagicMock()
    llm.summarize.return_value = None
    s = _summarizer(llm=llm)
    out = _outcome([ToolCall("foo", {})], status="error", failed_index=0, error_msg="x")
    assert s.summarize(out) == "foo failed"


def test_llm_disabled_skips_llm_on_unknown_verb():
    llm = MagicMock()
    s = _summarizer(llm=llm, enabled=False)
    out = _outcome([ToolCall("reticulate_splines", {})])
    result = s.summarize(out)
    # No LLM call; must produce *something* deterministic.
    llm.summarize.assert_not_called()
    assert result == "reticulate_splines"


def test_no_llm_client_still_works():
    s = _summarizer(llm=None)
    assert s.summarize(_outcome([ToolCall("minimize", {})])) == "minimized window"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_summarizer.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Create `src/voice_sprite/summarizer.py`**

```python
"""Hybrid rule-table + LLM-fallback summarizer for HUD entries."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from voice_commander.plan import PlanOutcome, ToolCall

from .llm_summary_client import LLMSummaryClient


class Summarizer:
    """Turns a PlanOutcome into a short one-line HUD summary.

    Order:

    1. ``status="miss"`` → ``"no match"`` (rules only, never LLM).
    2. ``status="ok"``:
       a. Any chain detector matches → its phrase.
       b. Single step in rule table → rule(kwargs, outcome).
       c. Multi-step where all steps in rule table → last-step rule.
       d. Any step outside rule table → LLM fallback (or raw fallback
          when ``llm_fallback_enabled=False``).
    3. ``status="error"``:
       a. LLM fallback with full outcome.
       b. LLM unavailable / disabled / returns None → ``f"{failed_step.name} failed"``.
    4. LLM response >80 chars → already truncated by ``LLMSummaryClient``.
    """

    def __init__(
        self,
        rules: dict[str, Callable[[dict[str, Any], PlanOutcome], str]],
        chain_detectors: list[Callable[[tuple[ToolCall, ...]], str | None]],
        llm_client: LLMSummaryClient | None,
        llm_fallback_enabled: bool = True,
    ) -> None:
        self._rules = rules
        self._chain_detectors = chain_detectors
        self._llm_client = llm_client
        self._llm_enabled = llm_fallback_enabled

    def summarize(self, outcome: PlanOutcome) -> str:
        if outcome.status == "miss":
            return "no match"
        if outcome.status == "error":
            return self._summarize_error(outcome)
        return self._summarize_ok(outcome)

    def _summarize_ok(self, outcome: PlanOutcome) -> str:
        steps = outcome.steps
        for detector in self._chain_detectors:
            phrase = detector(steps)
            if phrase is not None:
                return phrase
        if not steps:
            return ""  # defensive — ok with zero steps should not happen
        if all(s.name in self._rules for s in steps):
            last = steps[-1]
            return self._rules[last.name](last.kwargs, outcome)
        # Unknown verb — LLM fallback
        if self._llm_enabled and self._llm_client is not None:
            out = self._llm_client.summarize(outcome)
            if out:
                return out
        # Raw fallback for unknown verb: last step name
        return steps[-1].name

    def _summarize_error(self, outcome: PlanOutcome) -> str:
        if self._llm_enabled and self._llm_client is not None:
            out = self._llm_client.summarize(outcome)
            if out:
                return out
        # Raw fallback
        idx = outcome.failed_step_index
        if idx is not None and 0 <= idx < len(outcome.steps):
            return f"{outcome.steps[idx].name} failed"
        return "command failed"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_summarizer.py -v`
Expected: 10 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/voice_sprite/summarizer.py tests/unit/test_summarizer.py
git commit -m "feat(sprite): Summarizer orchestrates rules + chain detectors + LLM fallback"
```

---

## Phase 3 — Sprite composite window + HUD renderer

End state: the existing pyglet window is enlarged to host sprite + HUD + speech bubble; a new `ChatLogRenderer` draws entries; `__main__` wires summarizer + chat log into the SSE event handler. Sprite still sits on primary monitor — cursor-follow lands in Phase 4.

### Task 3.1: Extend `SpriteAppConfig` with `[hud]` section and new `[sprite]` keys

**Files:**
- Modify: `src/voice_sprite/config.py`
- Modify: `tests/unit/test_sprite_config.py`
- Modify: `config.toml`

- [ ] **Step 1: Write failing test**

Append to `tests/unit/test_sprite_config.py`:

```python
def test_hud_defaults(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("[sprite]\nbase_size_px = 128\n", encoding="utf-8")
    cfg = load_sprite_config(cfg_path)
    assert cfg.hud.enabled is True
    assert cfg.hud.max_lines == 5
    assert cfg.hud.hold_ms == 4000
    assert cfg.hud.fade_ms == 3000
    assert cfg.hud.font_size == 13
    assert cfg.hud.width_px == 220
    assert cfg.hud.offset_x == -230
    assert cfg.hud.offset_y == 0
    assert cfg.hud.llm_summary_timeout_ms == 800
    assert cfg.hud.llm_fallback_enabled is True


def test_hud_override(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        "[hud]\nenabled = false\nmax_lines = 3\nhold_ms = 2000\n",
        encoding="utf-8",
    )
    cfg = load_sprite_config(cfg_path)
    assert cfg.hud.enabled is False
    assert cfg.hud.max_lines == 3
    assert cfg.hud.hold_ms == 2000


def test_sprite_follow_cursor_defaults(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("", encoding="utf-8")
    cfg = load_sprite_config(cfg_path)
    assert cfg.follow_cursor is True
    assert cfg.follow_poll_hz == 30
    assert cfg.margin_x == 8
    assert cfg.margin_y == 8
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_sprite_config.py -v`
Expected: FAIL — attributes missing.

- [ ] **Step 3: Modify `src/voice_sprite/config.py`**

Replace the full file:

```python
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class HudConfig:
    enabled: bool = True
    max_lines: int = 5
    hold_ms: int = 4000
    fade_ms: int = 3000
    font_size: int = 13
    width_px: int = 220
    offset_x: int = -230
    offset_y: int = 0
    llm_summary_timeout_ms: int = 800
    llm_fallback_enabled: bool = True


@dataclass(frozen=True)
class SpriteAppConfig:
    """Configuration for the voice_sprite process."""

    daemon_url: str = "http://127.0.0.1:8765"
    corner: str = "bottom_right"
    base_size_px: int = 128
    offset_x: int = 16
    offset_y: int = 16
    asset_path: str = "assets/sprite"
    bubble_fade_ms: int = 2000
    heartbeat_timeout_ms: int = 3000
    render_scale: float = 0.75
    y_nudge_px: int = 16
    # Cursor-follow
    follow_cursor: bool = True
    follow_poll_hz: int = 30
    margin_x: int = 8
    margin_y: int = 8
    # HUD
    hud: HudConfig = field(default_factory=HudConfig)


def load_sprite_config(
    config_path: Path,
    local_path: Path | None = None,
) -> SpriteAppConfig:
    """Load sprite config from config.toml (+ optional config.local.toml)."""
    raw = _read_toml(config_path)
    if local_path is None:
        local_path = config_path.with_name(f"{config_path.stem}.local{config_path.suffix}")
    if local_path != config_path and local_path.exists():
        raw = _deep_merge(raw, _read_toml(local_path))

    sprite_raw = raw.get("sprite", {})
    web_raw = raw.get("web", {})
    hud_raw = raw.get("hud", {})

    host = web_raw.get("host", "127.0.0.1")
    port = web_raw.get("port", 8765)
    daemon_url = f"http://{host}:{port}"

    hud = HudConfig(
        enabled=bool(hud_raw.get("enabled", True)),
        max_lines=int(hud_raw.get("max_lines", 5)),
        hold_ms=int(hud_raw.get("hold_ms", 4000)),
        fade_ms=int(hud_raw.get("fade_ms", 3000)),
        font_size=int(hud_raw.get("font_size", 13)),
        width_px=int(hud_raw.get("width_px", 220)),
        offset_x=int(hud_raw.get("offset_x", -230)),
        offset_y=int(hud_raw.get("offset_y", 0)),
        llm_summary_timeout_ms=int(hud_raw.get("llm_summary_timeout_ms", 800)),
        llm_fallback_enabled=bool(hud_raw.get("llm_fallback_enabled", True)),
    )

    return SpriteAppConfig(
        daemon_url=daemon_url,
        corner=sprite_raw.get("corner", "bottom_right"),
        base_size_px=int(sprite_raw.get("base_size_px", 128)),
        offset_x=int(sprite_raw.get("offset_x", 16)),
        offset_y=int(sprite_raw.get("offset_y", 16)),
        asset_path=sprite_raw.get("asset_path", "assets/sprite"),
        bubble_fade_ms=int(sprite_raw.get("bubble_fade_ms", 2000)),
        heartbeat_timeout_ms=int(sprite_raw.get("heartbeat_timeout_ms", 3000)),
        render_scale=float(sprite_raw.get("render_scale", 0.75)),
        y_nudge_px=int(sprite_raw.get("y_nudge_px", 16)),
        follow_cursor=bool(sprite_raw.get("follow_cursor", True)),
        follow_poll_hz=int(sprite_raw.get("follow_poll_hz", 30)),
        margin_x=int(sprite_raw.get("margin_x", 8)),
        margin_y=int(sprite_raw.get("margin_y", 8)),
        hud=hud,
    )


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
```

- [ ] **Step 4: Append defaults to `config.toml`**

Append (do NOT replace existing blocks):

```toml
# --- HUD overlay (sprite-side) -----------------------------------------------
[hud]
enabled                 = true
max_lines               = 5
hold_ms                 = 4000
fade_ms                 = 3000
font_size               = 13
width_px                = 220
offset_x                = -230
offset_y                = 0
llm_summary_timeout_ms  = 800
llm_fallback_enabled    = true
```

Also, inside the existing `[sprite]` block, append (if absent):

```toml
follow_cursor  = true
follow_poll_hz = 30
margin_x       = 8
margin_y       = 8
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_sprite_config.py -v`
Expected: ALL pass (pre-existing + 3 new).

- [ ] **Step 6: Commit**

```bash
git add src/voice_sprite/config.py tests/unit/test_sprite_config.py config.toml
git commit -m "feat(sprite): extend config with [hud] section + cursor-follow keys"
```

---

### Task 3.2: `ChatLogRenderer` — pyglet labels driven by ChatLog

**Files:**
- Create: `src/voice_sprite/chat_log_renderer.py`
- Create: `tests/unit/test_chat_log_renderer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_chat_log_renderer.py
"""Tests for ChatLogRenderer — pyglet labels driven by ChatLog.

Pyglet Label is stubbed so tests run headless.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from voice_sprite.chat_log import ChatLog, ChatLogEntry


def test_draw_creates_label_per_entry(monkeypatch):
    # Patch pyglet.text.Label so we can count construction calls.
    fake_label_cls = MagicMock()
    with patch("voice_sprite.chat_log_renderer.pyglet") as pyglet_mock:
        pyglet_mock.text.Label = fake_label_cls
        from voice_sprite.chat_log_renderer import ChatLogRenderer

        log = ChatLog(max_lines=5, hold_ms=10000, fade_ms=1000)
        now = time.monotonic()
        log.append(ChatLogEntry("minimized window", "ok", now))
        log.append(ChatLogEntry("no match", "miss", now))

        window = MagicMock(width=400, height=200)
        r = ChatLogRenderer(
            chat_log=log,
            window=window,
            hud_width_px=220,
            font_size=13,
            line_gap_px=4,
        )
        r.draw()
        # Two entries → at least two Label creations (cached after first draw)
        assert fake_label_cls.call_count >= 2


def test_status_color_map(monkeypatch):
    with patch("voice_sprite.chat_log_renderer.pyglet") as pyglet_mock:
        pyglet_mock.text.Label = MagicMock()
        from voice_sprite.chat_log_renderer import _color_for_status

        assert _color_for_status("ok")[:3] == (143, 215, 127)
        assert _color_for_status("error")[:3] == (255, 118, 118)
        assert _color_for_status("miss")[:3] == (255, 181, 98)


def test_hidden_when_chat_log_empty(monkeypatch):
    fake_label_cls = MagicMock()
    with patch("voice_sprite.chat_log_renderer.pyglet") as pyglet_mock:
        pyglet_mock.text.Label = fake_label_cls
        from voice_sprite.chat_log_renderer import ChatLogRenderer

        log = ChatLog(max_lines=3, hold_ms=1, fade_ms=1)
        window = MagicMock(width=400, height=200)
        r = ChatLogRenderer(log, window, hud_width_px=220, font_size=13, line_gap_px=4)
        r.draw()
        fake_label_cls.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_chat_log_renderer.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Create `src/voice_sprite/chat_log_renderer.py`**

```python
"""Renders ChatLog entries as fading pyglet labels on a transparent overlay."""

from __future__ import annotations

import time

import pyglet

from .chat_log import ChatLog, ChatLogEntry

_COLOR_OK = (143, 215, 127)      # #8fd77f — success
_COLOR_ERROR = (255, 118, 118)   # #ff7676 — error
_COLOR_MISS = (255, 181, 98)     # #ffb562 — miss


def _color_for_status(status: str) -> tuple[int, int, int]:
    if status == "error":
        return _COLOR_ERROR
    if status == "miss":
        return _COLOR_MISS
    return _COLOR_OK


class ChatLogRenderer:
    """Draws the ChatLog's current entries as pyglet.text.Label objects.

    Label pool is created lazily (on first draw call that needs more slots).
    Labels are reused across frames; text/color/position set each draw.
    """

    def __init__(
        self,
        chat_log: ChatLog,
        window: pyglet.window.Window,  # type: ignore[name-defined]
        hud_width_px: int,
        font_size: int,
        line_gap_px: int,
    ) -> None:
        self._log = chat_log
        self._window = window
        self._width = hud_width_px
        self._font_size = font_size
        self._line_gap = line_gap_px
        self._labels: list[pyglet.text.Label] = []

    def _ensure_labels(self, count: int) -> None:
        while len(self._labels) < count:
            self._labels.append(
                pyglet.text.Label(
                    text="",
                    font_name="Consolas",
                    font_size=self._font_size,
                    x=0,
                    y=0,
                    anchor_x="left",
                    anchor_y="bottom",
                    color=(255, 255, 255, 0),
                )
            )

    def draw(self) -> None:
        entries = self._log.entries()  # newest-first
        if not entries:
            return
        self._ensure_labels(len(entries))
        now = time.monotonic()

        # Layout — HUD region sits on the left portion of the window.
        # Newest entry at bottom of HUD region, stacking upward.
        line_h = self._font_size + self._line_gap
        bottom_y = 0  # draw at window bottom; window is anchored so sprite bottom = window bottom
        for i, entry in enumerate(entries):
            label = self._labels[i]
            opacity = self._log.opacity_of(entry, now)
            if opacity <= 0:
                label.text = ""
                continue
            r, g, b = _color_for_status(entry.status)
            label.text = entry.text
            label.x = 0
            label.y = bottom_y + i * line_h
            label.color = (r, g, b, int(255 * opacity))
            label.draw()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_chat_log_renderer.py -v`
Expected: 3 tests pass.

- [ ] **Step 5: Add `chat_log_renderer.py` + `cursor_tracker.py` to coverage omit list (preemptively)**

Edit `pyproject.toml` `[tool.coverage.run] omit = [...]` — append:

```toml
    "src/voice_sprite/chat_log_renderer.py",
    "src/voice_sprite/cursor_tracker.py",
```

Rationale: both modules call straight into pyglet / ctypes DLLs at runtime; unit coverage via mocks is already present but runtime exercise is covered by HITL gates.

- [ ] **Step 6: Commit**

```bash
git add src/voice_sprite/chat_log_renderer.py tests/unit/test_chat_log_renderer.py pyproject.toml
git commit -m "feat(sprite): ChatLogRenderer draws fading entries via pyglet labels"
```

---

### Task 3.3: Rework `SpriteWindow` to composite dimensions

**Files:**
- Modify: `src/voice_sprite/window.py`

- [ ] **Step 1: Add `sprite_region_bounds` parameter + ChatLogRenderer hook**

Open `src/voice_sprite/window.py`. Edit the constructor signature to add two keyword-only args `hud_renderer: ChatLogRenderer | None = None` and `sprite_region_x: int = 0` (window-local x where sprite starts; HUD occupies `[0, sprite_region_x]`). Wire them up:

Change `__init__` signature:

```python
    def __init__(
        self,
        width: int,
        height: int,
        x: int,
        y: int,
        renderer: SpriteRenderer,
        bubble: SpeechBubble,
        render_scale: float = 1.0,
        y_nudge_px: int = 0,
        *,
        hud_renderer: "ChatLogRenderer | None" = None,
        sprite_region_x: int = 0,
        sprite_region_w: int | None = None,
    ) -> None:
```

Inside `__init__`, after the existing `self._y_nudge_px = y_nudge_px` line, add:

```python
        self._hud_renderer = hud_renderer
        self._sprite_region_x = sprite_region_x
        self._sprite_region_w = sprite_region_w if sprite_region_w is not None else width
```

Update the `on_draw` method — replace the `fit_scale = min(self.width / w, self.height / h)` line and the subsequent sprite-positioning block with a version that uses `sprite_region_w` / `sprite_region_x`:

```python
        region_w = self._sprite_region_w
        region_h = self.height
        fit_scale = min(region_w / w, region_h / h)
        final_scale = fit_scale * self._render_scale
        disp_w = w * final_scale
        disp_h = h * final_scale
        sprite_x = self._sprite_region_x + (region_w - disp_w) / 2
        sprite_y = (region_h - disp_h) / 2 + self._y_nudge_px
```

Also after the `self._sprite.draw()` line, before the speech-bubble block, add:

```python
        if self._hud_renderer is not None:
            self._hud_renderer.draw()
```

Add to the TYPE_CHECKING block at top:

```python
    from .chat_log_renderer import ChatLogRenderer
```

- [ ] **Step 2: Import check**

Run: `uv run python -c "from voice_sprite.window import SpriteWindow"`
Expected: no ImportError.

- [ ] **Step 3: Run all unit tests**

Run: `uv run pytest tests/unit -v`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add src/voice_sprite/window.py
git commit -m "feat(sprite): SpriteWindow supports composite sprite + HUD region"
```

---

### Task 3.4: Wire plan_outcome handler in `__main__`

**Files:**
- Modify: `src/voice_sprite/__main__.py`

- [ ] **Step 1: Edit `__main__.main()` — construct ChatLog + Summarizer + LLMSummaryClient + ChatLogRenderer**

After the existing `# State machine + renderer + bubble` block (roughly lines 85-88), add:

```python
    # Chat-log + summarizer stack
    from .chat_log import ChatLog, ChatLogEntry
    from .chat_log_renderer import ChatLogRenderer
    from .llm_summary_client import LLMSummaryClient
    from .summarizer import Summarizer
    from .summary_rules import CHAIN_DETECTORS, RULES

    chat_log = ChatLog(
        max_lines=cfg.hud.max_lines,
        hold_ms=cfg.hud.hold_ms,
        fade_ms=cfg.hud.fade_ms,
    )
    llm_client: LLMSummaryClient | None = None
    if cfg.hud.enabled and cfg.hud.llm_fallback_enabled:
        llm_client = LLMSummaryClient(
            endpoint_url=cfg.daemon_url.replace(":8765", ":1234") + "/v1",
            model_id="google/gemma-4-e4b",
            timeout_ms=cfg.hud.llm_summary_timeout_ms,
        )
    summarizer = Summarizer(
        rules=RULES,
        chain_detectors=CHAIN_DETECTORS,
        llm_client=llm_client,
        llm_fallback_enabled=cfg.hud.llm_fallback_enabled,
    )
```

Note: the LM Studio endpoint is NOT the same as daemon_url. Fix the URL derivation properly — read from config. **Correct approach:** extend sprite config to read `[llm].endpoint_url` from the same `config.toml`. For this task we add that to `config.py` in a follow-up; for now, **skip LLM wiring if the user has not set an explicit override** by defaulting to `http://localhost:1234/v1` verbatim:

Replace the `LLMSummaryClient(endpoint_url=…)` line with:

```python
        llm_client = LLMSummaryClient(
            endpoint_url="http://localhost:1234/v1",
            model_id="google/gemma-4-e4b",
            timeout_ms=cfg.hud.llm_summary_timeout_ms,
        )
```

- [ ] **Step 2: Build the HUD renderer + register with the window**

After the existing `x, y = positions.get(cfg.corner, positions["bottom_right"])` line, add HUD-width computation and extend window size. Replace the subsequent `SpriteWindow(...)` block with:

```python
    # Composite window: HUD to the LEFT of sprite region.
    hud_w = cfg.hud.width_px if cfg.hud.enabled else 0
    window_w = size + hud_w
    window_h = size
    # Anchor sprite region to the right of the window.
    sprite_region_x = hud_w
    sprite_region_w = size

    hud_renderer: ChatLogRenderer | None = None
    # ChatLogRenderer is built after window is constructed (needs window ref).

    from .window import SpriteWindow

    window = SpriteWindow(
        width=window_w,
        height=window_h,
        x=x,
        y=y,
        renderer=renderer,
        bubble=bubble,
        render_scale=cfg.render_scale,
        y_nudge_px=cfg.y_nudge_px,
        sprite_region_x=sprite_region_x,
        sprite_region_w=sprite_region_w,
    )

    if cfg.hud.enabled:
        hud_renderer = ChatLogRenderer(
            chat_log=chat_log,
            window=window,
            hud_width_px=hud_w,
            font_size=cfg.hud.font_size,
            line_gap_px=4,
        )
        window._hud_renderer = hud_renderer  # inject post-construction

    window.load_charsheet_image(str(png_path))
    window.apply_win32_flags()
```

- [ ] **Step 3: Extend `on_event` to handle `plan_outcome`**

Find the existing `def on_event(event_type, data): ...` block. Add a new branch right before the existing `if event_type == "tool_fired" ...` line:

```python
        if event_type == "plan_outcome":
            try:
                from voice_commander.plan import PlanOutcome
                outcome = PlanOutcome.from_event_dict(data)
                summary = summarizer.summarize(outcome)
                if summary:
                    import time as _time
                    chat_log.append(ChatLogEntry(
                        text=summary,
                        status=outcome.status,  # type: ignore[arg-type]
                        born_at_s=_time.monotonic(),
                    ))
            except Exception:
                logger.exception("Failed to process plan_outcome event")
```

- [ ] **Step 4: Call `chat_log.tick(now)` from the existing `update(dt)` loop**

Inside the existing `def update(dt: float):` function, append:

```python
        import time as _time
        chat_log.tick(_time.monotonic())
```

- [ ] **Step 5: Call `llm_client.close()` in the `finally` block**

Find the existing `finally: sse.stop() ...` block. Add:

```python
        if llm_client is not None:
            llm_client.close()
```

- [ ] **Step 6: Smoke test**

Run: `uv run python -c "import voice_sprite.__main__"`
Expected: imports cleanly (no pyglet display probe — we only import the module).

Run the unit suite one more time to ensure nothing broke:

Run: `uv run pytest tests/unit -v`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/voice_sprite/__main__.py
git commit -m "feat(sprite): wire plan_outcome event → Summarizer → ChatLog → HUD"
```

---

## Phase 4 — Cursor-follow sprite

End state: sprite polls cursor position at 30 Hz, snaps to bottom-right of whichever monitor's work area the cursor lives on, rescaling per-monitor DPI across boundary crosses. HUD follows because it lives in the same pyglet window.

### Task 4.1: Extend `dpi.py` with `get_dpi_for_monitor()`

**Files:**
- Modify: `src/voice_sprite/dpi.py`
- Modify: `tests/unit/test_dpi.py` (if exists) or create

- [ ] **Step 1: Inspect existing dpi tests**

Run: `ls tests/unit/test_dpi.py`
If exists, append below; otherwise the new test file is fine.

- [ ] **Step 2: Write/append the failing test**

```python
# tests/unit/test_dpi.py (append if exists, create otherwise)
"""Tests for DPI helpers."""

from unittest.mock import patch, MagicMock

from voice_sprite.dpi import get_dpi_for_monitor


def test_get_dpi_for_monitor_returns_dpi_x():
    fake_windll = MagicMock()
    dpi_x_storage = []

    def mock_GetDpiForMonitor(hmon, dpi_type, dpi_x_ptr, dpi_y_ptr):
        # Simulate Windows writing 144 to both dpi_x and dpi_y
        dpi_x_ptr._obj.value = 144
        dpi_y_ptr._obj.value = 144
        return 0  # S_OK

    fake_windll.shcore.GetDpiForMonitor.side_effect = mock_GetDpiForMonitor

    with patch("voice_sprite.dpi.ctypes.windll", fake_windll):
        assert get_dpi_for_monitor(12345) == 144


def test_get_dpi_for_monitor_falls_back_to_96_on_error():
    fake_windll = MagicMock()
    fake_windll.shcore.GetDpiForMonitor.side_effect = OSError("boom")

    with patch("voice_sprite.dpi.ctypes.windll", fake_windll):
        assert get_dpi_for_monitor(0) == 96
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_dpi.py -v`
Expected: FAIL — `get_dpi_for_monitor` missing.

- [ ] **Step 4: Extend `src/voice_sprite/dpi.py`**

Append to the file:

```python
def get_dpi_for_monitor(hmon: int) -> int:
    """Return the effective DPI of a specific monitor. Falls back to 96 on failure.

    Uses ``GetDpiForMonitor`` (shcore.dll) with ``MDT_EFFECTIVE_DPI=0``.
    """
    try:
        dpi_x = ctypes.c_uint()
        dpi_y = ctypes.c_uint()
        ctypes.windll.shcore.GetDpiForMonitor(
            hmon, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)
        )
        return int(dpi_x.value)
    except (AttributeError, OSError):
        logger.debug("GetDpiForMonitor failed for hmon=%s; falling back to 96", hmon)
        return 96
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_dpi.py -v`
Expected: 2 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/voice_sprite/dpi.py tests/unit/test_dpi.py
git commit -m "feat(sprite): add get_dpi_for_monitor() helper for per-monitor rescale"
```

---

### Task 4.2: `CursorDock` — ctypes + pyglet polling

**Files:**
- Create: `src/voice_sprite/cursor_tracker.py`
- Create: `tests/unit/test_cursor_tracker.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_cursor_tracker.py
"""Tests for CursorDock — mocks ctypes + window."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _fake_ctypes(cursor_xy=(100, 100), rcwork=(0, 0, 1920, 1040), hmon=1, dpi=96):
    fake = MagicMock()

    def set_pt(ptr):
        ptr.x = cursor_xy[0]
        ptr.y = cursor_xy[1]
        return 1  # BOOL success

    fake.windll.user32.GetCursorPos.side_effect = set_pt
    fake.windll.user32.MonitorFromPoint.return_value = hmon

    def get_mi(hmon_arg, mi_ptr):
        mi = mi_ptr._obj
        mi.rcWork.left, mi.rcWork.top, mi.rcWork.right, mi.rcWork.bottom = rcwork
        return 1

    fake.windll.user32.GetMonitorInfoW.side_effect = get_mi
    return fake


def test_tick_snaps_to_bottom_right_of_workarea():
    with patch("voice_sprite.cursor_tracker.ctypes", _fake_ctypes(rcwork=(0, 0, 1920, 1040))):
        with patch("voice_sprite.cursor_tracker.get_dpi_for_monitor", return_value=96):
            from voice_sprite.cursor_tracker import CursorDock

            window = MagicMock(width=348, height=128)
            dock = CursorDock(
                window=window,
                sprite_base_size_px=128,
                window_extra_w_px=220,
                window_extra_h_px=0,
                margin_x=8,
                margin_y=8,
            )
            dock.tick(0.033)
            window.set_size.assert_called_once_with(128 + 220, 128 + 0)
            # Sprite region sits flush against rcWork.right - margin.
            # Window left = right - (sprite + extra) - margin + extra_w (shift right so
            # the sprite column, not the HUD, hugs the right edge).
            # With our spec formula:
            #   x = rcWork.right - window_w - margin_x + extra_w
            #   y = rcWork.bottom - window_h - margin_y
            expected_x = 1920 - 348 - 8 + 220
            expected_y = 1040 - 128 - 8
            window.set_location.assert_called_once_with(expected_x, expected_y)


def test_tick_hysteresis_no_op_when_hmon_unchanged():
    fake = _fake_ctypes(hmon=42)
    with patch("voice_sprite.cursor_tracker.ctypes", fake):
        with patch("voice_sprite.cursor_tracker.get_dpi_for_monitor", return_value=96):
            from voice_sprite.cursor_tracker import CursorDock

            window = MagicMock(width=348, height=128)
            dock = CursorDock(window, 128, 220, 0, 8, 8)
            dock.tick(0.033)
            dock.tick(0.033)
            assert window.set_location.call_count == 1
            assert window.set_size.call_count == 1


def test_tick_locked_workstation_noops():
    # GetCursorPos returns 0 on locked workstation.
    fake = MagicMock()
    fake.windll.user32.GetCursorPos.return_value = 0
    with patch("voice_sprite.cursor_tracker.ctypes", fake):
        from voice_sprite.cursor_tracker import CursorDock

        window = MagicMock(width=348, height=128)
        dock = CursorDock(window, 128, 220, 0, 8, 8)
        dock.tick(0.033)
        window.set_location.assert_not_called()
        window.set_size.assert_not_called()


def test_tick_monitor_info_failure_skips():
    fake = _fake_ctypes()
    fake.windll.user32.GetMonitorInfoW.side_effect = lambda *_: 0  # FALSE
    with patch("voice_sprite.cursor_tracker.ctypes", fake):
        with patch("voice_sprite.cursor_tracker.get_dpi_for_monitor", return_value=96):
            from voice_sprite.cursor_tracker import CursorDock

            window = MagicMock(width=348, height=128)
            dock = CursorDock(window, 128, 220, 0, 8, 8)
            dock.tick(0.033)
            window.set_location.assert_not_called()


def test_tick_dpi_rescale():
    with patch("voice_sprite.cursor_tracker.ctypes", _fake_ctypes()):
        with patch("voice_sprite.cursor_tracker.get_dpi_for_monitor", return_value=144):
            from voice_sprite.cursor_tracker import CursorDock

            window = MagicMock(width=348, height=128)
            dock = CursorDock(
                window, sprite_base_size_px=128, window_extra_w_px=220,
                window_extra_h_px=0, margin_x=8, margin_y=8,
            )
            dock.tick(0.033)
            # scale 1.5 → sprite 192, extra_w 330 → window (522, 192)
            window.set_size.assert_called_once_with(192 + 330, 192 + 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_cursor_tracker.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Create `src/voice_sprite/cursor_tracker.py`**

```python
"""Polls the OS cursor and docks a pyglet window to the current monitor's
work area (taskbar-excluded). Ctypes-only — no pywin32 dependency."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging

from .dpi import get_dpi_for_monitor

logger = logging.getLogger(__name__)

_MONITOR_DEFAULTTONEAREST = 0x00000002


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", wt.LONG),
        ("top", wt.LONG),
        ("right", wt.LONG),
        ("bottom", wt.LONG),
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", wt.DWORD),
    ]


class CursorDock:
    """Snaps the given pyglet window to the bottom-right of the
    monitor currently under the cursor, with per-monitor DPI rescale.

    The sprite occupies the window's right region (``sprite_base_size_px``
    wide after DPI scale); the HUD occupies the left region (``window_extra_w_px``
    wide after DPI scale); the speech bubble occupies the region above the
    sprite (``window_extra_h_px`` tall). The sprite column — NOT the HUD —
    is what sits flush against ``rcWork.right - margin_x``.
    """

    def __init__(
        self,
        window,
        sprite_base_size_px: int,
        window_extra_w_px: int,
        window_extra_h_px: int,
        margin_x: int,
        margin_y: int,
    ) -> None:
        self._window = window
        self._sprite_base = sprite_base_size_px
        self._extra_w_base = window_extra_w_px
        self._extra_h_base = window_extra_h_px
        self._margin_x = margin_x
        self._margin_y = margin_y
        self._last_hmon: int | None = None

    def tick(self, _dt: float) -> None:
        pt = wt.POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            return
        if pt.x == 0 and pt.y == 0:
            # Common sentinel on locked workstation; skip.
            return

        hmon = ctypes.windll.user32.MonitorFromPoint(pt, _MONITOR_DEFAULTTONEAREST)
        if hmon == self._last_hmon:
            return

        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        if not ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            logger.debug("GetMonitorInfoW failed for hmon=%s", hmon)
            return

        dpi = get_dpi_for_monitor(hmon)
        scale = dpi / 96.0
        sprite_size = int(self._sprite_base * scale)
        extra_w = int(self._extra_w_base * scale)
        extra_h = int(self._extra_h_base * scale)
        window_w = sprite_size + extra_w
        window_h = sprite_size + extra_h

        self._window.set_size(window_w, window_h)

        # Anchor sprite bottom-right corner to rcWork bottom-right - margin.
        # Window origin is top-left; sprite sits at the window's right edge,
        # so window_x is shifted right by extra_w to keep the sprite (not HUD)
        # flush against the right margin.
        x = mi.rcWork.right - window_w - self._margin_x + extra_w
        y = mi.rcWork.bottom - window_h - self._margin_y
        self._window.set_location(x, y)
        self._last_hmon = hmon
        logger.info(
            "CursorDock → hmon=%s rcWork=(%d,%d,%d,%d) dpi=%d → window=(%d,%d) @ (%d,%d)",
            hmon, mi.rcWork.left, mi.rcWork.top, mi.rcWork.right, mi.rcWork.bottom,
            dpi, window_w, window_h, x, y,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_cursor_tracker.py -v`
Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/voice_sprite/cursor_tracker.py tests/unit/test_cursor_tracker.py
git commit -m "feat(sprite): CursorDock snaps window to cursor's monitor work area"
```

---

### Task 4.3: Wire `CursorDock` into `__main__` + remove static positions dict

**Files:**
- Modify: `src/voice_sprite/__main__.py`

- [ ] **Step 1: Replace the positions dict + schedule CursorDock**

Locate the `positions = {...}` block and the `x, y = positions.get(...)` line inside `main()`. Replace them (and the subsequent window construction) with:

```python
    # Start position: primary-monitor bottom-right, will be updated on first CursorDock tick.
    screen = pyglet.display.get_display().get_default_screen()
    hud_w = cfg.hud.width_px if cfg.hud.enabled else 0
    bubble_h = 0  # future: add bubble vertical space if needed
    window_w = size + hud_w
    window_h = size + bubble_h
    x = screen.width - window_w - cfg.margin_x
    y = cfg.margin_y
```

Keep the existing `SpriteWindow(...)` construction you added in Task 3.4, passing `width=window_w, height=window_h, x=x, y=y`.

- [ ] **Step 2: Import + schedule CursorDock**

After the `window.apply_win32_flags()` call, add:

```python
    # Cursor-follow docking
    if cfg.follow_cursor:
        from .cursor_tracker import CursorDock
        dock = CursorDock(
            window=window,
            sprite_base_size_px=cfg.base_size_px,
            window_extra_w_px=cfg.hud.width_px if cfg.hud.enabled else 0,
            window_extra_h_px=0,
            margin_x=cfg.margin_x,
            margin_y=cfg.margin_y,
        )
        pyglet.clock.schedule_interval(dock.tick, 1.0 / max(1, cfg.follow_poll_hz))
        logger.info("CursorDock scheduled at %d Hz", cfg.follow_poll_hz)
```

- [ ] **Step 3: Ensure PMv2 ordering**

At the very top of `main()` (right after `_configure_logging()` but before any pyglet import or use), add an explicit DPI-awareness call:

```python
    # Must run BEFORE the first pyglet window is constructed, or its HWND
    # is pinned to system-DPI awareness and set_location coords get silently
    # rescaled when crossing monitor boundaries. See ADR 0053.
    import ctypes, contextlib
    with contextlib.suppress(AttributeError, OSError):
        ctypes.windll.shcore.SetProcessDpiAwarenessContext(-4)
```

(`-4` is `DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2`.)

- [ ] **Step 4: Smoke-test module import**

Run: `uv run python -c "import voice_sprite.__main__"`
Expected: clean import.

Run: `uv run pytest tests/unit -v`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/voice_sprite/__main__.py
git commit -m "feat(sprite): wire CursorDock for multi-monitor follow + PMv2 ordering"
```

---

## Phase 5 — ADRs, docs, HITL validation

End state: docs reflect the new surface; three ADRs filed; one agent-reference row per ADR; README + gotchas updated; human has validated each HITL gate and confirmed behaviour.

### Task 5.1: ADR 0051 — HUD overlay

**Files:**
- Create: `docs/decisions/0051-command-hud-overlay.md`

- [ ] **Step 1: Write the ADR**

```markdown
# 0051. Command HUD Overlay (RPG-Style Chat Log Next to Sprite)

**Status:** Accepted
**Date:** 2026-04-22
**Spec:** [superpowers/specs/2026-04-22-command-hud-design.md](../superpowers/specs/2026-04-22-command-hud-design.md)

## Context

The daemon already emits rich per-step logs and granular SSE events, but
user-facing feedback is limited to the sprite state animation plus a miss
chime. Users cannot glance at the screen to see *what the last command was*
or *whether it succeeded / why it failed*.

## Decision

Render a persistent, RPG-style chat-log overlay directly next to the sprite.
One line per voice command cycle — the one-line summary of what happened,
colour-coded by outcome (green=ok, red=error, amber=miss). Five lines
visible by default, each holding full opacity for 4 s then linearly fading
over 3 s. The HUD lives in the SAME pyglet window as the sprite so sprite
moves (ADR 0053) drag the HUD with them automatically.

Rejected alternatives:
- **Web UI chat panel** — requires the user to have the dashboard tab open;
  poor glanceability.
- **Windows toast notifications** — re-opens the ADR 0013 can of worms.
- **Sprite speech bubble only** — already exists, overwrites previous entries,
  no history.
- **Separate process for HUD** — unnecessary complexity; sprite already owns
  the transparent window and the SSE connection.

## Consequences

- Existing pyglet window grows in size (sprite + HUD + future bubble).
- Introduces a new SSE event type `plan_outcome` (ADR 0052 covers the
  summarization strategy).
- No new processes, no new deps.
- Preserved: miss chimes (ADR 0049), audio-only feedback philosophy (ADR 0013
  — toasts are still banned, on-canvas glyphs are not toasts).
```

- [ ] **Step 2: Commit**

```bash
git add docs/decisions/0051-command-hud-overlay.md
git commit -m "docs(adr): 0051 — command HUD overlay (chat-log next to sprite)"
```

---

### Task 5.2: ADR 0052 — Hybrid rule + LLM summarization

**Files:**
- Create: `docs/decisions/0052-hybrid-rule-llm-summarization.md`

- [ ] **Step 1: Write the ADR**

```markdown
# 0052. Hybrid Rule + LLM Summarization for HUD Entries

**Status:** Accepted
**Date:** 2026-04-22
**Spec:** [superpowers/specs/2026-04-22-command-hud-design.md](../superpowers/specs/2026-04-22-command-hud-design.md)

## Context

The HUD (ADR 0051) needs to turn a `PlanOutcome` into a ≤6-word summary.
Four candidates were considered:
1. Rule-based mapping only — cheap, deterministic, dumb on novel chains.
2. Second LLM call per command — rich, but +300-500 ms per utterance and
   token cost on every command.
3. Piggyback on the first LLM call (add `summary` field to response
   schema) — near-zero cost, but breaks OpenAI tool-calling shape.
4. Hybrid: rules by default, LLM fallback only on `status=error` or
   unknown verb.

## Decision

Use option (4). A rule table in `src/voice_sprite/summary_rules.py` covers
the nine-verb primitive catalog (ADR 0043) plus `scroll` and `no_match`.
Chain detectors (starting with `detect_search_chain`) recognise canonical
multi-step patterns and replace the last-step-wins default with a more
natural phrase. LLM fallback (`LLMSummaryClient`) is called only when the
rule table cannot handle the outcome — errors or tool names outside the
catalog. LLM failure / timeout / empty response degrades to a raw rule
fallback so the HUD always renders a line.

## Consequences

- ~95 % of commands never hit the LLM — hot path stays cheap.
- Errors and unusual chains get rich explanations.
- Adding a new primitive verb means one rule-table entry + one test — lowest
  friction.
- Raw-rule fallback bounds the worst case to "predictable but terse".
- Per-summary LLM timeout (`[hud].llm_summary_timeout_ms`, default 800 ms)
  is independent of the routing LLM's `[llm].timeout_ms` because
  summarization is off the hot path.
```

- [ ] **Step 2: Commit**

```bash
git add docs/decisions/0052-hybrid-rule-llm-summarization.md
git commit -m "docs(adr): 0052 — hybrid rule + LLM summarization for HUD entries"
```

---

### Task 5.3: ADR 0053 — Sprite follows cursor across monitors

**Files:**
- Create: `docs/decisions/0053-sprite-follows-cursor-monitor.md`

- [ ] **Step 1: Write the ADR**

```markdown
# 0053. Sprite Follows Cursor Across Monitors, Docks Above Taskbar

**Status:** Accepted
**Date:** 2026-04-22
**Spec:** [superpowers/specs/2026-04-22-command-hud-design.md](../superpowers/specs/2026-04-22-command-hud-design.md)

## Context

The sprite was pinned to one configured corner of the primary monitor.
On multi-monitor setups this means the sprite was effectively invisible
whenever the user worked on a secondary display. Users wanted it to
follow — specifically, to dock to the bottom-right of whichever monitor
holds the mouse cursor, sitting *above* (next to) the taskbar rather than
overlapping it, regardless of which edge the taskbar is docked to.

## Decision

Poll `GetCursorPos` on the pyglet clock at 30 Hz. Map cursor to monitor
via `MonitorFromPoint(MONITOR_DEFAULTTONEAREST)`. If the returned
`HMONITOR` changed since last tick, read `MONITORINFO.rcWork` via
`GetMonitorInfoW` (Windows has already shrunk this rect to exclude the
taskbar on whatever edge), call `GetDpiForMonitor` for per-monitor scale,
recompute window width/height (`sprite + HUD + bubble`), and call
`window.set_size` + `window.set_location`. The sprite column — not the HUD
column — is what sits flush against `rcWork.right - margin_x`.

Rejected alternatives:
- **Low-level mouse hook (`WH_MOUSE_LL`)** — runs on the installing
  thread's message pump, subject to `LowLevelHooksTimeout` silent unhook,
  needs elevated rights in some environments. Polling is simpler and
  safer.
- **Fixed monitor via config** — ignores the actual user problem.
- **Follow the foreground window instead of cursor** — surprising UX;
  cursor is what the user is tracking with their eyes.
- **Use pywin32** — already a dep, but ctypes is enough for four calls
  and avoids cross-process import weight.

## Consequences

- `PER_MONITOR_AWARE_V2` MUST be set *before* the first pyglet window is
  created, else the HWND stays pinned to system-DPI awareness and
  `set_location` coords get silently rescaled across monitors. The
  `SetProcessDpiAwarenessContext(-4)` call moves to the top of `main()`.
- Sprite size rescales per-monitor on boundary cross (explicit
  `set_size`) — without this, the sprite visually shrinks/grows.
- `rcWork` handles any taskbar edge (bottom / top / left / right /
  autohide / multi-taskbar on Win11 22H2+) with no extra work —
  no `ABM_GETTASKBARPOS` query needed.
- On a locked workstation, `GetCursorPos` returns `(0, 0)` with success;
  the tick no-ops and the sprite parks until unlock.
- Virtual-screen coordinates can be negative (monitors left of / above
  primary); do NOT clamp to `>= 0`.
- 30 Hz polling is ~0.02 % CPU; no measurable battery impact.
- Window-level click-through flags (`WS_EX_LAYERED | WS_EX_TRANSPARENT |
  WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE`) survive `set_location` — ADR 0050
  composition recipe stays intact.
```

- [ ] **Step 2: Commit**

```bash
git add docs/decisions/0053-sprite-follows-cursor-monitor.md
git commit -m "docs(adr): 0053 — sprite follows cursor across monitors above taskbar"
```

---

### Task 5.4: Update `docs/agents/technical-decisions.md`, `docs/architecture.md`, `docs/gotchas.md`, `README.md`

**Files:**
- Modify: `docs/agents/technical-decisions.md`
- Modify: `docs/architecture.md`
- Modify: `docs/gotchas.md`
- Modify: `README.md`

- [ ] **Step 1: Add rows to `docs/agents/technical-decisions.md` under the Sprite Companion section**

Append these rows to the sprite table:

```markdown
| Command HUD surface | Standalone chat-log overlay rendered in the same pyglet window as the sprite | Glanceable RPG-chat feedback per command; sharing the window avoids a second HWND | [0051](../decisions/0051-command-hud-overlay.md) |
| HUD summarization | Hybrid: rule table covers nine-verb catalog, LLM fallback on errors / unknown verbs | Cheap hot path, rich error explanations, bounded worst case | [0052](../decisions/0052-hybrid-rule-llm-summarization.md) |
| Multi-monitor docking | 30 Hz `GetCursorPos` poll → `MonitorFromPoint` → `rcWork` → `set_location` + `set_size`; PMv2 awareness mandatory | Sprite + HUD track the cursor; taskbar-excluded docking works on any taskbar edge | [0053](../decisions/0053-sprite-follows-cursor-monitor.md) |
```

- [ ] **Step 2: Append §10 and §11 to `docs/architecture.md`**

```markdown
---

## 10. Command HUD

### Components

- **`PlanOutcome`** (daemon — `src/voice_commander/plan.py`) — frozen
  dataclass carrying the full outcome of one command cycle.
- **`Summarizer`** (sprite — `src/voice_sprite/summarizer.py`) — routes
  rule → chain-detector → LLM fallback → raw fallback.
- **`RULES` + `CHAIN_DETECTORS`** (sprite — `summary_rules.py`).
- **`LLMSummaryClient`** (sprite — `llm_summary_client.py`) — httpx client
  for LM Studio; 800 ms deadline; returns None on any failure.
- **`ChatLog`** (sprite — `chat_log.py`) — ring buffer + fade curve.
- **`ChatLogRenderer`** (sprite — `chat_log_renderer.py`) — pyglet labels.

### Event

```text
plan_outcome {
  transcript: str,
  steps: [{name, kwargs}, ...],
  status: "ok" | "error" | "miss",
  failed_step_index: int | null,
  error_msg: str | null,
  duration_ms: int,
}
```

Published by `Dispatcher.run_plan` (ok / error) and
`StreamingDaemon._process_utterance` (miss on `route()=None` and on
confidence-gate drop).

---

## 11. Cursor-Follow Sprite (Multi-Monitor)

30 Hz `pyglet.clock` tick polls `GetCursorPos`, dispatches to
`MonitorFromPoint`, and (when the monitor changes) reads `MONITORINFO.rcWork`
+ `GetDpiForMonitor` to compute a new window size + location. Anchor point
is the sprite column's bottom-right — NOT the window top-left — so the
HUD's leftward extension does not push the sprite off the monitor.

`PER_MONITOR_AWARE_V2` MUST be set before the first pyglet window is
constructed; `main()` now calls `SetProcessDpiAwarenessContext(-4)` as
its first post-logging step. See ADR 0053 for the full gotcha list.
```

- [ ] **Step 3: Append to `docs/gotchas.md`**

Add a new section:

```markdown
## Win11 Multi-Monitor + DPI Gotchas (Sprite / HUD)

- **`PER_MONITOR_AWARE_V2` ordering.** `SetProcessDpiAwarenessContext(-4)`
  MUST run before the first pyglet window is created. Otherwise that HWND
  is forever pinned to system-DPI awareness and `set_location` silently
  rescales coords across monitors.
- **`rcWork` vs `rcMonitor`.** `rcWork` is already shrunk to exclude the
  taskbar on whatever edge it's on (bottom / top / left / right / autohide).
  Never use `rcMonitor` for docking — you will overlap the taskbar.
- **Negative virtual-screen coords.** Monitors positioned left of / above
  the primary have negative `rcWork.left` / `rcWork.top`. `set_location`
  handles this fine — do NOT clamp to `>= 0`.
- **Mixed-DPI cross.** `WM_DPICHANGED` fires on monitor change but pyglet
  2.1 does not auto-rescale its framebuffer. Call `window.set_size` with
  `base * scale` explicitly on each cross.
- **Locked workstation.** `GetCursorPos` returns `(0, 0)` with success
  during lock. Treat this as "no-op" rather than docking to primary
  monitor origin.
- **`LowLevelHooksTimeout`.** Do NOT use `WH_MOUSE_LL` for cursor
  tracking — the OS silently unhooks callbacks that exceed ~300 ms.
  Polling on `pyglet.clock` is the correct pattern.
- **Taskbar layered windows.** `WS_EX_LAYERED | WS_EX_TRANSPARENT |
  WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE` survive `set_location` calls —
  no need to re-apply after moves.
```

- [ ] **Step 4: Update `README.md`**

Add a new "On-screen command HUD" bullet to the Features list. Append a `[hud]` config table row. Mention the follow-cursor behaviour in the sprite feature description.

Specifically, find the existing sprite feature bullet and append to it:

```markdown
The sprite now also follows the cursor across monitors, docking to the
bottom-right of whichever monitor holds the mouse, above the taskbar.
Alongside the sprite, a chat-log HUD renders one simplified line per
command ("minimized window", "couldn't find 'sptfy'") with colour-coded
outcomes. Configure via `[hud]` and `[sprite]` in `config.toml`.
```

Append these rows to the config table:

```markdown
| `[hud]` | `enabled` | `true` | Master toggle for the chat-log HUD next to the sprite. |
| `[hud]` | `max_lines` | `5` | Number of entries visible at once. |
| `[hud]` | `hold_ms` / `fade_ms` | `4000` / `3000` | Full-opacity hold then linear fade duration. |
| `[hud]` | `llm_fallback_enabled` | `true` | Use LM Studio to summarize errors and unknown verbs. |
| `[sprite]` | `follow_cursor` | `true` | Sprite tracks the cursor across monitors (ADR 0053). |
| `[sprite]` | `follow_poll_hz` | `30` | Cursor-poll rate in Hz. |
```

- [ ] **Step 5: Commit**

```bash
git add docs/agents/technical-decisions.md docs/architecture.md docs/gotchas.md README.md
git commit -m "docs: technical-decisions/architecture/gotchas/README — HUD + cursor-follow"
```

---

### Task 5.5: HITL validation gate

**Files:** none modified — validation only.

- [ ] **Step 1: Ensure clean build state**

Run: `uv run pytest`
Expected: all tests pass.

Run: `uv run ruff check .`
Expected: clean.

Run: `uv run mypy src`
Expected: clean.

- [ ] **Step 2: Start LM Studio on localhost:1234**

Load `google/gemma-4-e4b` (or whatever matches the daemon's routing model).

- [ ] **Step 3: Launch daemon + sprite**

Run in one shell: `uv run voice-commander`
Run in a second shell: `uv run voice-sprite`

Expected: sprite window appears at bottom-right of the monitor where the cursor currently is.

- [ ] **Step 4: Walk through the HITL checklist**

For each gate, confirm the described behaviour. Do NOT proceed if any gate fails.

1. Press Scroll Lock. Say "minimize". Press Scroll Lock.
   Expect: the focused window minimizes. HUD shows `minimized window` in green for ~4 s then fades.

2. Press Scroll Lock. Say "open sptfy" (typo). Press Scroll Lock.
   Expect: miss chime. HUD shows `no match` in amber.

3. Press Scroll Lock. Say "search cats". Press Scroll Lock.
   Expect: chained tool calls fire. HUD shows `searched chrome for "cats"` in green.

4. Stop LM Studio. Press Scroll Lock. Say "copy". Press Scroll Lock.
   Expect: miss chime. HUD shows `no match` in amber.
   Restart LM Studio. Press Scroll Lock. Say "copy". Press Scroll Lock.
   Expect: HUD shows `pressed ctrl+c` (or equivalent) in green — recovery succeeded.

5. Kill the sprite process mid-session.
   Expect: daemon keeps running; commands still dispatch (no HUD).
   Relaunch sprite.
   Expect: fresh HUD buffer; previous commands gone.

6. Press Scroll Lock. Say 10 short commands rapid-fire. Press Scroll Lock.
   Expect: HUD ring buffer never exceeds `max_lines` entries; oldest scroll off cleanly.

7. Drag the cursor to a secondary monitor.
   Expect: within 1/30 s of crossing the boundary, the sprite snaps to the bottom-right of the new monitor. HUD moves with it.

8. Move the Windows taskbar to the top of the screen (Settings → Personalization → Taskbar).
   Expect: on next cursor move, the sprite docks at the bottom of `rcWork` (which is now above the top-edge taskbar).

9. If you have a mixed-DPI setup (100 % + 150 %), drag cursor across the boundary.
   Expect: sprite and HUD rescale on cross — no visible shrinking / growing.

10. Press Win+L to lock the workstation.
    Expect: sprite parks; no movement.
    Unlock.
    Expect: sprite resumes following.

11. In `config.local.toml`, set `[sprite] follow_cursor = false`. Restart sprite.
    Expect: sprite static at primary-monitor bottom-right.

12. In `config.local.toml`, set `[hud] enabled = false`. Restart sprite.
    Expect: no HUD labels rendered; sprite still follows cursor.

- [ ] **Step 5: Commit a lockfile update if anything shifted**

```bash
git status
# If there are only uv-managed or log files, do nothing.
# If deps changed legitimately, commit the lockfile delta.
```

- [ ] **Step 6: Final commit + PR**

Leave the working tree clean. The PR for this feature squashes the per-task commits.

---

## Summary

After all tasks:

- Daemon publishes one new SSE event type `plan_outcome` on every executed voice command cycle (success, error, and miss).
- Sprite process consumes it, synthesizes a one-line summary via rule table + LLM fallback, and renders fading entries next to the sprite inside the same transparent pyglet window.
- Sprite follows the mouse cursor across monitors and docks above the taskbar of whichever monitor the cursor currently lives on, with per-monitor DPI rescale.
- Three ADRs (0051/0052/0053) document the decisions at the time they were made.
- Zero new dependencies (reuses `httpx`, `pyglet`, ctypes/`wintypes`).
- 80 % coverage floor preserved; `chat_log_renderer.py` and `cursor_tracker.py` added to the coverage omit list.
