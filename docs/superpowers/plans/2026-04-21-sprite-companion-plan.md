# Sprite Companion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a live on-screen sprite companion (separate process) that mirrors daemon state via SSE events — transparent, always-on-top, click-through pyglet window with sprite-sheet animations.

**Architecture:** Daemon-side EventBus publishes state changes to a new FastAPI SSE endpoint (`GET /events`). A separate `voice_sprite` process connects via httpx-sse, drives a state machine, and renders the active pose in a pyglet window with Win32 click-through/topmost flags.

**Tech Stack:** pyglet 2.x, httpx + httpx-sse, FastAPI StreamingResponse, ctypes (Win32), asyncio

**Design decisions locked in this plan:**
- File-watch for hot reload: **pyglet.clock mtime poll** (no watchdog dep — simpler, sufficient for art iteration)
- `events_dropped` counter: **log-only in MVP** (no /metrics endpoint)
- Charsheet art: **placeholder PNG generated in tests** — real art ships later via prompt template

---

## File Structure

### New files to CREATE

**Daemon side:**
| File | Responsibility |
|------|---------------|
| `src/voice_commander/event_bus.py` | In-daemon pub/sub broker with bounded async queues |
| `tests/unit/test_event_bus.py` | EventBus unit tests |
| `tests/integration/test_sse_endpoint.py` | FastAPI test client subscribes /events, asserts SSE frames |

**Sprite side:**
| File | Responsibility |
|------|---------------|
| `src/voice_sprite/__init__.py` | Package marker |
| `src/voice_sprite/__main__.py` | CLI entry: parse args, load config, start pyglet loop |
| `src/voice_sprite/config.py` | TOML load with config.local.toml overlay |
| `src/voice_sprite/event_client.py` | httpx-sse consumer → calls on_event callback |
| `src/voice_sprite/state_machine.py` | 11 states + transition graph + heartbeat timeout |
| `src/voice_sprite/charsheet.py` | TOML parser + PNG bounds validator |
| `src/voice_sprite/sprite_renderer.py` | Sheet slicing, animation scheduler, frame tick |
| `src/voice_sprite/window.py` | pyglet Window subclass (transparent, topmost, click-through) |
| `src/voice_sprite/speech_bubble.py` | Fading last-command label |
| `src/voice_sprite/dpi.py` | ctypes GetDpiForMonitor wrapper |
| `src/voice_sprite/win32_flags.py` | ctypes WS_EX_LAYERED + TRANSPARENT + TOOLWINDOW + NOACTIVATE |
| `tests/unit/test_state_machine.py` | State machine unit tests |
| `tests/unit/test_speech_bubble.py` | Speech bubble unit tests |
| `tests/unit/test_event_client.py` | SSE client unit tests (mocked httpx) |
| `tests/unit/test_charsheet.py` | Charsheet validator unit tests |
| `tests/unit/test_dpi.py` | DPI wrapper unit tests (mocked ctypes) |
| `tests/unit/test_sprite_config.py` | Sprite config loading unit tests |

**Assets:**
| File | Responsibility |
|------|---------------|
| `assets/sprite/charsheet.toml` | Metadata: frame size, rows, per-state/transition anims |
| `assets/sprite/README.md` | Nano-banana prompt template for art regeneration |

**Docs:**
| File | Responsibility |
|------|---------------|
| `docs/decisions/0040-sprite-separate-process-via-sse.md` | ADR: sprite as separate process |
| `docs/decisions/0041-pyglet-over-tkinter-pyqt-web-overlay.md` | ADR: pyglet choice |
| `docs/decisions/0042-charsheet-grid-format-sidecar-toml.md` | ADR: char sheet format |
| `docs/decisions/0043-eventbus-sse-outbound-telemetry.md` | ADR: EventBus + SSE |
| `docs/decisions/0044-miss-chimes-retained.md` | ADR: audio chimes stay |

### Files to MODIFY

| File | What changes |
|------|-------------|
| `src/voice_commander/config.py` | Add `SpriteConfig` dataclass, wire into `Config` |
| `src/voice_commander/web/app.py` | Add `/events` SSE endpoint, accept `event_bus` param |
| `src/voice_commander/daemon.py` | Add event_bus field, heartbeat thread, publish calls at all touchpoints |
| `src/voice_commander/dispatcher.py` | Add event_bus param, publish `tool_fired`/`miss`/`tool_error` |
| `pyproject.toml` | Add pyglet, httpx-sse deps, `voice-sprite` entry point, coverage omit |
| `config.toml` | Add `[sprite]` section with defaults |
| `start.ps1` | Add `-NoSprite` flag, sprite subprocess spawn after daemon |
| `docs/architecture.md` | Add EventBus + sprite process sections |
| `tests/unit/test_config.py` | Add SpriteConfig round-trip tests |
| `tests/integration/test_daemon_factory.py` | Verify event_bus wiring |

---

## Phase 0: Research & Reference Docs (REQUIRED — do first)

Per CLAUDE.md: *"If a library is new to the repo, land its reference in docs/references/ before writing code against it."* Both pyglet and httpx-sse are new to this repo. No implementation code may land before this phase is complete. Parallelize research with Haiku sub-agents — do not serialize.

### Task 0a: Pyglet 2.x reference doc

**Files:**
- Create: `docs/references/pyglet.md`

**Goal:** Authoritative vendored reference for every pyglet API the plan uses. Any agent implementing Tasks 14–15 must be able to read this doc and write correct code without guessing.

- [ ] **Step 1: Pull upstream docs**

Sources to consult (via Haiku sub-agent / WebFetch — prefer the latest 2.x stable, currently 2.0.x):
- https://pyglet.readthedocs.io/en/latest/programming_guide/windowing.html
- https://pyglet.readthedocs.io/en/latest/programming_guide/image.html (sprite sheets, TextureRegion, TextureGrid, ImageGrid)
- https://pyglet.readthedocs.io/en/latest/programming_guide/graphics.html (Sprite class)
- https://pyglet.readthedocs.io/en/latest/programming_guide/text.html (Label)
- https://pyglet.readthedocs.io/en/latest/programming_guide/time.html (clock.schedule_interval)
- https://pyglet.readthedocs.io/en/latest/programming_guide/app.html (event loop)
- https://pyglet.readthedocs.io/en/latest/modules/window.html (Window API + WINDOW_STYLE_BORDERLESS / WINDOW_STYLE_TRANSPARENT)

- [ ] **Step 2: Verify claims the plan makes**

The plan asserts each of these — each must be verified against upstream docs or a minimal smoke test, and the verified answer written into the reference doc:

| Plan assertion | Verify |
|---|---|
| `WINDOW_STYLE_BORDERLESS` exists on 2.x Window | Check `pyglet.window.Window.WINDOW_STYLE_*` constants list |
| `Window` supports transparent background | Confirm style/flag name, platform caveats |
| Access native HWND via `window._hwnd` on pyglet 2.x Windows backend | Confirm attribute path — may be `._view_hwnd`, `._hwnd`, or require `display.get_platform_window()` |
| `pyglet.image.load(path)` returns `AbstractImage` | Confirm return type + how to slice into grid |
| `pyglet.sprite.Sprite(region, x, y)` renders a texture region at absolute coords | Confirm constructor signature |
| `pyglet.text.Label(...)` for in-window labels | Confirm kwargs actually available |
| `pyglet.clock.schedule_interval(fn, seconds)` | Confirm signature and callback arg (`dt`) |
| `pyglet.app.run()` is blocking, single-threaded | Confirm + threading caveats |

- [ ] **Step 3: Cover the Windows-specific gaps**

Pyglet + Win32 interop isn't documented well upstream. Collect:
- How to get the Win32 HWND from a pyglet 2.x Window reliably.
- Whether pyglet's transparent-background flag works on Windows 11 (or requires custom Win32 flags).
- Known pyglet-on-Windows gotchas (DPI, display probe at import time, multi-monitor layout origin).

- [ ] **Step 4: Write the reference doc**

Use `docs/references/` existing files as style template. Include: canonical import paths, minimal runnable window snippet, sprite-sheet slicing snippet, clock snippet, Win32 HWND retrieval snippet, version compatibility matrix, known issues.

- [ ] **Step 5: Commit**

```bash
git add docs/references/pyglet.md
git commit -m "docs(references): vendor pyglet 2.x reference for sprite companion"
```

### Task 0b: httpx-sse reference doc

**Files:**
- Create: `docs/references/httpx-sse.md`

**Goal:** Vendored reference covering every httpx-sse API the plan uses — connect, iterate, reconnect, timeouts, async client.

- [ ] **Step 1: Pull upstream docs**

Sources (Haiku sub-agent):
- https://github.com/florimondmanca/httpx-sse (README — primary reference)
- https://www.python-httpx.org/async/ (async client basics, since httpx-sse wraps httpx)
- https://www.python-httpx.org/advanced/timeouts/ (timeout semantics)

- [ ] **Step 2: Verify claims the plan makes**

| Plan assertion | Verify |
|---|---|
| `httpx_sse.aconnect_sse(client, "GET", url)` yields an async context manager | Confirm exact API surface + version |
| Event stream has `.aiter_sse()` yielding objects with `.event` and `.data` | Confirm attribute names |
| Reconnect-on-disconnect is the caller's responsibility (not built in) | Confirm — plan relies on this |
| `httpx.AsyncClient(timeout=httpx.Timeout(...))` per-connect timeout works with SSE long-poll | Confirm — SSE streams must not hit read timeout mid-stream |

- [ ] **Step 3: Write the reference doc**

Include: install line, minimal async subscriber snippet, reconnect loop pattern, per-event handling, timeout configuration for long-lived SSE streams, error-type catalog.

- [ ] **Step 4: Commit**

```bash
git add docs/references/httpx-sse.md
git commit -m "docs(references): vendor httpx-sse reference for sprite companion"
```

### Task 0c: Pyglet smoke test

**Files:**
- Create: `scripts/pyglet-smoke.py` (throwaway; delete after Task 15)

**Goal:** Before Task 15 claims "pyglet window works," prove the exact required flags (borderless + transparent + topmost + click-through on Windows 11) work on the target machine. This is the single highest-risk unknown in the plan.

- [ ] **Step 1: Write smoke script**

Tiny script: open a 256×256 borderless transparent pyglet window in the bottom-right corner, apply the Win32 `WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE` via ctypes on the HWND the reference doc describes, draw a red square, run the event loop.

- [ ] **Step 2: Run on target hardware**

Confirm:
- Window appears, background is transparent (desktop visible behind red square).
- Window stays above other windows.
- Clicking on the red square passes the click through to the app beneath.
- Window does not grab taskbar focus.

- [ ] **Step 3: Record findings**

Append a "Smoke-test results" section to `docs/references/pyglet.md` with the exact working flag combination and any deviations from what the plan assumes.

- [ ] **Step 4: Commit**

```bash
git add scripts/pyglet-smoke.py docs/references/pyglet.md
git commit -m "chore(sprite): pyglet Win32 click-through flags smoke test"
```

### Task 0d: Reconcile plan with research findings

- [ ] **Step 1: Re-read Tasks 14 and 15**

If the reference docs or smoke test contradict any snippet in Tasks 14 (sprite renderer) or 15 (pyglet window), patch those tasks in this plan file BEFORE starting Phase 1. Record reconciliation notes inline.

- [ ] **Step 2: Commit plan amendments if any**

```bash
git add docs/superpowers/plans/2026-04-21-sprite-companion-plan.md
git commit -m "docs(plan): reconcile sprite companion plan with pyglet/httpx-sse research"
```

Phase 0 exit gate: `docs/references/pyglet.md` and `docs/references/httpx-sse.md` exist on the branch; pyglet smoke test passed on target hardware; Tasks 14 & 15 reflect verified APIs. Only then may Phase 1 begin.

---

## Phase 1: Daemon-Side EventBus + SSE

### Task 1: Add SpriteConfig to config system

**Files:**
- Modify: `src/voice_commander/config.py:80-125`
- Modify: `config.toml`
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write failing test for SpriteConfig parsing**

```python
# Append to tests/unit/test_config.py

def test_sprite_config_defaults():
    cfg = Config.load(Path("/nonexistent.toml"))
    assert cfg.sprite.enabled is True
    assert cfg.sprite.corner == "bottom_right"
    assert cfg.sprite.base_size_px == 128
    assert cfg.sprite.offset_x == 16
    assert cfg.sprite.offset_y == 16
    assert cfg.sprite.asset_path == "assets/sprite"
    assert cfg.sprite.bubble_fade_ms == 2000
    assert cfg.sprite.heartbeat_timeout_ms == 3000


def test_sprite_config_override(tmp_path):
    toml_file = tmp_path / "config.toml"
    toml_file.write_text('[sprite]\ncorner = "top_left"\nbase_size_px = 64\n')
    cfg = Config.load(toml_file)
    assert cfg.sprite.corner == "top_left"
    assert cfg.sprite.base_size_px == 64
    assert cfg.sprite.enabled is True  # default preserved


def test_sprite_config_invalid_key(tmp_path):
    toml_file = tmp_path / "config.toml"
    toml_file.write_text("[sprite]\nbogus = 42\n")
    with pytest.raises(ValueError, match="Unknown config key 'bogus'"):
        Config.load(toml_file)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_config.py::test_sprite_config_defaults -v`
Expected: FAIL — `Config` has no `sprite` attribute

- [ ] **Step 3: Implement SpriteConfig**

Add to `src/voice_commander/config.py` after `LLMRouterConfig`:

```python
@dataclass(frozen=True)
class SpriteConfig:
    enabled: bool = True
    corner: str = "bottom_right"
    base_size_px: int = 128
    offset_x: int = 16
    offset_y: int = 16
    asset_path: str = "assets/sprite"
    bubble_fade_ms: int = 2000
    heartbeat_timeout_ms: int = 3000
```

Add `sprite` field to `Config`:

```python
@dataclass(frozen=True)
class Config:
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    matching: MatchingConfig = field(default_factory=MatchingConfig)
    feedback: FeedbackConfig = field(default_factory=FeedbackConfig)
    vad: VadConfig = field(default_factory=VadConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    web: WebConfig = field(default_factory=WebConfig)
    llm_router: LLMRouterConfig = field(default_factory=LLMRouterConfig)
    sprite: SpriteConfig = field(default_factory=SpriteConfig)
```

Add sprite parsing to `Config.load()` — add after the `llm_router` line:

```python
            sprite=_section(SpriteConfig, raw.get("sprite", {})),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_config.py -v`
Expected: ALL PASS

- [ ] **Step 5: Add [sprite] section to config.toml**

Append to `config.toml`:

```toml
[sprite]
enabled = true
corner = "bottom_right"
base_size_px = 128
offset_x = 16
offset_y = 16
asset_path = "assets/sprite"
bubble_fade_ms = 2000
heartbeat_timeout_ms = 3000
```

- [ ] **Step 6: Commit**

```bash
git add src/voice_commander/config.py config.toml tests/unit/test_config.py
git commit -m "feat(config): add SpriteConfig section for sprite companion"
```

---

### Task 2: EventBus — pub/sub broker

**Files:**
- Create: `src/voice_commander/event_bus.py`
- Create: `tests/unit/test_event_bus.py`

- [ ] **Step 1: Write failing tests for EventBus**

```python
# tests/unit/test_event_bus.py
from __future__ import annotations

import asyncio

import pytest

from voice_commander.event_bus import Event, EventBus


def _drain(q: asyncio.Queue[Event]) -> list[Event]:
    """Drain all events from an asyncio.Queue synchronously."""
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    return events


def test_publish_and_subscribe():
    bus = EventBus()
    q = bus.subscribe()
    bus.publish("session_started")
    events = _drain(q)
    assert len(events) == 1
    assert events[0].type == "session_started"
    assert events[0].id == 1


def test_publish_with_data():
    bus = EventBus()
    q = bus.subscribe()
    bus.publish("tool_fired", {"name": "copy"})
    events = _drain(q)
    assert events[0].data == {"name": "copy"}


def test_multiple_subscribers():
    bus = EventBus()
    q1 = bus.subscribe()
    q2 = bus.subscribe()
    bus.publish("miss")
    assert _drain(q1)[0].type == "miss"
    assert _drain(q2)[0].type == "miss"


def test_unsubscribe():
    bus = EventBus()
    q = bus.subscribe()
    bus.unsubscribe(q)
    bus.publish("miss")
    assert _drain(q) == []


def test_monotonic_ids():
    bus = EventBus()
    q = bus.subscribe()
    bus.publish("a")
    bus.publish("b")
    bus.publish("c")
    events = _drain(q)
    assert [e.id for e in events] == [1, 2, 3]


def test_ring_buffer_replay():
    bus = EventBus(max_buffer=5)
    q = bus.subscribe()
    for i in range(10):
        bus.publish(f"e{i}")
    _drain(q)  # flush subscriber queue
    replayed = bus.replay_after(7)
    assert [e.id for e in replayed] == [8, 9, 10]


def test_subscriber_queue_overflow_drops_oldest():
    bus = EventBus(max_subscriber_queue=2)
    q = bus.subscribe()
    bus.publish("a")
    bus.publish("b")
    bus.publish("c")  # should drop "a"
    events = _drain(q)
    assert len(events) == 2
    assert events[0].type == "b"
    assert events[1].type == "c"
    assert bus.events_dropped == 1


def test_no_subscribers_no_error():
    bus = EventBus()
    bus.publish("orphan")  # should not raise


def test_replay_after_zero_returns_all_buffered():
    bus = EventBus(max_buffer=100)
    bus.publish("x")
    bus.publish("y")
    replayed = bus.replay_after(0)
    assert len(replayed) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_event_bus.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement EventBus**

```python
# src/voice_commander/event_bus.py
from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Event:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    id: int = 0


class EventBus:
    """Thread-safe pub/sub broker with bounded per-subscriber async queues.

    Designed for daemon → SSE bridge: sync ``publish()`` from any thread,
    async ``subscribe()`` queues consumed by FastAPI SSE generators.
    """

    def __init__(
        self,
        max_buffer: int = 100,
        max_subscriber_queue: int = 1024,
    ) -> None:
        self._subscribers: list[asyncio.Queue[Event]] = []
        self._lock = threading.Lock()
        self._buffer: list[Event] = []
        self._max_buffer = max_buffer
        self._max_subscriber_queue = max_subscriber_queue
        self._next_id = 1
        self._events_dropped = 0

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Non-blocking publish from any thread."""
        with self._lock:
            event = Event(
                type=event_type,
                data=data or {},
                ts=time.time(),
                id=self._next_id,
            )
            self._next_id += 1
            self._buffer.append(event)
            if len(self._buffer) > self._max_buffer:
                self._buffer = self._buffer[-self._max_buffer :]
            for q in self._subscribers:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    # Drop oldest, enqueue new
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    try:
                        q.put_nowait(event)
                    except asyncio.QueueFull:
                        pass
                    self._events_dropped += 1

    def subscribe(self) -> asyncio.Queue[Event]:
        """Create a new subscriber queue."""
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._max_subscriber_queue)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[Event]) -> None:
        """Remove a subscriber queue."""
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def replay_after(self, last_id: int) -> list[Event]:
        """Return buffered events with id > last_id (for SSE reconnect)."""
        with self._lock:
            return [e for e in self._buffer if e.id > last_id]

    @property
    def events_dropped(self) -> int:
        return self._events_dropped
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_event_bus.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/event_bus.py tests/unit/test_event_bus.py
git commit -m "feat: add EventBus pub/sub broker for daemon→SSE bridge"
```

---

### Task 3: SSE endpoint on FastAPI

**Files:**
- Modify: `src/voice_commander/web/app.py:22-29`
- Create: `tests/integration/test_sse_endpoint.py`

- [ ] **Step 1: Write failing integration test**

```python
# tests/integration/test_sse_endpoint.py
from __future__ import annotations

import json
import threading

import pytest
from fastapi.testclient import TestClient

from voice_commander.event_bus import EventBus
from voice_commander.registry import ToolRegistry
from voice_commander.tool_metadata import ToolMetadataStore
from voice_commander.web.app import create_app


@pytest.fixture()
def event_bus():
    return EventBus(max_buffer=100, max_subscriber_queue=64)


@pytest.fixture()
def client(event_bus, tmp_path):
    registry = ToolRegistry()
    store = ToolMetadataStore(tmp_path)
    lock = threading.Lock()
    app = create_app(registry, store, lock, event_bus=event_bus)
    return TestClient(app)


def test_sse_receives_published_event(client, event_bus):
    """Publish an event, then read it from the SSE stream."""
    event_bus.publish("session_started")
    with client.stream("GET", "/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        # Read first SSE frame (the replayed event)
        lines = []
        for chunk in response.iter_lines():
            lines.append(chunk)
            if chunk == "":  # empty line = end of SSE frame
                break
        text = "\n".join(lines)
        assert "event: session_started" in text
        assert '"ts"' in text


def test_sse_event_id_increments(client, event_bus):
    event_bus.publish("a")
    event_bus.publish("b")
    with client.stream("GET", "/events") as response:
        frames = []
        line_buf = []
        for chunk in response.iter_lines():
            line_buf.append(chunk)
            if chunk == "":
                frames.append("\n".join(line_buf))
                line_buf = []
                if len(frames) >= 2:
                    break
        assert "id: 1" in frames[0]
        assert "id: 2" in frames[1]


def test_sse_last_event_id_replay(client, event_bus):
    """Reconnect with Last-Event-ID should replay missed events."""
    event_bus.publish("a")
    event_bus.publish("b")
    event_bus.publish("c")
    with client.stream("GET", "/events", headers={"Last-Event-ID": "1"}) as response:
        frames = []
        line_buf = []
        for chunk in response.iter_lines():
            line_buf.append(chunk)
            if chunk == "":
                frames.append("\n".join(line_buf))
                line_buf = []
                if len(frames) >= 2:
                    break
        # Should get events 2 and 3 (after id=1)
        assert "id: 2" in frames[0]
        assert "id: 3" in frames[1]


def test_sse_no_event_bus_returns_503(tmp_path):
    registry = ToolRegistry()
    store = ToolMetadataStore(tmp_path)
    lock = threading.Lock()
    app = create_app(registry, store, lock, event_bus=None)
    client = TestClient(app)
    response = client.get("/events")
    assert response.status_code == 503


@pytest.mark.integration
def test_sse_keepalive(client, event_bus):
    """After 30s timeout, server sends a keepalive comment."""
    # This is hard to test without waiting 30s; just verify stream starts
    with client.stream("GET", "/events") as response:
        assert response.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/integration/test_sse_endpoint.py -v`
Expected: FAIL — `create_app()` doesn't accept `event_bus`

- [ ] **Step 3: Add SSE endpoint to FastAPI app**

Modify `src/voice_commander/web/app.py`:

Add imports at top:

```python
import asyncio
import json as json_mod

from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
```

Change `create_app` signature to accept `event_bus`:

```python
def create_app(
    registry: ToolRegistry,
    store: ToolMetadataStore,
    reload_lock: threading.Lock,
    event_bus: "EventBus | None" = None,
) -> FastAPI:
```

Add the `/events` route inside `create_app()`, after the `/healthz` route:

```python
    # ------------------------------------------------------------------
    # GET /events — Server-Sent Events stream for sprite companion
    # ------------------------------------------------------------------

    @app.get("/events")
    async def events(request: Request) -> StreamingResponse | JSONResponse:
        if event_bus is None:
            return JSONResponse({"error": "EventBus not configured"}, status_code=503)

        last_id_str = request.headers.get("Last-Event-ID", "0")
        try:
            last_id = int(last_id_str)
        except ValueError:
            last_id = 0

        q = event_bus.subscribe()

        async def generate():  # type: ignore[return]
            try:
                # Replay missed events from ring buffer
                for ev in event_bus.replay_after(last_id):
                    yield (
                        f"id: {ev.id}\n"
                        f"event: {ev.type}\n"
                        f"data: {json_mod.dumps(ev.data | {'ts': ev.ts})}\n\n"
                    )
                # Stream new events
                while True:
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=30.0)
                        yield (
                            f"id: {ev.id}\n"
                            f"event: {ev.type}\n"
                            f"data: {json_mod.dumps(ev.data | {'ts': ev.ts})}\n\n"
                        )
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                event_bus.unsubscribe(q)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
```

Add a forward-reference import guard at the module level (avoid circular import):

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..event_bus import EventBus
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/integration/test_sse_endpoint.py -v`
Expected: ALL PASS

- [ ] **Step 5: Run existing web tests to verify no regression**

Run: `pytest tests/integration/test_ui_end_to_end.py -v`
Expected: ALL PASS (existing tests pass `event_bus=None` implicitly via default)

- [ ] **Step 6: Commit**

```bash
git add src/voice_commander/web/app.py tests/integration/test_sse_endpoint.py
git commit -m "feat: add SSE /events endpoint for sprite companion"
```

---

### Task 4: Wire EventBus into Dispatcher

**Files:**
- Modify: `src/voice_commander/dispatcher.py`
- Modify: `tests/unit/test_dispatcher.py`

- [ ] **Step 1: Write failing test for dispatcher event publishing**

Append to `tests/unit/test_dispatcher.py`:

```python
from voice_commander.event_bus import EventBus


def _drain_events(bus):
    q = bus.subscribe()
    # Events already published won't be in new subscriber — use replay
    return bus.replay_after(0)


def test_dispatch_match_publishes_tool_fired():
    bus = EventBus()
    sink = CapturingFeedbackSink()
    d = Dispatcher(sink, event_bus=bus)
    tool = _make_tool("copy")
    match = MatchResult(tool=tool, phrase="copy", score=95.0, candidates=())
    d.dispatch("copy", match)
    events = bus.replay_after(0)
    assert any(e.type == "tool_fired" and e.data["name"] == "copy" for e in events)


def test_dispatch_miss_publishes_miss():
    bus = EventBus()
    sink = CapturingFeedbackSink()
    d = Dispatcher(sink, event_bus=bus)
    match = MatchResult(tool=None, phrase=None, score=0.0, candidates=())
    d.dispatch("gibberish", match)
    events = bus.replay_after(0)
    assert any(e.type == "miss" for e in events)


def test_dispatch_tool_error_publishes_tool_error():
    bus = EventBus()
    sink = CapturingFeedbackSink()
    d = Dispatcher(sink, event_bus=bus)

    def bad_func():
        raise RuntimeError("boom")

    tool = ToolEntry(
        name="boom", phrases=("boom",), func=bad_func,
        module="test", docstring=None,
    )
    match = MatchResult(tool=tool, phrase="boom", score=95.0, candidates=())
    d.dispatch("boom", match)
    events = bus.replay_after(0)
    assert any(e.type == "tool_error" and e.data["name"] == "boom" for e in events)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_dispatcher.py::test_dispatch_match_publishes_tool_fired -v`
Expected: FAIL — `Dispatcher.__init__()` doesn't accept `event_bus`

- [ ] **Step 3: Add event_bus to Dispatcher**

Modify `src/voice_commander/dispatcher.py`:

```python
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from .feedback import FeedbackSink
from .matcher import MatchResult
from .plan import Plan
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

    def _publish(self, event_type: str, data: dict | None = None) -> None:
        if self._event_bus is not None:
            self._event_bus.publish(event_type, data)

    def dispatch(self, transcript: str, match: MatchResult) -> None:
        if match.tool is None or match.phrase is None:
            self._feedback.on_miss(transcript, match.candidates)
            self._publish("miss", {"transcript": transcript})
            return
        self._feedback.on_match(match.tool.name, match.phrase, match.score)
        try:
            match.tool.func()
            self._publish("tool_fired", {"name": match.tool.name})
        except Exception as e:
            self._feedback.on_error(f"tool:{match.tool.name}", e)
            self._publish("tool_error", {"name": match.tool.name, "msg": str(e)})

    def run_plan(self, transcript: str, plan: Plan, registry: ToolRegistry) -> None:
        """Execute a multi-step plan from the LLM router."""
        self._feedback.on_plan_start(transcript, len(plan.steps))
        executed = 0
        for step in plan.steps:
            tool = registry.by_name(step.name)
            if tool is None:
                self._feedback.on_error(
                    f"plan:unknown_tool:{step.name}",
                    ValueError(f"Tool '{step.name}' not found in registry"),
                )
                self._publish("tool_error", {"name": step.name, "msg": "unknown tool"})
                break
            try:
                tool.func(**step.kwargs)
                self._publish("tool_fired", {"name": step.name})
            except Exception as e:
                self._feedback.on_error(f"plan:step:{step.name}", e)
                self._publish("tool_error", {"name": step.name, "msg": str(e)})
                break
            executed += 1
            if tool.settle_ms > 0:
                time.sleep(tool.settle_ms / 1000.0)
        self._feedback.on_plan_complete(transcript, executed)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_dispatcher.py -v`
Expected: ALL PASS (existing tests still pass because `event_bus=None` is default)

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/dispatcher.py tests/unit/test_dispatcher.py
git commit -m "feat: wire EventBus into Dispatcher for tool_fired/miss/tool_error events"
```

---

### Task 5: Wire EventBus into StreamingDaemon

**Files:**
- Modify: `src/voice_commander/daemon.py`
- Modify: `tests/integration/test_daemon_factory.py`

- [ ] **Step 1: Write failing test for daemon event_bus wiring**

Append to `tests/integration/test_daemon_factory.py`:

```python
def test_daemon_factory_creates_event_bus(base_cfg):
    """Event bus is always created when web is enabled."""
    with patch.multiple(
        _DAEMON_MOD,
        load_silero_vad=MagicMock(),
        VADGate=MagicMock(),
        WindowsFeedbackSink=MagicMock(),
        Transcriber=MagicMock(),
        StreamingRecorder=MagicMock(),
        HotkeyController=MagicMock(),
        validate_config_or_die=MagicMock(),
        validate_or_die=MagicMock(),
    ):
        daemon = build_streaming_daemon(base_cfg)
    assert daemon._event_bus is not None


def test_daemon_factory_event_bus_passed_to_dispatcher(base_cfg):
    with patch.multiple(
        _DAEMON_MOD,
        load_silero_vad=MagicMock(),
        VADGate=MagicMock(),
        WindowsFeedbackSink=MagicMock(),
        Transcriber=MagicMock(),
        StreamingRecorder=MagicMock(),
        HotkeyController=MagicMock(),
        validate_config_or_die=MagicMock(),
        validate_or_die=MagicMock(),
    ):
        daemon = build_streaming_daemon(base_cfg)
    assert daemon._dispatcher._event_bus is daemon._event_bus
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/integration/test_daemon_factory.py::test_daemon_factory_creates_event_bus -v`
Expected: FAIL — daemon has no `_event_bus`

- [ ] **Step 3: Add EventBus to daemon**

Modify `src/voice_commander/daemon.py`:

Add import:
```python
from .event_bus import EventBus
```

Add `event_bus` parameter to `StreamingDaemon.__init__()`:
```python
    def __init__(
        self,
        feedback: FeedbackSink,
        recorder: StreamingRecorder | None,
        transcriber: Transcriber,
        matcher: Matcher,
        dispatcher: Dispatcher,
        *,
        registry: ToolRegistry | None = None,
        llm_router: LLMRouter | None = None,
        event_bus: EventBus | None = None,
        min_confidence: float = 0.30,
        min_word_count: int = 1,
        max_no_speech_prob: float = 0.6,
        output_dir: str = "outputs",
        web_server: WebServer | None = None,
    ) -> None:
```

Add to body:
```python
        self._event_bus = event_bus
```

Add publish calls to `on_scroll_lock()`:
```python
    def _publish(self, event_type: str, data: dict | None = None) -> None:
        if self._event_bus is not None:
            self._event_bus.publish(event_type, data)
```

In `on_scroll_lock()`, after `self._feedback.on_recording_start()`:
```python
                self._publish("session_started")
```
After `self._feedback.on_recording_stop()`:
```python
            self._publish("session_stopped")
```

In `on_mute_toggle()`, after `self._muted = False` (unmute):
```python
                self._publish("unmuted")
```
After `self._muted = True` (mute):
```python
            self._publish("muted")
```

In `_process_utterance()`, before `self._transcriber.transcribe()`:
```python
        self._publish("transcribing")
```
Before `self._matcher.match()`:
```python
        self._publish("matching")
```
Before `self._llm_router.route()`:
```python
            self._publish("llm_thinking")
```

Add heartbeat thread in `run()`, after pipeline thread start:
```python
        # Start heartbeat producer thread (1 Hz).
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="vc-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()
```

Add warmup events in `run()`, around `self._transcriber.load()`:
```python
        self._publish("warmup_start")
        try:
            self._transcriber.load()
        except Exception as e:
            ...
        self._publish("warmup_done")
```

Add heartbeat loop method:
```python
    def _heartbeat_loop(self) -> None:
        while not self._shutdown.wait(1.0):
            self._publish("daemon_heartbeat")
```

In `build_streaming_daemon()`, create EventBus and pass it:
```python
    event_bus = EventBus()
```

Pass to `Dispatcher`:
```python
    dispatcher = Dispatcher(feedback, event_bus=event_bus)
```

Pass to `create_app`:
```python
        app = create_app(registry, store, reload_lock, event_bus=event_bus)
```

Pass to `StreamingDaemon`:
```python
    daemon = StreamingDaemon(
        ...
        event_bus=event_bus,
        ...
    )
```

- [ ] **Step 4: Run all tests**

Run: `pytest tests/unit/ tests/integration/ -v --ignore=tests/integration/test_llm_router_live.py`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/daemon.py tests/integration/test_daemon_factory.py
git commit -m "feat: wire EventBus into daemon — publish events at all state change points"
```

---

### Task 6: Add dependencies to pyproject.toml

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add new dependencies and entry point**

Add to `dependencies` list:
```toml
    "pyglet>=2.0",
    "httpx-sse>=0.4.0",
```

Add `voice-sprite` entry point under `[project.scripts]`:
```toml
voice-sprite = "voice_sprite.__main__:main"
```

Add to `[tool.hatch.build.targets.wheel]`:
```toml
packages = ["src/voice_commander", "src/voice_sprite"]
```

Add coverage omit for sprite hardware modules:
```toml
    "src/voice_sprite/dpi.py",
    "src/voice_sprite/win32_flags.py",
    "src/voice_sprite/window.py",
    "src/voice_sprite/__main__.py",
```

Add mypy ignore for pyglet:
```toml
    "pyglet.*",
    "httpx_sse.*",
```

- [ ] **Step 2: Run uv sync**

Run: `uv sync`
Expected: Dependencies install successfully

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "feat(deps): add pyglet, httpx-sse; add voice-sprite entry point"
```

---

## Phase 2: Sprite Process Core

### Task 7: State machine

**Files:**
- Create: `src/voice_sprite/__init__.py`
- Create: `src/voice_sprite/state_machine.py`
- Create: `tests/unit/test_state_machine.py`

- [ ] **Step 1: Create package marker**

```python
# src/voice_sprite/__init__.py
```

- [ ] **Step 2: Write failing state machine tests**

```python
# tests/unit/test_state_machine.py
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from voice_sprite.state_machine import (
    ANIMATED_TRANSITIONS,
    EVENT_STATE_MAP,
    HOLD_DURATION_S,
    HOLD_STATES,
    SpriteState,
    StateMachine,
)


def test_initial_state_is_warmup():
    sm = StateMachine()
    assert sm.current_state == SpriteState.WARMUP


def test_session_started_transitions_to_listening():
    sm = StateMachine()
    sm.current_state = SpriteState.IDLE
    result = sm.on_event("session_started", {})
    assert result == SpriteState.LISTENING


def test_session_stopped_transitions_to_idle():
    sm = StateMachine()
    sm.current_state = SpriteState.LISTENING
    sm.on_event("session_stopped", {})
    assert sm.current_state == SpriteState.IDLE


def test_vad_speech_active_true():
    sm = StateMachine()
    sm.current_state = SpriteState.LISTENING
    result = sm.on_event("vad_speech", {"active": True})
    assert result == SpriteState.HEARING_SPEECH


def test_vad_speech_active_false_ignored():
    sm = StateMachine()
    sm.current_state = SpriteState.LISTENING
    result = sm.on_event("vad_speech", {"active": False})
    assert result is None
    assert sm.current_state == SpriteState.LISTENING


def test_tool_fired_transitions_to_success():
    sm = StateMachine()
    sm.current_state = SpriteState.THINKING
    result = sm.on_event("tool_fired", {"name": "copy"})
    assert result == SpriteState.SUCCESS


def test_miss_transitions_to_miss():
    sm = StateMachine()
    sm.current_state = SpriteState.THINKING
    sm.on_event("miss", {})
    assert sm.target_state == SpriteState.MISS


def test_muted_sets_overlay():
    sm = StateMachine()
    sm.on_event("muted", {})
    assert sm.muted is True
    sm.on_event("unmuted", {})
    assert sm.muted is False


def test_muted_does_not_change_state():
    sm = StateMachine()
    sm.current_state = SpriteState.LISTENING
    sm.on_event("muted", {})
    assert sm.current_state == SpriteState.LISTENING


def test_heartbeat_resets_timer():
    sm = StateMachine()
    sm.on_event("daemon_heartbeat", {})
    assert sm._last_heartbeat > 0


def test_heartbeat_timeout_forces_crashed():
    sm = StateMachine(heartbeat_timeout_ms=100)
    sm.current_state = SpriteState.IDLE
    sm._last_heartbeat = time.monotonic() - 1.0  # 1s ago, timeout is 0.1s
    changed = sm.tick(0.016)
    assert changed is True
    assert sm.current_state == SpriteState.CRASHED


def test_heartbeat_recovery_after_crashed():
    sm = StateMachine()
    sm.current_state = SpriteState.CRASHED
    sm.on_event("daemon_heartbeat", {})
    assert sm.target_state == SpriteState.IDLE


def test_hold_state_returns_to_listening():
    sm = StateMachine()
    sm.current_state = SpriteState.LISTENING
    sm.on_event("tool_fired", {"name": "copy"})
    # Simulate hold timer expiry
    sm._hold_timer = time.monotonic() - 0.1
    changed = sm.tick(0.016)
    assert changed is True
    assert sm.current_state == SpriteState.LISTENING


def test_warmup_done_transitions_to_idle():
    sm = StateMachine()
    assert sm.current_state == SpriteState.WARMUP
    sm.on_event("warmup_done", {})
    assert sm.current_state == SpriteState.IDLE


def test_unknown_event_returns_none():
    sm = StateMachine()
    result = sm.on_event("bogus_event", {})
    assert result is None


def test_force_crashed():
    sm = StateMachine()
    sm.current_state = SpriteState.LISTENING
    sm.force_crashed()
    assert sm.current_state == SpriteState.CRASHED
    assert sm.target_state == SpriteState.CRASHED


def test_all_states_in_event_map():
    """Every non-crashed, non-muted state should be reachable via at least one event."""
    reachable = set(v for v in EVENT_STATE_MAP.values() if v is not None)
    unreachable = {SpriteState.CRASHED}  # only reachable via heartbeat timeout
    for state in SpriteState:
        if state not in unreachable:
            assert state in reachable, f"{state} not reachable via any event"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/unit/test_state_machine.py -v`
Expected: FAIL — module not found

- [ ] **Step 4: Implement state machine**

```python
# src/voice_sprite/state_machine.py
from __future__ import annotations

import time
from enum import Enum
from typing import Any


class SpriteState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    HEARING_SPEECH = "hearing_speech"
    THINKING = "thinking"
    LLM_THINKING = "llm_thinking"
    SUCCESS = "success"
    MISS = "miss"
    TOOL_ERROR = "tool_error"
    WARMUP = "warmup"
    CRASHED = "crashed"


# Event → target state mapping (pure data table).
# None means "no state change" (e.g. heartbeat just resets timer).
EVENT_STATE_MAP: dict[str, SpriteState | None] = {
    "session_started": SpriteState.LISTENING,
    "session_stopped": SpriteState.IDLE,
    "vad_speech": SpriteState.HEARING_SPEECH,
    "transcribing": SpriteState.THINKING,
    "matching": SpriteState.THINKING,
    "llm_thinking": SpriteState.LLM_THINKING,
    "tool_fired": SpriteState.SUCCESS,
    "miss": SpriteState.MISS,
    "tool_error": SpriteState.TOOL_ERROR,
    "warmup_start": SpriteState.WARMUP,
    "warmup_done": SpriteState.IDLE,
    "daemon_heartbeat": None,
}

# Transitions that get bespoke animations (all others = instant swap).
ANIMATED_TRANSITIONS: set[tuple[SpriteState, SpriteState]] = {
    (SpriteState.IDLE, SpriteState.LISTENING),
    (SpriteState.LISTENING, SpriteState.IDLE),
    (SpriteState.LISTENING, SpriteState.HEARING_SPEECH),
    (SpriteState.HEARING_SPEECH, SpriteState.THINKING),
    (SpriteState.THINKING, SpriteState.LLM_THINKING),
    (SpriteState.THINKING, SpriteState.SUCCESS),
    (SpriteState.LLM_THINKING, SpriteState.SUCCESS),
    (SpriteState.THINKING, SpriteState.MISS),
    (SpriteState.LLM_THINKING, SpriteState.MISS),
}

# States that hold for 1 second before returning to LISTENING.
HOLD_STATES: set[SpriteState] = {
    SpriteState.SUCCESS,
    SpriteState.MISS,
    SpriteState.TOOL_ERROR,
}
HOLD_DURATION_S = 1.0


class StateMachine:
    """Sprite state machine driven by SSE events."""

    def __init__(self, heartbeat_timeout_ms: int = 3000) -> None:
        self.current_state = SpriteState.WARMUP
        self.target_state = SpriteState.WARMUP
        self.muted = False
        self._heartbeat_timeout_s = heartbeat_timeout_ms / 1000.0
        self._last_heartbeat: float = 0.0
        self._hold_timer: float | None = None
        self._transitioning = False

    def on_event(self, event_type: str, data: dict[str, Any]) -> SpriteState | None:
        """Process an SSE event. Returns new target state, or None if no change."""
        if event_type == "daemon_heartbeat":
            self._last_heartbeat = time.monotonic()
            if self.current_state == SpriteState.CRASHED:
                self.target_state = SpriteState.IDLE
            return None

        if event_type == "muted":
            self.muted = True
            return None

        if event_type == "unmuted":
            self.muted = False
            return None

        # vad_speech only triggers on active=true
        if event_type == "vad_speech" and not data.get("active", False):
            return None

        target = EVENT_STATE_MAP.get(event_type)
        if target is None:
            return None

        self.target_state = target

        if target in HOLD_STATES:
            self._hold_timer = time.monotonic() + HOLD_DURATION_S

        pair = (self.current_state, target)
        self._transitioning = pair in ANIMATED_TRANSITIONS

        if not self._transitioning:
            self.current_state = target

        return target

    def tick(self, dt: float) -> bool:
        """Called every frame. Returns True if state changed."""
        # Heartbeat timeout → CRASHED
        if self._last_heartbeat > 0:
            elapsed = time.monotonic() - self._last_heartbeat
            if (
                elapsed > self._heartbeat_timeout_s
                and self.current_state != SpriteState.CRASHED
            ):
                self.current_state = SpriteState.CRASHED
                self.target_state = SpriteState.CRASHED
                return True

        # Hold timer expiry → return to LISTENING
        if self._hold_timer is not None and time.monotonic() >= self._hold_timer:
            self._hold_timer = None
            self.target_state = SpriteState.LISTENING
            self.current_state = SpriteState.LISTENING
            return True

        return False

    def force_crashed(self) -> None:
        """Force CRASHED state (used when SSE connection drops)."""
        self.current_state = SpriteState.CRASHED
        self.target_state = SpriteState.CRASHED
        self._transitioning = False
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/test_state_machine.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/voice_sprite/__init__.py src/voice_sprite/state_machine.py tests/unit/test_state_machine.py
git commit -m "feat: add sprite state machine with 11 states + transition graph"
```

---

### Task 8: Speech bubble

**Files:**
- Create: `src/voice_sprite/speech_bubble.py`
- Create: `tests/unit/test_speech_bubble.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_speech_bubble.py
from voice_sprite.speech_bubble import SpeechBubble


def test_initial_state_not_visible():
    b = SpeechBubble()
    assert b.visible is False
    assert b.text == ""
    assert b.opacity == 0.0


def test_show_makes_visible():
    b = SpeechBubble(fade_ms=2000)
    b.show("copy")
    assert b.visible is True
    assert b.text == "copy"
    assert b.opacity == 1.0


def test_tick_fades():
    b = SpeechBubble(fade_ms=1000)
    b.show("paste")
    b.tick(0.5)
    assert b.visible is True
    assert 0.4 < b.opacity < 0.6


def test_tick_past_duration_hides():
    b = SpeechBubble(fade_ms=1000)
    b.show("paste")
    b.tick(1.1)
    assert b.visible is False
    assert b.text == ""


def test_show_resets_timer():
    b = SpeechBubble(fade_ms=1000)
    b.show("first")
    b.tick(0.8)
    b.show("second")
    assert b.text == "second"
    assert b.opacity == 1.0


def test_zero_fade_ms():
    b = SpeechBubble(fade_ms=0)
    b.show("instant")
    assert b.opacity == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_speech_bubble.py -v`
Expected: FAIL

- [ ] **Step 3: Implement speech bubble**

```python
# src/voice_sprite/speech_bubble.py
from __future__ import annotations


class SpeechBubble:
    """Fading last-command label displayed near the sprite."""

    def __init__(self, fade_ms: int = 2000) -> None:
        self._fade_s = fade_ms / 1000.0
        self._text: str = ""
        self._timer: float = 0.0
        self._visible = False

    def show(self, text: str) -> None:
        """Show text and restart the fade timer."""
        self._text = text
        self._timer = self._fade_s
        self._visible = True

    def tick(self, dt: float) -> None:
        """Advance the fade timer by dt seconds."""
        if not self._visible:
            return
        self._timer -= dt
        if self._timer <= 0:
            self._visible = False
            self._text = ""

    @property
    def text(self) -> str:
        return self._text if self._visible else ""

    @property
    def opacity(self) -> float:
        if not self._visible or self._fade_s == 0:
            return 0.0
        return max(0.0, min(1.0, self._timer / self._fade_s))

    @property
    def visible(self) -> bool:
        return self._visible
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_speech_bubble.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/voice_sprite/speech_bubble.py tests/unit/test_speech_bubble.py
git commit -m "feat: add sprite speech bubble with fade timer"
```

---

### Task 9: Charsheet TOML parser + validator

**Files:**
- Create: `src/voice_sprite/charsheet.py`
- Create: `tests/unit/test_charsheet.py`
- Create: `assets/sprite/charsheet.toml`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_charsheet.py
from __future__ import annotations

from pathlib import Path

import pytest

from voice_sprite.charsheet import CharSheet, CharSheetError, load_charsheet
from voice_sprite.state_machine import SpriteState


@pytest.fixture()
def valid_toml(tmp_path):
    toml = tmp_path / "charsheet.toml"
    toml.write_text(
        'frame_width = 64\nframe_height = 64\nfps = 12\n\n'
        '[states.idle]\nrow = 0\nframes = 2\n\n'
        '[states.listening]\nrow = 1\nframes = 2\n\n'
        '[states.hearing_speech]\nrow = 2\nframes = 2\n\n'
        '[states.thinking]\nrow = 3\nframes = 2\n\n'
        '[states.llm_thinking]\nrow = 4\nframes = 2\n\n'
        '[states.success]\nrow = 5\nframes = 2\n\n'
        '[states.miss]\nrow = 6\nframes = 2\n\n'
        '[states.tool_error]\nrow = 7\nframes = 2\n\n'
        '[states.warmup]\nrow = 8\nframes = 2\n\n'
        '[states.crashed]\nrow = 9\nframes = 2\n\n'
    )
    return toml


@pytest.fixture()
def valid_png(tmp_path):
    """Create a minimal 128x640 PNG (2 cols x 10 rows of 64x64 frames)."""
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("Pillow not installed")
    img = Image.new("RGBA", (128, 640), (0, 0, 0, 0))
    path = tmp_path / "charsheet.png"
    img.save(path)
    return path


def test_load_charsheet_valid(valid_toml, valid_png):
    cs = load_charsheet(valid_toml, valid_png)
    assert cs.frame_width == 64
    assert cs.frame_height == 64
    assert cs.fps == 12
    assert len(cs.states) == 10


def test_load_charsheet_missing_state(tmp_path, valid_png):
    toml = tmp_path / "charsheet.toml"
    toml.write_text(
        'frame_width = 64\nframe_height = 64\nfps = 12\n\n'
        '[states.idle]\nrow = 0\nframes = 2\n'
    )
    with pytest.raises(CharSheetError, match="missing"):
        load_charsheet(toml, valid_png)


def test_load_charsheet_oob_row(tmp_path):
    """Row * frame_height exceeds PNG height."""
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("Pillow not installed")
    # 64x64 PNG — only 1 row fits
    img = Image.new("RGBA", (128, 64), (0, 0, 0, 0))
    png = tmp_path / "charsheet.png"
    img.save(png)
    toml = tmp_path / "charsheet.toml"
    # row 1 requires y >= 64, but PNG is only 64px tall
    toml.write_text(
        'frame_width = 64\nframe_height = 64\nfps = 12\n\n'
        + "".join(
            f'[states.{s.value}]\nrow = {i}\nframes = 2\n\n'
            for i, s in enumerate(SpriteState)
        )
    )
    with pytest.raises(CharSheetError, match="bounds"):
        load_charsheet(toml, png)


def test_load_charsheet_unknown_keys_warns(valid_toml, valid_png, caplog):
    """Unknown TOML keys should WARN, not fail."""
    import tomllib
    data = tomllib.loads(valid_toml.read_text())
    data["future_key"] = "whatever"
    import tomli_w  # noqa: might not exist — skip gracefully
    # Just verify the load function handles it; we test via adding to file
    content = valid_toml.read_text() + '\nfuture_key = "hi"\n'
    valid_toml.write_text(content)
    cs = load_charsheet(valid_toml, valid_png)
    assert cs.frame_width == 64  # still loads


def test_charsheet_get_state_anim(valid_toml, valid_png):
    cs = load_charsheet(valid_toml, valid_png)
    anim = cs.get_state_anim(SpriteState.IDLE)
    assert anim.row == 0
    assert anim.frames == 2


def test_charsheet_transitions(tmp_path, valid_png):
    toml = tmp_path / "charsheet.toml"
    content = (
        'frame_width = 64\nframe_height = 64\nfps = 12\n\n'
        + "".join(
            f'[states.{s.value}]\nrow = {i}\nframes = 2\n\n'
            for i, s in enumerate(SpriteState)
        )
        + '[transitions."listening->thinking"]\nrow = 10\nframes = 3\nonce = true\n'
    )
    toml.write_text(content)
    # Need PNG tall enough: 11 rows * 64 = 704
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("Pillow not installed")
    img = Image.new("RGBA", (192, 704), (0, 0, 0, 0))
    png = tmp_path / "charsheet.png"
    img.save(png)
    cs = load_charsheet(toml, png)
    t = cs.get_transition(SpriteState.LISTENING, SpriteState.THINKING)
    assert t is not None
    assert t.frames == 3
    assert t.once is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_charsheet.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement charsheet parser + validator**

```python
# src/voice_sprite/charsheet.py
from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .state_machine import SpriteState

logger = logging.getLogger(__name__)

KNOWN_TOP_KEYS = {"frame_width", "frame_height", "fps", "states", "transitions"}


class CharSheetError(Exception):
    pass


@dataclass(frozen=True)
class AnimInfo:
    row: int
    frames: int
    once: bool = False


@dataclass
class CharSheet:
    frame_width: int
    frame_height: int
    fps: int
    states: dict[SpriteState, AnimInfo]
    transitions: dict[tuple[SpriteState, SpriteState], AnimInfo]

    def get_state_anim(self, state: SpriteState) -> AnimInfo:
        return self.states[state]

    def get_transition(
        self, from_state: SpriteState, to_state: SpriteState
    ) -> AnimInfo | None:
        return self.transitions.get((from_state, to_state))


def load_charsheet(toml_path: Path, png_path: Path) -> CharSheet:
    """Load and validate a charsheet TOML + PNG pair."""
    if not png_path.exists():
        raise CharSheetError(f"Charsheet PNG not found: {png_path}")

    with toml_path.open("rb") as f:
        raw = tomllib.load(f)

    # Warn on unknown top-level keys
    for key in raw:
        if key not in KNOWN_TOP_KEYS:
            logger.warning("Unknown charsheet.toml key: %s (ignored)", key)

    frame_w = raw.get("frame_width", 128)
    frame_h = raw.get("frame_height", 128)
    fps = raw.get("fps", 12)

    # Parse state animations
    states_raw = raw.get("states", {})
    states: dict[SpriteState, AnimInfo] = {}
    for state in SpriteState:
        entry = states_raw.get(state.value)
        if entry is None:
            raise CharSheetError(
                f"State '{state.value}' missing from charsheet.toml [states] section"
            )
        states[state] = AnimInfo(
            row=entry["row"],
            frames=entry["frames"],
            once=entry.get("once", False),
        )

    # Parse transition animations
    transitions_raw = raw.get("transitions", {})
    transitions: dict[tuple[SpriteState, SpriteState], AnimInfo] = {}
    for key, entry in transitions_raw.items():
        parts = key.split("->")
        if len(parts) != 2:
            logger.warning("Malformed transition key: %s (ignored)", key)
            continue
        try:
            from_state = SpriteState(parts[0].strip())
            to_state = SpriteState(parts[1].strip())
        except ValueError:
            logger.warning("Unknown state in transition '%s' (ignored)", key)
            continue
        transitions[(from_state, to_state)] = AnimInfo(
            row=entry["row"],
            frames=entry["frames"],
            once=entry.get("once", True),
        )

    cs = CharSheet(
        frame_width=frame_w,
        frame_height=frame_h,
        fps=fps,
        states=states,
        transitions=transitions,
    )

    # Validate PNG bounds
    _validate_png_bounds(cs, png_path)

    return cs


def _validate_png_bounds(cs: CharSheet, png_path: Path) -> None:
    """Verify every declared row × frames fits inside the PNG."""
    try:
        from PIL import Image

        img = Image.open(png_path)
        png_w, png_h = img.size
        img.close()
    except ImportError:
        # Pillow not available — skip bounds check (pyglet will catch at render time)
        logger.warning("Pillow not installed — skipping charsheet PNG bounds validation")
        return

    all_anims: list[tuple[str, AnimInfo]] = []
    for state, anim in cs.states.items():
        all_anims.append((f"state:{state.value}", anim))
    for (from_s, to_s), anim in cs.transitions.items():
        all_anims.append((f"transition:{from_s.value}->{to_s.value}", anim))

    for label, anim in all_anims:
        max_x = anim.frames * cs.frame_width
        max_y = (anim.row + 1) * cs.frame_height
        if max_x > png_w or max_y > png_h:
            raise CharSheetError(
                f"{label} (row={anim.row}, frames={anim.frames}) exceeds PNG bounds "
                f"({png_w}x{png_h}). Need at least {max_x}x{max_y}."
            )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_charsheet.py -v`
Expected: ALL PASS (tests that need Pillow skip if not installed)

- [ ] **Step 5: Create charsheet.toml asset**

```toml
# assets/sprite/charsheet.toml
# Metadata for the sprite companion character sheet.
# Each [states.*] entry maps a SpriteState to a row in charsheet.png.
# Regenerate charsheet.png using the prompt template in README.md.

frame_width = 128
frame_height = 128
fps = 12

[states.idle]
row = 0
frames = 4

[states.listening]
row = 1
frames = 6

[states.hearing_speech]
row = 2
frames = 4

[states.thinking]
row = 3
frames = 6

[states.llm_thinking]
row = 4
frames = 6

[states.success]
row = 5
frames = 3

[states.miss]
row = 6
frames = 3

[states.tool_error]
row = 7
frames = 3

[states.warmup]
row = 8
frames = 4

[states.crashed]
row = 9
frames = 2

# Transition animations (optional — pairs not listed use instant swap)
[transitions."idle->listening"]
row = 10
frames = 4
once = true

[transitions."listening->idle"]
row = 11
frames = 4
once = true

[transitions."listening->hearing_speech"]
row = 12
frames = 3
once = true

[transitions."hearing_speech->thinking"]
row = 13
frames = 4
once = true

[transitions."thinking->success"]
row = 14
frames = 3
once = true

[transitions."thinking->miss"]
row = 15
frames = 3
once = true
```

- [ ] **Step 6: Create README.md art generation template**

```markdown
# Sprite Companion — Character Sheet

## How to regenerate

Use your preferred image generator with this prompt template:

> A pixel-art character sheet grid for a small desktop assistant sprite.
> 128×128 pixel frames, 6 columns wide, 16 rows tall (total 768×2048 PNG).
> Transparent background (RGBA).
>
> Row 0: idle — gentle breathing loop (4 frames)
> Row 1: listening — ears perked, slight glow (6 frames)
> Row 2: hearing speech — mouth open, sound waves (4 frames)
> Row 3: thinking — spinning gear above head (6 frames)
> Row 4: LLM thinking — brain glow + sparkles (6 frames)
> Row 5: success — happy bounce + checkmark (3 frames)
> Row 6: miss — confused head tilt + question mark (3 frames)
> Row 7: tool error — red flash + exclamation (3 frames)
> Row 8: warmup — loading spinner (4 frames)
> Row 9: crashed — greyed out, X eyes (2 frames)
> Rows 10-15: transition animations (see charsheet.toml)

Save the output as `charsheet.png` in this directory. Run the daemon
to validate that all rows/frames fit within the PNG bounds.
```

- [ ] **Step 7: Commit**

```bash
git add src/voice_sprite/charsheet.py tests/unit/test_charsheet.py assets/sprite/charsheet.toml assets/sprite/README.md
git commit -m "feat: add charsheet TOML parser + validator + asset template"
```

---

### Task 10: SSE event client

**Files:**
- Create: `src/voice_sprite/event_client.py`
- Create: `tests/unit/test_event_client.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_event_client.py
from __future__ import annotations

import json
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from voice_sprite.event_client import SSEClient


def test_on_event_callback_called():
    """Verify on_event fires with parsed event type and data."""
    received = []
    client = SSEClient(
        base_url="http://localhost:9999",
        on_event=lambda t, d: received.append((t, d)),
        on_disconnect=lambda: None,
    )
    # Simulate calling _dispatch directly
    client._dispatch("tool_fired", {"name": "copy", "ts": 1.0})
    assert received == [("tool_fired", {"name": "copy", "ts": 1.0})]


def test_on_disconnect_callback_called():
    """Verify on_disconnect fires when connection drops."""
    disconnected = threading.Event()
    client = SSEClient(
        base_url="http://localhost:9999",
        on_event=lambda t, d: None,
        on_disconnect=lambda: disconnected.set(),
    )
    client._on_disconnect_callback()
    assert disconnected.is_set()


def test_stop_sets_event():
    client = SSEClient(
        base_url="http://localhost:9999",
        on_event=lambda t, d: None,
        on_disconnect=lambda: None,
    )
    client.stop()
    assert client._stop.is_set()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_event_client.py -v`
Expected: FAIL

- [ ] **Step 3: Implement SSE client**

```python
# src/voice_sprite/event_client.py
from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable

import httpx
from httpx_sse import connect_sse

logger = logging.getLogger(__name__)


class SSEClient:
    """Connects to the daemon's /events SSE endpoint in a background thread."""

    def __init__(
        self,
        base_url: str,
        on_event: Callable[[str, dict[str, Any]], None],
        on_disconnect: Callable[[], None],
    ) -> None:
        self._base_url = base_url
        self._on_event = on_event
        self._on_disconnect = on_disconnect
        self._last_event_id: str = "0"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="sse-client"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def _dispatch(self, event_type: str, data: dict[str, Any]) -> None:
        self._on_event(event_type, data)

    def _on_disconnect_callback(self) -> None:
        self._on_disconnect()

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                with httpx.Client(
                    timeout=httpx.Timeout(
                        connect=5.0, read=35.0, write=5.0, pool=5.0
                    )
                ) as client:
                    headers: dict[str, str] = {}
                    if self._last_event_id != "0":
                        headers["Last-Event-ID"] = self._last_event_id
                    with connect_sse(
                        client,
                        "GET",
                        f"{self._base_url}/events",
                        headers=headers,
                    ) as sse:
                        backoff = 1.0
                        for event in sse.iter_sse():
                            if self._stop.is_set():
                                return
                            if event.id:
                                self._last_event_id = event.id
                            try:
                                data = (
                                    json.loads(event.data) if event.data else {}
                                )
                            except json.JSONDecodeError:
                                data = {}
                            self._dispatch(event.event or "message", data)
            except (
                httpx.ConnectError,
                httpx.ReadTimeout,
                httpx.RemoteProtocolError,
            ) as e:
                logger.warning(
                    "SSE connection lost: %s. Reconnecting in %.0fs", e, backoff
                )
                self._on_disconnect_callback()
                if self._stop.wait(backoff):
                    return
                backoff = min(backoff * 2, 8.0)
            except Exception:
                logger.exception("Unexpected SSE error")
                self._on_disconnect_callback()
                if self._stop.wait(backoff):
                    return
                backoff = min(backoff * 2, 8.0)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_event_client.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/voice_sprite/event_client.py tests/unit/test_event_client.py
git commit -m "feat: add SSE event client with reconnect + backoff"
```

---

### Task 11: Sprite config loading

**Files:**
- Create: `src/voice_sprite/config.py`
- Create: `tests/unit/test_sprite_config.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_sprite_config.py
from __future__ import annotations

from pathlib import Path

import pytest

from voice_sprite.config import SpriteAppConfig, load_sprite_config


def test_defaults():
    cfg = load_sprite_config(Path("/nonexistent.toml"))
    assert cfg.daemon_url == "http://127.0.0.1:8765"
    assert cfg.corner == "bottom_right"
    assert cfg.base_size_px == 128
    assert cfg.offset_x == 16
    assert cfg.offset_y == 16
    assert cfg.asset_path == "assets/sprite"
    assert cfg.bubble_fade_ms == 2000
    assert cfg.heartbeat_timeout_ms == 3000


def test_override(tmp_path):
    toml = tmp_path / "config.toml"
    toml.write_text(
        '[sprite]\ncorner = "top_left"\nbase_size_px = 64\n'
        '[web]\nport = 9000\n'
    )
    cfg = load_sprite_config(toml)
    assert cfg.corner == "top_left"
    assert cfg.base_size_px == 64
    assert cfg.daemon_url == "http://127.0.0.1:9000"


def test_local_overlay(tmp_path):
    base = tmp_path / "config.toml"
    base.write_text('[sprite]\ncorner = "bottom_right"\n')
    local = tmp_path / "config.local.toml"
    local.write_text('[sprite]\ncorner = "top_right"\n')
    cfg = load_sprite_config(base)
    assert cfg.corner == "top_right"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_sprite_config.py -v`
Expected: FAIL

- [ ] **Step 3: Implement sprite config**

```python
# src/voice_sprite/config.py
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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


def load_sprite_config(
    config_path: Path,
    local_path: Path | None = None,
) -> SpriteAppConfig:
    """Load sprite config from the daemon's config.toml [sprite] + [web] sections."""
    raw = _read_toml(config_path)
    if local_path is None:
        local_path = config_path.with_name(
            f"{config_path.stem}.local{config_path.suffix}"
        )
    if local_path != config_path and local_path.exists():
        raw = _deep_merge(raw, _read_toml(local_path))

    sprite_raw = raw.get("sprite", {})
    web_raw = raw.get("web", {})

    # Derive daemon URL from web config
    host = web_raw.get("host", "127.0.0.1")
    port = web_raw.get("port", 8765)
    daemon_url = f"http://{host}:{port}"

    return SpriteAppConfig(
        daemon_url=daemon_url,
        corner=sprite_raw.get("corner", "bottom_right"),
        base_size_px=sprite_raw.get("base_size_px", 128),
        offset_x=sprite_raw.get("offset_x", 16),
        offset_y=sprite_raw.get("offset_y", 16),
        asset_path=sprite_raw.get("asset_path", "assets/sprite"),
        bubble_fade_ms=sprite_raw.get("bubble_fade_ms", 2000),
        heartbeat_timeout_ms=sprite_raw.get("heartbeat_timeout_ms", 3000),
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

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_sprite_config.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/voice_sprite/config.py tests/unit/test_sprite_config.py
git commit -m "feat: add sprite process config loading from daemon's config.toml"
```

---

### Task 12: DPI awareness

**Files:**
- Create: `src/voice_sprite/dpi.py`
- Create: `tests/unit/test_dpi.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_dpi.py
from __future__ import annotations

from unittest.mock import MagicMock, patch

from voice_sprite.dpi import get_primary_dpi


def test_get_primary_dpi_returns_96_on_fallback():
    """When ctypes calls fail, return 96."""
    with patch("voice_sprite.dpi.ctypes") as mock_ctypes:
        mock_ctypes.windll.shcore.SetProcessDpiAwarenessContext.side_effect = OSError
        mock_ctypes.windll.shcore.GetDpiForMonitor.side_effect = OSError
        result = get_primary_dpi()
    assert result == 96


def test_get_primary_dpi_reads_real_value():
    """When ctypes calls succeed, return the DPI value."""
    with patch("voice_sprite.dpi.ctypes") as mock_ctypes:
        mock_ctypes.windll.user32.MonitorFromPoint.return_value = 1
        mock_ctypes.c_uint.return_value = MagicMock(value=144)
        mock_ctypes.byref = MagicMock()
        result = get_primary_dpi()
    assert result == 144
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_dpi.py -v`
Expected: FAIL

- [ ] **Step 3: Implement DPI module**

```python
# src/voice_sprite/dpi.py
from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging

logger = logging.getLogger(__name__)


def get_primary_dpi() -> int:
    """Return the DPI of the primary monitor. Falls back to 96 on failure."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwarenessContext(-4)
    except (AttributeError, OSError):
        pass

    try:
        hmon = ctypes.windll.user32.MonitorFromPoint(
            ctypes.wintypes.POINT(0, 0), 1  # MONITOR_DEFAULTTOPRIMARY
        )
        dpi_x = ctypes.c_uint()
        dpi_y = ctypes.c_uint()
        ctypes.windll.shcore.GetDpiForMonitor(
            hmon, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)
        )
        return dpi_x.value
    except (AttributeError, OSError):
        logger.warning("Could not read DPI — falling back to 96")
        return 96
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_dpi.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/voice_sprite/dpi.py tests/unit/test_dpi.py
git commit -m "feat: add DPI awareness module for sprite scaling"
```

---

### Task 13: Win32 click-through flags

**Files:**
- Create: `src/voice_sprite/win32_flags.py`

- [ ] **Step 1: Implement Win32 flags module**

```python
# src/voice_sprite/win32_flags.py
"""Apply Win32 extended window styles for click-through, topmost, no-taskbar sprite."""
from __future__ import annotations

import ctypes
import logging

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = -1
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
LWA_ALPHA = 0x00000002


def apply_click_through(hwnd: int) -> None:
    """Make a window click-through, always-on-top, no taskbar, no focus steal."""
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
    user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)
    logger.info("Applied click-through flags to hwnd=%d", hwnd)
```

- [ ] **Step 2: Commit**

```bash
git add src/voice_sprite/win32_flags.py
git commit -m "feat: add Win32 click-through flags module for sprite window"
```

---

## Phase 3: Sprite Rendering + Window

### Task 14: Sprite renderer

**Files:**
- Create: `src/voice_sprite/sprite_renderer.py`

This module handles frame selection from the charsheet based on current state and animation progress. It depends on pyglet at runtime but the logic is testable without a display.

- [ ] **Step 1: Implement sprite renderer**

```python
# src/voice_sprite/sprite_renderer.py
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .charsheet import AnimInfo, CharSheet, load_charsheet
from .state_machine import SpriteState

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class SpriteRenderer:
    """Manages frame selection and animation timing from a charsheet."""

    def __init__(self, charsheet: CharSheet) -> None:
        self._cs = charsheet
        self._current_anim: AnimInfo | None = None
        self._frame_index: int = 0
        self._frame_timer: float = 0.0
        self._frame_duration: float = 1.0 / charsheet.fps
        self._state = SpriteState.WARMUP
        self._transition_anim: AnimInfo | None = None
        self._transition_done = False
        self._set_anim(charsheet.get_state_anim(SpriteState.WARMUP))

    def _set_anim(self, anim: AnimInfo) -> None:
        self._current_anim = anim
        self._frame_index = 0
        self._frame_timer = 0.0

    def set_state(self, state: SpriteState) -> None:
        """Switch to a new state's animation."""
        # Check for transition animation
        transition = self._cs.get_transition(self._state, state)
        if transition is not None:
            self._transition_anim = transition
            self._transition_done = False
            self._set_anim(transition)
        else:
            self._set_anim(self._cs.get_state_anim(state))
            self._transition_anim = None
        self._state = state

    def tick(self, dt: float) -> None:
        """Advance animation by dt seconds."""
        if self._current_anim is None:
            return
        self._frame_timer += dt
        if self._frame_timer >= self._frame_duration:
            self._frame_timer -= self._frame_duration
            self._frame_index += 1
            if self._frame_index >= self._current_anim.frames:
                if self._transition_anim is not None and self._transition_anim.once:
                    # Transition complete → switch to target state's idle anim
                    self._transition_anim = None
                    self._transition_done = True
                    self._set_anim(self._cs.get_state_anim(self._state))
                else:
                    self._frame_index = 0  # loop

    @property
    def frame_region(self) -> tuple[int, int, int, int]:
        """Return (x, y, width, height) of the current frame in the charsheet."""
        if self._current_anim is None:
            return (0, 0, self._cs.frame_width, self._cs.frame_height)
        x = self._frame_index * self._cs.frame_width
        y = self._current_anim.row * self._cs.frame_height
        return (x, y, self._cs.frame_width, self._cs.frame_height)

    def reload_charsheet(self, charsheet: CharSheet) -> None:
        """Hot-reload the charsheet (e.g. after file change)."""
        self._cs = charsheet
        anim = charsheet.get_state_anim(self._state)
        self._set_anim(anim)
        self._transition_anim = None

    @property
    def charsheet(self) -> CharSheet:
        return self._cs
```

- [ ] **Step 2: Commit**

```bash
git add src/voice_sprite/sprite_renderer.py
git commit -m "feat: add sprite renderer with frame selection + transition logic"
```

---

### Task 15: Pyglet window + main entry point

**Files:**
- Create: `src/voice_sprite/window.py`
- Create: `src/voice_sprite/__main__.py`

- [ ] **Step 1: Implement pyglet window**

```python
# src/voice_sprite/window.py
from __future__ import annotations

import logging
import platform
from typing import TYPE_CHECKING

import pyglet

if TYPE_CHECKING:
    from .speech_bubble import SpeechBubble
    from .sprite_renderer import SpriteRenderer

logger = logging.getLogger(__name__)


class SpriteWindow(pyglet.window.Window):  # type: ignore[misc]
    """Transparent, borderless, always-on-top sprite window."""

    def __init__(
        self,
        width: int,
        height: int,
        x: int,
        y: int,
        renderer: "SpriteRenderer",
        bubble: "SpeechBubble",
    ) -> None:
        super().__init__(
            width=width,
            height=height,
            style=pyglet.window.Window.WINDOW_STYLE_BORDERLESS,
            vsync=False,
        )
        self.set_location(x, y)
        self._renderer = renderer
        self._bubble = bubble
        self._image: pyglet.image.AbstractImage | None = None
        self._sprite: pyglet.sprite.Sprite | None = None
        self._label: pyglet.text.Label | None = None
        self._muted = False
        self._mute_color = (128, 128, 128)

    def load_charsheet_image(self, png_path: str) -> None:
        """Load the charsheet PNG into a pyglet image."""
        self._image = pyglet.image.load(png_path)

    def set_muted(self, muted: bool) -> None:
        self._muted = muted

    def on_draw(self) -> None:
        self.clear()
        if self._image is None:
            return

        x, y, w, h = self._renderer.frame_region
        # pyglet uses bottom-left origin; charsheet uses top-left rows
        img_h = self._image.height
        region = self._image.get_region(x, img_h - y - h, w, h)

        if self._sprite is None:
            self._sprite = pyglet.sprite.Sprite(region, x=0, y=0)
        else:
            self._sprite.image = region

        if self._muted:
            self._sprite.color = self._mute_color
        else:
            self._sprite.color = (255, 255, 255)

        self._sprite.draw()

        # Speech bubble
        if self._bubble.visible:
            if self._label is None:
                self._label = pyglet.text.Label(
                    self._bubble.text,
                    font_name="Segoe UI",
                    font_size=10,
                    x=w // 2,
                    y=h + 4,
                    anchor_x="center",
                    anchor_y="bottom",
                    color=(255, 255, 255, int(self._bubble.opacity * 255)),
                )
            else:
                self._label.text = self._bubble.text
                self._label.color = (
                    255,
                    255,
                    255,
                    int(self._bubble.opacity * 255),
                )
            self._label.draw()

    def apply_win32_flags(self) -> None:
        """Apply click-through, topmost, no-taskbar flags (Windows only)."""
        if platform.system() != "Windows":
            logger.warning("Win32 flags only apply on Windows")
            return
        from .win32_flags import apply_click_through

        hwnd = self.canvas.hwnd if hasattr(self.canvas, "hwnd") else None
        if hwnd is None:
            # pyglet 2.x: access via _hwnd
            hwnd = getattr(self, "_hwnd", None)
        if hwnd is None:
            logger.error("Could not obtain HWND for sprite window")
            return
        apply_click_through(hwnd)
```

- [ ] **Step 2: Implement CLI entry point**

```python
# src/voice_sprite/__main__.py
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

logger = logging.getLogger("voice_sprite")


def _configure_logging() -> None:
    os.makedirs("outputs", exist_ok=True)
    handler = RotatingFileHandler(
        "outputs/sprite.log",
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(threadName)s %(name)s %(levelname)s: %(message)s",
        handlers=[handler, logging.StreamHandler()],
        force=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="voice-sprite",
        description="On-screen sprite companion for Voice Commander.",
    )
    parser.add_argument(
        "--config",
        default="config.toml",
        help="Path to Voice Commander config.toml (default: config.toml)",
    )
    args = parser.parse_args()

    _configure_logging()
    logger.info("voice-sprite starting")

    from .charsheet import CharSheetError, load_charsheet
    from .config import load_sprite_config
    from .dpi import get_primary_dpi
    from .event_client import SSEClient
    from .speech_bubble import SpeechBubble
    from .sprite_renderer import SpriteRenderer
    from .state_machine import SpriteState, StateMachine

    # Load config
    config_path = Path(args.config)
    cfg = load_sprite_config(config_path)
    logger.info("Config loaded: %s", cfg)

    # DPI scaling
    dpi = get_primary_dpi()
    scale = dpi / 96.0
    size = int(cfg.base_size_px * scale)
    logger.info("DPI=%d, scale=%.2f, size=%dpx", dpi, scale, size)

    # Load charsheet
    asset_dir = Path(cfg.asset_path)
    toml_path = asset_dir / "charsheet.toml"
    png_path = asset_dir / "charsheet.png"

    if not png_path.exists():
        logger.error("Charsheet PNG not found: %s", png_path)
        print(f"ERROR: {png_path} not found. See assets/sprite/README.md for generation instructions.", file=sys.stderr)
        sys.exit(1)

    try:
        charsheet = load_charsheet(toml_path, png_path)
    except CharSheetError as e:
        logger.error("Charsheet validation failed: %s", e)
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # State machine + renderer + bubble
    sm = StateMachine(heartbeat_timeout_ms=cfg.heartbeat_timeout_ms)
    renderer = SpriteRenderer(charsheet)
    bubble = SpeechBubble(fade_ms=cfg.bubble_fade_ms)

    # Import pyglet (deferred to avoid import-time display probe)
    import pyglet

    # Calculate position
    screen = pyglet.canvas.get_display().get_default_screen()
    offset_x = int(cfg.offset_x * scale)
    offset_y = int(cfg.offset_y * scale)

    positions = {
        "bottom_right": (screen.width - size - offset_x, offset_y),
        "bottom_left": (offset_x, offset_y),
        "top_right": (screen.width - size - offset_x, screen.height - size - offset_y),
        "top_left": (offset_x, screen.height - size - offset_y),
    }
    x, y = positions.get(cfg.corner, positions["bottom_right"])

    from .window import SpriteWindow

    window = SpriteWindow(
        width=size, height=size, x=x, y=y, renderer=renderer, bubble=bubble
    )
    window.load_charsheet_image(str(png_path))
    window.apply_win32_flags()

    # Hot-reload: check charsheet file mtime every 2 seconds
    _last_toml_mtime = toml_path.stat().st_mtime if toml_path.exists() else 0
    _last_png_mtime = png_path.stat().st_mtime if png_path.exists() else 0

    def check_hot_reload(dt: float) -> None:
        nonlocal _last_toml_mtime, _last_png_mtime, charsheet
        try:
            toml_mt = toml_path.stat().st_mtime if toml_path.exists() else 0
            png_mt = png_path.stat().st_mtime if png_path.exists() else 0
            if toml_mt != _last_toml_mtime or png_mt != _last_png_mtime:
                logger.info("Charsheet changed — hot-reloading")
                charsheet = load_charsheet(toml_path, png_path)
                renderer.reload_charsheet(charsheet)
                window.load_charsheet_image(str(png_path))
                _last_toml_mtime = toml_mt
                _last_png_mtime = png_mt
        except Exception:
            logger.exception("Hot-reload failed")

    pyglet.clock.schedule_interval(check_hot_reload, 2.0)

    # SSE event handler
    def on_event(event_type: str, data: dict) -> None:
        result = sm.on_event(event_type, data)
        if result is not None:
            renderer.set_state(result)
            logger.info("State → %s", result.value)
        window.set_muted(sm.muted)
        if event_type == "tool_fired" and "name" in data:
            bubble.show(data["name"])

    def on_disconnect() -> None:
        sm.force_crashed()
        renderer.set_state(SpriteState.CRASHED)
        logger.warning("SSE disconnected — sprite entering CRASHED state")

    # Start SSE client
    sse = SSEClient(
        base_url=cfg.daemon_url,
        on_event=on_event,
        on_disconnect=on_disconnect,
    )
    sse.start()

    # Main update loop
    def update(dt: float) -> None:
        changed = sm.tick(dt)
        if changed:
            renderer.set_state(sm.current_state)
        renderer.tick(dt)
        bubble.tick(dt)

    pyglet.clock.schedule_interval(update, 1 / 60.0)

    logger.info("Sprite window open at (%d, %d), size=%d", x, y, size)

    try:
        pyglet.app.run()
    except KeyboardInterrupt:
        pass
    finally:
        sse.stop()
        logger.info("voice-sprite exiting")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Commit**

```bash
git add src/voice_sprite/window.py src/voice_sprite/__main__.py
git commit -m "feat: add pyglet sprite window + CLI entry point"
```

---

## Phase 4: Integration + Docs

### Task 16: start.ps1 integration

**Files:**
- Modify: `start.ps1`

- [ ] **Step 1: Add -NoSprite parameter**

Add to the param block (after `$NoOpenBrowser`):

```powershell
    [Parameter(ParameterSetName = 'Interactive')]
    [Parameter(ParameterSetName = 'NoMenu')]
    [Parameter(ParameterSetName = 'DirectDevice')]
    [switch]$NoSprite
```

- [ ] **Step 2: Add sprite spawn to Start-VoiceWithUI**

In the `Start-VoiceWithUI` function, after the browser auto-open block and before `return Start-VoiceDaemon`, add:

```powershell
    # Spawn sprite companion unless disabled
    if (-not $NoSprite -and -not $NoUI) {
        Write-Verbose "Scheduling sprite companion spawn (after 500 ms)"
        Start-Job -ScriptBlock {
            Start-Sleep -Milliseconds 500
            $spriteProcess = Start-Process -FilePath "uv" -ArgumentList "run","voice-sprite" -PassThru -WindowStyle Hidden
            # Sprite runs independently — daemon does not track it
        } | Out-Null
    }
```

- [ ] **Step 3: Commit**

```bash
git add start.ps1
git commit -m "feat: add --NoSprite flag to start.ps1 + sprite subprocess spawn"
```

---

### Task 17: ADRs

**Files:**
- Create: `docs/decisions/0040-sprite-separate-process-via-sse.md`
- Create: `docs/decisions/0041-pyglet-over-tkinter-pyqt-web-overlay.md`
- Create: `docs/decisions/0042-charsheet-grid-format-sidecar-toml.md`
- Create: `docs/decisions/0043-eventbus-sse-outbound-telemetry.md`
- Create: `docs/decisions/0044-miss-chimes-retained.md`

- [ ] **Step 1: Write ADR 0040**

```markdown
# ADR 0040: Sprite as a Separate Process via SSE

**Date:** 2026-04-21
**Status:** Accepted
**Supersedes:** —
**References:** ADR 0013 (dropped WinRT toasts), ADR 0020 (embedded FastAPI)

## Context

We need visual feedback for daemon state (listening, thinking, success, miss, crashed). The sprite must never steal focus, never block the audio pipeline, and must be crash-isolated from the daemon.

## Decision

The sprite runs as a separate OS process (`voice_sprite`) consuming events from the daemon's FastAPI `/events` SSE endpoint. No direct IPC, shared memory, or in-process rendering.

## Consequences

- A sprite crash cannot take down voice recognition.
- Daemon has zero knowledge of sprite health — fire-and-forget events.
- SSE reconnect with `Last-Event-ID` provides automatic gap recovery.
- Adds ~20 MB memory for the pyglet process.
- Requires the FastAPI web server to be running (it already is by default).
```

- [ ] **Step 2: Write ADR 0041**

```markdown
# ADR 0041: Pyglet over Tkinter / PyQt / Web Overlay

**Date:** 2026-04-21
**Status:** Accepted

## Context

We need a transparent, always-on-top, click-through, borderless window for the sprite. Candidates: Tkinter, PyQt, Electron overlay, pyglet.

## Decision

pyglet 2.x. It provides:
- Per-pixel alpha transparency via OpenGL.
- Sprite-sheet primitives (TextureGrid, Animation) built-in.
- No heavy framework dependency (Qt = 100+ MB).
- Direct HWND access for Win32 extended style flags.

## Consequences

- Requires OpenGL-capable GPU (universal on modern Windows).
- Sprite-sheet slicing is native; no Pillow at runtime.
- pyglet's event loop runs on the main thread; SSE client runs on a daemon thread.
```

- [ ] **Step 3: Write ADR 0042**

```markdown
# ADR 0042: Char-Sheet Grid Format with Sidecar TOML

**Date:** 2026-04-21
**Status:** Accepted
**References:** ADR 0021 (sidecar TOML per tool)

## Context

Sprite animations need to be regeneratable without manual frame splicing. The art pipeline must be "generate a grid PNG + fill in a TOML" — no code changes needed for new art.

## Decision

Single `charsheet.png` (N columns × M rows) with a sidecar `charsheet.toml` mapping state names to rows. Validator at startup confirms every state has a row and every row fits within the PNG bounds.

## Consequences

- Art regeneration is a single image generation step + TOML edit.
- Hot-reload watches both files for mtime changes.
- Unknown TOML keys warn (not fail) for forward compatibility.
```

- [ ] **Step 4: Write ADR 0043**

```markdown
# ADR 0043: EventBus + SSE as Daemon's Outbound Telemetry Channel

**Date:** 2026-04-21
**Status:** Accepted
**References:** ADR 0020 (embedded FastAPI)

## Context

The daemon needs to broadcast state changes to external consumers (sprite, future dashboards). Previous approach was ad-hoc log scraping.

## Decision

In-daemon `EventBus` with sync `publish()` (callable from any thread) and per-subscriber `asyncio.Queue` for SSE consumers. FastAPI `/events` endpoint streams events as `text/event-stream` with `Last-Event-ID` ring-buffer replay (100 events).

## Consequences

- Any future consumer (web dashboard, analytics) uses the same SSE feed.
- `publish()` is non-blocking — zero impact on the audio pipeline hot path.
- Bounded per-subscriber queues (1024 events) prevent memory leaks from slow consumers.
- `events_dropped` counter is log-only in MVP; future ADR for `/metrics` endpoint.
```

- [ ] **Step 5: Write ADR 0044**

```markdown
# ADR 0044: Miss Chimes Retained

**Date:** 2026-04-21
**Status:** Accepted
**References:** ADR 0014 (miss-only chimes)

## Context

With the sprite providing visual feedback for all states, we considered removing the audio miss chime.

## Decision

Keep the miss chime (ADR 0014 stays). The sprite **supplements** audio feedback, it does not replace it. Peripheral vision may miss sprite state changes during focused work; the audio chime provides an interrupt-level signal that a command was not understood.

## Consequences

- No changes to `WindowsFeedbackSink` or `winsound` usage.
- Users who prefer visual-only can mute system sounds independently.
```

- [ ] **Step 6: Commit**

```bash
git add docs/decisions/0040-sprite-separate-process-via-sse.md docs/decisions/0041-pyglet-over-tkinter-pyqt-web-overlay.md docs/decisions/0042-charsheet-grid-format-sidecar-toml.md docs/decisions/0043-eventbus-sse-outbound-telemetry.md docs/decisions/0044-miss-chimes-retained.md
git commit -m "docs: add ADRs 0040-0044 for sprite companion decisions"
```

---

### Task 18: Update architecture docs

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/agents/technical-decisions.md` (if it exists)

- [ ] **Step 1: Add EventBus + sprite sections to architecture.md**

Append after the Web UI Subsystem section (§7):

```markdown
---

## 8. EventBus Subsystem

### Component

```python
class EventBus:
    def publish(self, event_type: str, data: dict | None = None) -> None: ...
    def subscribe(self) -> asyncio.Queue[Event]: ...
    def unsubscribe(self, q: asyncio.Queue[Event]) -> None: ...
    def replay_after(self, last_id: int) -> list[Event]: ...
```

**What it does:** Thread-safe pub/sub broker. `publish()` is called from daemon threads (hotkey, pipeline, heartbeat). Each SSE connection calls `subscribe()` to get a bounded `asyncio.Queue` (max 1024 events, drop-oldest on overflow). 100-event ring buffer supports `Last-Event-ID` reconnect replay.

**Who calls it:** `StreamingDaemon` (session/mute/warmup events), `Dispatcher` (tool_fired/miss/tool_error), heartbeat thread (1 Hz daemon_heartbeat).

**Who consumes it:** FastAPI `/events` SSE endpoint → sprite process via httpx-sse.

### SSE Endpoint

`GET /events` — `text/event-stream`. One JSON event per SSE frame. Keepalive every 30s. `Last-Event-ID` header rewinds through ring buffer.

---

## 9. Sprite Companion (Separate Process)

### Architecture

```
voice-commander daemon (process A)            voice_sprite (process B)
───────────────────────────────────           ─────────────────────────
HotkeyCtrl ──▶ VADGate ──▶ Dispatcher         httpx SSE client
                              │                     │
                              ▼                     ▼
                         EventBus ──SSE──▶   StateMachine
                              ▲                     │
                         FastAPI /events             ▼
                         (uvicorn thread)     pyglet Window
                                              (topmost, click-through,
                                               transparent, no titlebar)
```

### Module layout

- `state_machine.py` — 11 states, event→state mapping, heartbeat timeout
- `event_client.py` — httpx-sse with exponential backoff reconnect
- `charsheet.py` — TOML parser + PNG bounds validator
- `sprite_renderer.py` — frame selection + animation timing
- `window.py` — pyglet Window with Win32 click-through flags
- `speech_bubble.py` — fading last-command label
- `dpi.py` — per-monitor DPI scaling
- `win32_flags.py` — WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE

### Lifecycle

`start.ps1` spawns daemon → waits 500 ms → spawns `uv run voice-sprite`. Daemon never checks sprite health. Sprite polls daemon heartbeat; 3s timeout → CRASHED state → SSE reconnect loop.
```

- [ ] **Step 2: Commit**

```bash
git add docs/architecture.md
git commit -m "docs: add EventBus + sprite companion sections to architecture.md"
```

---

### Task 19: Run full test suite + lint

- [ ] **Step 1: Run all unit + integration tests**

Run: `pytest tests/unit/ tests/integration/ -v --ignore=tests/integration/test_llm_router_live.py`
Expected: ALL PASS

- [ ] **Step 2: Run ruff lint**

Run: `ruff check src/ tests/`
Expected: No errors

- [ ] **Step 3: Run ruff format**

Run: `ruff format --check src/ tests/`
Expected: Already formatted (or run `ruff format src/ tests/` to fix)

- [ ] **Step 4: Fix any issues found**

- [ ] **Step 5: Commit if fixes needed**

```bash
git add -u
git commit -m "style: fix lint issues from sprite companion implementation"
```

---

### Task 20: Human validation gate

This is the Phase 4 human validation checklist. The implementing engineer should verify each item manually before declaring the feature complete.

- [ ] **Step 1: Create placeholder charsheet.png**

Generate a minimal charsheet PNG using the prompt template in `assets/sprite/README.md`, or create a programmatic placeholder:

```python
# scripts/generate-placeholder-charsheet.py
from PIL import Image, ImageDraw, ImageFont

FRAME_W, FRAME_H = 128, 128
COLS = 6
ROWS = 16
img = Image.new("RGBA", (COLS * FRAME_W, ROWS * FRAME_H), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

states = ["idle", "listening", "hearing", "thinking", "llm_think",
          "success", "miss", "error", "warmup", "crashed"]
for row, name in enumerate(states):
    for col in range(COLS):
        x, y = col * FRAME_W, row * FRAME_H
        draw.rectangle([x+2, y+2, x+FRAME_W-2, y+FRAME_H-2],
                       outline=(100, 200, 100, 200), width=2)
        draw.text((x+10, y+10), f"{name}\nf{col}", fill=(255, 255, 255, 200))

img.save("assets/sprite/charsheet.png")
print("Placeholder charsheet.png created")
```

- [ ] **Step 2: Validate manually**

| Check | How |
|-------|-----|
| Sprite renders in corner | `uv run voice-sprite` — window appears in bottom-right |
| Click-through works | Click where sprite is — click lands on window beneath |
| States change | Say commands, observe sprite transitions |
| Heartbeat timeout | Kill daemon → sprite turns to CRASHED within 3.5s |
| Reconnect | Restart daemon → sprite recovers from CRASHED |
| Speech bubble | Fire a tool → tool name appears briefly |
| DPI scaling | Check at 100%, 150%, 200% scale |
| Corner config | Change `corner` in config.local.toml → restart sprite |
| Hot reload | Edit charsheet.toml → sprite reloads within 2s |
| Crash isolation | Kill sprite → daemon keeps running |

---

## Summary

| Phase | Tasks | What's delivered |
|-------|-------|-----------------|
| 0 | 0a-0d | Pyglet + httpx-sse reference docs, Win32 click-through smoke test, plan reconciliation |
| 1 | 1-6 | SpriteConfig, EventBus, SSE endpoint, daemon wiring, deps |
| 2 | 7-13 | State machine, speech bubble, charsheet, SSE client, sprite config, DPI, Win32 flags |
| 3 | 14-15 | Sprite renderer, pyglet window, CLI entry point |
| 4 | 16-20 | start.ps1 integration, ADRs, architecture docs, full test suite, human validation |

**Total:** 24 tasks, ~115 steps, estimated 5-7 hours for an experienced engineer (Phase 0 adds ~1 hour of parallelizable research).
