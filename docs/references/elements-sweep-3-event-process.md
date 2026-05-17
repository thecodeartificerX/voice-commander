# Elements-Mode Sweep — Event / SSE / Process Layer

**Scope:** duplicate event delivery, duplicate subscribers, duplicate processes,
stale-connection handling, and `on_event` routing for `elements.show` /
`elements.hide`.

**Files read:**
- `src/voice_sprite/__main__.py`
- `src/voice_sprite/event_client.py`
- `src/voice_sprite/elements_overlay.py`
- `src/voice_sprite/win32_flags.py`
- `src/voice_commander/event_bus.py`
- `src/voice_commander/supervisor/cli.py`
- `src/voice_commander/supervisor/loop.py`
- `src/voice_commander/supervisor/process.py`
- `src/voice_commander/supervisor/sprite.py`
- `src/voice_commander/web/app.py` (`/events` SSE endpoint)
- `src/voice_commander/daemon.py` (`ElementsSession` wiring)
- `src/voice_commander/elements/session.py`
- `src/voice_commander/__main__.py` (daemon single-instance lock)
- `src/voice_commander/single_instance.py`

---

## Ranked Findings

### FINDING 1 — CRITICAL: `elements.show` replayed on every SSE reconnect, triggering a second `apply_win32_flags()` on the existing GL context

**Files:** `src/voice_commander/web/app.py:272` (`subscribe_with_replay`),
`src/voice_commander/event_bus.py:79-90` (`subscribe_with_replay`),
`src/voice_sprite/__main__.py:241-244` (`_show_elements` / `pyglet.clock.schedule_once`)

**Severity:** CRITICAL — almost certainly the primary cause of "works once, opaque
black on second invocation"

**Mechanism:**

The SSE endpoint at `/events` calls `event_bus.subscribe_with_replay(last_id)`.
`EventBus` keeps a ring buffer of the last 100 events (`_max_buffer=100`,
`event_bus.py:38`). On reconnect the sprite sends `Last-Event-ID` equal to the last
event it received. The server then replays every buffered event with
`id > last_id` (`event_bus.py:89`).

`elements.show` is an event that lands in the ring buffer like any other. If the
sprite SSE connection drops and reconnects **while the overlay is already on screen**
(or even after `elements.hide` fires but before the reconnect drains replay), the
replay stream will re-deliver `elements.show` to `on_event`. That calls
`_show_elements`, which schedules `_create` on the pyglet event loop. `_create`
calls `_hide_elements_now()` first — but that just closes the old GL window. A brand
new `ElementsOverlayWindow` is then constructed and `apply_win32_flags()` is called.

**Why this produces opaque black:** The first-time window goes through the canonical
lifecycle: pyglet creates it, calls `_set_transparency()` (DWM blur-behind), then
the window is shown — transparent. When the replayed event creates the replacement
window, the GL context is a brand-new OpenGL surface on an already-running pyglet
app. Some Windows driver / DWM optimisation paths do not re-honour
`DwmEnableBlurBehindWindow` reliably on windows created after a DWM compositor
cycle has already processed the display list. The second window's `on_draw` fires
before `apply_win32_flags()` completes (there is a one-frame race — `schedule_once`
with `delay=0.0` queues the callback but GL rendering may run first), so DWM
composites the first frame as opaque black. The same race exists even without a
reconnect if two `elements.show` events land back-to-back.

**Replay also re-delivers `elements.hide`** events that already reached the sprite.
A replayed `elements.hide` hitting an active overlay closes it spuriously; a
replayed `elements.show` re-opens it.

**Concrete fix (two parts):**

1. **Filter transient events from replay.** In `EventBus.subscribe_with_replay` (or
   at publish time) mark `elements.show` and `elements.hide` as non-replayable, or
   use a separate ring buffer only for replay-safe state events (heartbeat, muted,
   session_started, etc.). The simplest approach: in the `/events` SSE generator,
   skip replay of events whose `type` is in a `NO_REPLAY` set:
   `{"elements.show", "elements.hide", "picker.open", "picker.close"}`.

2. **On the sprite side, add an idempotency guard.** Track a `_last_elements_show_id`
   (the SSE event id). If an `elements.show` arrives with the same or lower id as
   one already processed, discard it. This is defence-in-depth against any future
   path that re-delivers the event.

---

### FINDING 2 — HIGH: No single-instance guard on the sprite process; supervisor can spawn a second sprite on daemon restart

**Files:** `src/voice_commander/supervisor/loop.py:45-63`,
`src/voice_commander/supervisor/cli.py:102-122`,
`src/voice_commander/supervisor/sprite.py:27-35`

**Severity:** HIGH — directly confirmed by the reported symptom of two
`python -m voice_sprite` processes

**Mechanism:**

`supervisor/loop.py:run()` calls `daemon_factory()` in a `while True` loop on every
`EXIT_RESTART` (exit code 75). The sprite is spawned **once before the loop starts**
(`cli.py:116`) and is **never respawned**. That part is safe.

However, `SingleInstanceLock` covers the **supervisor** (`outputs/.supervisor.lock`,
`cli.py:108`) and the **daemon** (`outputs/.daemon.lock`, `__main__.py:221`). There
is **no lock for the sprite**. The sprite has no `SingleInstanceLock` and no port
guard. If the user manually runs `python -m voice_sprite` while a supervised sprite
is running, or if two terminal sessions start `python -m voice_commander-supervisor`
(the supervisor lock only fires if both start from the same `outputs/` directory
relative path), two sprites can coexist.

Two sprites → two SSE connections → every `elements.show` event is delivered to
both `on_event` handlers → two `ElementsOverlayWindow` instances are created,
stacked on top of each other. If one is transparent and the other lands with the
opaque-black second-window bug, the combined result is a black overlay.

**Concrete fix:** Add a `SingleInstanceLock` (or a Windows named mutex) inside
`voice_sprite/__main__.py:main()` before `sse.start()`, analogous to the daemon
lock. The lock path should be `outputs/.sprite.lock`. If it fails, log and exit 1
rather than running a second overlay.

---

### FINDING 3 — HIGH: `elements.show` → `apply_win32_flags()` timing race: GL draw fires before DWM flags are applied

**Files:** `src/voice_sprite/__main__.py:227-244`,
`src/voice_sprite/elements_overlay.py:189-206`,
`src/voice_sprite/win32_flags.py:70-143`

**Severity:** HIGH — explains works-once / opaque-black-thereafter pattern even
without any reconnect

**Mechanism:**

In `_show_elements` (`__main__.py:227`), the flow is:

```python
def _show_elements(data):
    def _create(_dt):
        _hide_elements_now()          # 1. close old overlay if any
        overlay = ElementsOverlayWindow(monitor, elements)  # 2. create + show
        overlay.apply_win32_flags()   # 3. DWM flags
        _elements_overlay.append(overlay)
    pyglet.clock.schedule_once(_create, 0.0)
```

`ElementsOverlayWindow.__init__` creates the window **visible** (no `visible=False`
kwarg). Pyglet's `_create()` calls `_set_transparency()` inside the platform backend
**synchronously at window creation** — this is the correct path for the first window.
But the GL event loop may fire `on_draw` for the new window **before** the
`schedule_once` callback that contains `apply_win32_flags()` runs, because
`schedule_once(delay=0.0)` adds to the scheduled-callback list processed at the
*next* clock tick, while `on_draw` may fire in the same clock tick as window
creation. On the **first** invocation this is benign — pyglet's own
`_set_transparency()` already set the DWM state. On the **second** invocation
(after `_hide_elements_now()` destroyed the previous GL context), the compositor
may not have re-committed the DWM state for the new HWND by the time `on_draw`
fires.

The comment in `__main__.py:237` notes this explicitly:
> "apply_win32_flags() must run AFTER the window is shown, otherwise SetWindowLongW
> drops the layered per-pixel alpha..."

But "after the window is shown" and "before the first `on_draw`" cannot both be
guaranteed by `schedule_once(0.0)` alone — the scheduling contract only says "no
sooner than 0 seconds", not "before the next draw call".

**Concrete fix:** Either (a) move `apply_win32_flags()` into
`ElementsOverlayWindow.__init__` itself immediately after `super().__init__()` and
accept the note that `SetWindowLongW` ordering matters — test which order works — or
(b) add a one-frame delay: `pyglet.clock.schedule_once(lambda _: overlay.apply_win32_flags(), 1/60.0)` so it fires in the frame *after* the first draw. Option (b) is the
lower-risk change given the existing comment about the ordering constraint.

---

### FINDING 4 — MEDIUM: SSE reconnect calls `on_disconnect` which sets sprite state to CRASHED and never un-CRASHEs on re-connect

**Files:** `src/voice_sprite/event_client.py:88-92`,
`src/voice_sprite/__main__.py:290-294`

**Severity:** MEDIUM — does not directly cause the black overlay, but every
reconnect corrupts sprite state and triggers a state-machine transition that is
irreversible without another event from the daemon

**Mechanism:**

`SSEClient._run` calls `self._on_disconnect_callback()` on any
`httpx.ConnectError / ReadTimeout / RemoteProtocolError` before reconnecting
(`event_client.py:88`). The sprite's `on_disconnect` callback calls
`sm.force_crashed()` and `renderer.set_state(SpriteState.CRASHED)`
(`__main__.py:291-293`). There is no matching "on_reconnect" path that resets the
state machine to IDLE before the reconnect SSE stream starts delivering events. The
first event from the reconnected stream (which may be a replayed `elements.show`
from Finding 1) lands on a sprite still in CRASHED state.

Combined with Finding 1, the sprite: (a) enters CRASHED state, (b) replays
`elements.show`, (c) creates a new overlay while the sprite window itself shows a
crashed animation. Whether the overlay is transparent or black depends on the DWM
timing from Finding 3.

**Concrete fix:** Add an `on_reconnect` callback (or reuse `on_disconnect` with a
second argument) that resets the state machine to IDLE. The simplest change: in
`SSEClient`, after a successful reconnect (after `connect_sse` succeeds and before
the `iter_sse` loop begins), call an `on_reconnect` callback. In `__main__.py`,
implement it as `sm.force_idle()` (or similar) + `renderer.set_state(SpriteState.IDLE)`.

---

### FINDING 5 — MEDIUM: Replay of `elements.show` cannot be stopped by `Last-Event-ID` alone if the sprite crashed before receiving it

**Files:** `src/voice_sprite/event_client.py:27`, `event_client.py:60-61`

**Severity:** MEDIUM — replay guard partially broken by the CRASHED state reset

**Mechanism:**

`SSEClient._last_event_id` is initialised to `"0"` and is only updated when a
received event carries a non-empty `event.id` (`event_client.py:72-73`). If the
sprite crashes or is restarted from scratch (new process), `_last_event_id` resets
to `"0"` and the reconnect sends no `Last-Event-ID` header (because `last_event_id != "0"` is False — wait, it IS `"0"`, so the check at `event_client.py:60`
`if self._last_event_id != "0"` passes it over and the header is omitted). This
causes the server to replay from `id=1`, i.e., replay the **entire ring buffer** of
100 events — including any `elements.show` published since daemon startup.

Even in the normal (non-crash) case, if the sprite is running and its SSE
connection drops, `_last_event_id` retains the last received id, the reconnect
sends `Last-Event-ID`, and the server replays from there. A replay starting just
after an `elements.show` will re-deliver it.

**Concrete fix:** See Finding 1, part 1 — exclude `elements.show` / `elements.hide`
from replay. The `_last_event_id` mechanism by itself cannot prevent the overlay bug
because any clean-start or post-crash reconnect will replay from 0.

---

### FINDING 6 — LOW: `_hide_elements_now()` pops and closes windows but does not await GL teardown before creating the replacement

**Files:** `src/voice_sprite/__main__.py:219-225`

**Severity:** LOW — teardown race unlikely in practice but theoretically possible

**Mechanism:**

```python
def _hide_elements_now():
    while _elements_overlay:
        window_to_close = _elements_overlay.pop()
        try:
            window_to_close.close()
        except Exception:
            pass
```

`pyglet.window.Window.close()` marks the window for destruction and removes it from
the pyglet app's window list, but on Windows the actual Win32 `DestroyWindow` call
and DWM deregistration may not complete synchronously within the same clock tick.
If `ElementsOverlayWindow.__init__` immediately follows (within the same
`_create` callback, as it does), the new window's `SetWindowPos(..., HWND_TOPMOST, ...)`
in `apply_win32_flags` could race with the old window's `DestroyWindow`. In
practice pyglet serialises both on the same event-loop thread so the risk is low,
but it is an unguarded assumption.

**Concrete fix:** Low priority given same-thread guarantee. Document the assumption
with a comment.

---

### FINDING 7 — LOW: No guard against `elements_session` being `None` during scan callback

**Files:** `src/voice_commander/daemon.py:791`

**Severity:** LOW — already guarded by `if self._elements_session is None: return`
at the top of `_do_element_scan`. Included for completeness; not a bug.

---

### FINDING 8 — INFORMATIONAL: Duplicate subscriber queues accumulate if the `/events` FastAPI handler exits without `unsubscribe`

**Files:** `src/voice_commander/web/app.py:274-317`

**Severity:** INFORMATIONAL — correctly handled via `finally: event_bus.unsubscribe(q)` at line 317. No bug.

However: if uvicorn is restarted (daemon `EXIT_RESTART`) and the previous generator's
`finally` block did not run (e.g. uvicorn kills the coroutine without awaiting
cleanup), the stale queue remains in `_subscribers`. This is a theoretical resource
leak. The sprite reconnects, a new queue is added, and now two queues receive every
event — but the stale one has no consumer, so it just fills and drops. No
double-delivery to the sprite. The leak is bounded by the number of reconnects.

---

## Top Suspect

**Finding 1** (replay of `elements.show` on SSE reconnect) is the single most
likely cause of the works-once / black-thereafter pattern. The `subscribe_with_replay`
mechanism is correct for stateful events like `session_started` / `muted`, but it is
actively harmful for transient overlay-open events. Every reconnect (normal
keep-alive timeout, daemon restart, momentary network hiccup) replays the open
command to a sprite that may already have, or already have closed, the overlay. The
replacement window created from the replayed event is the one most likely to hit the
DWM timing race (Finding 3) because it is the second window created in the
compositor's lifetime.

**Finding 2** (no sprite single-instance lock) explains the previously observed two
`voice_sprite` processes and the stacked-window symptom.

Both must be fixed together — resolving only one leaves the other as a live bug
path.

---

## Fix Priority Summary

| # | Severity | File(s) | One-line fix |
|---|----------|---------|-------------|
| 1 | CRITICAL | `web/app.py:272`, `event_bus.py:79` | Exclude `elements.show/hide` from SSE replay |
| 2 | HIGH | `supervisor/sprite.py`, `voice_sprite/__main__.py` | Add `SingleInstanceLock` to sprite startup |
| 3 | HIGH | `voice_sprite/__main__.py:244` | Defer `apply_win32_flags` by one frame (1/60 s) instead of 0 s |
| 4 | MEDIUM | `event_client.py:88`, `__main__.py:290` | Add `on_reconnect` callback to reset state machine before replay flows in |
| 5 | MEDIUM | `event_client.py:27,60` | Covered by Fix 1; secondary mitigation via reconnect reset |
| 6 | LOW | `__main__.py:219` | Document same-thread teardown assumption |
