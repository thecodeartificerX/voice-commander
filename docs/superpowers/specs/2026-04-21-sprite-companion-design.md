# Sprite Companion — Design Spec

**Date:** 2026-04-21
**Status:** Design approved, plan pending
**Scope:** Add a live on-screen sprite that mirrors daemon state — listening, thinking, success, miss, crashed, etc. — rendered in a transparent, always-on-top, click-through Pyglet window driven by an SSE feed from the existing FastAPI web UI.

---

## 1. Goals

1. Give the user a peripheral-vision visual signal of what the voice commander is doing, without stealing focus or interrupting work.
2. Reuse the existing embedded FastAPI UI (ADR 0020) as the event bus — no second server, no direct IPC.
3. Keep the sprite fully decoupled: a sprite crash must never take down voice. A daemon crash must degrade the sprite gracefully (greyed-out / "crashed" pose).
4. Make the character sheet regeneratable. The art pipeline is "hand user a prompt template, user returns a grid PNG + metadata TOML" — no hand-edited frame splicing.
5. Honour the existing "boil the ocean" standard: tests, ADRs, docs before merge.

## 2. Non-goals (MVP)

- Multi-monitor placement (primary only).
- Runtime corner-switching UI (config edit + restart).
- Mouse interaction of any kind — sprite is click-through, no right-click menu, no drag.
- Speech bubbles beyond a 2-second fading last-command label.
- Live analytics dashboards or rolling counters pinned to the sprite.
- Replacing the miss-only chime (ADR 0014 stays — sprite supplements, does not replace, audio feedback).
- Respawning a crashed sprite. Failure is loud, not silent.

## 3. Architecture

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

- **EventBus** — new in-daemon pub/sub (thin broker around `queue.Queue` + per-subscriber `asyncio.Queue`). Fire-and-forget `publish()`; non-blocking on the hot path. Bounded per-subscriber queue (1024 events), drop-oldest on overflow.
- **FastAPI `/events`** — new SSE endpoint on the existing uvicorn daemon thread. One JSON event per SSE frame. `Last-Event-ID` header supported with a 100-event ring buffer for reconnect resume.
- **`voice_sprite`** — separate Python package with its own CLI entry point (`uv run voice-sprite`). Ships alongside `voice_commander` in the same repo, same `uv` environment, but runs as a distinct OS process.
- **Lifecycle** — `start.ps1` spawns daemon, waits 500 ms for FastAPI to bind, then spawns `voice-sprite` subprocess unless `--no-sprite` is passed. Daemon never checks sprite health. Sprite polls daemon heartbeat (see §6).

## 4. States (11) and transitions

Eleven discrete poses plus one overlay (`muted`). The fading last-command speech bubble is a separate UI element (§6), not a state.

### State list

| # | State | Meaning | Typical source event |
|---|-------|---------|----------------------|
| 1 | `idle` | Daemon up, session off | `session_stopped` |
| 2 | `listening` | Session on, no speech yet | `session_started` |
| 3 | `hearing_speech` | VAD active frame | `vad_speech{active:true}` |
| 4 | `thinking` | Transcribe + fuzzy match running | `transcribing`, `matching` |
| 5 | `llm_thinking` | Router escalated to LLM | `llm_thinking` |
| 6 | `success` | Tool fired | `tool_fired{name}` |
| 7 | `miss` | Fuzzy + LLM both failed | `miss` |
| 8 | `tool_error` | Exception mid-plan | `tool_error{name,msg}` |
| 9 | `muted` | Mute hotkey on (overlay tint, not swap) | `muted`, `unmuted` |
| 10 | `warmup` | CUDA / LLM loading at startup | `warmup_start` |
| 11 | `crashed` | Daemon unreachable | heartbeat timeout (sprite-local) |

### Transition graph (animated)

Any pair not listed falls back to instant state swap (no animation). Keeps the char sheet regeneratable without exhaustive coverage.

- `idle → listening`, `listening → idle`
- `listening → hearing_speech`
- `hearing_speech → thinking`
- `thinking → llm_thinking`
- `thinking → success`, `llm_thinking → success`
- `thinking → miss`, `llm_thinking → miss`
- `any → tool_error`
- `success | miss | tool_error → listening` (1-second hold, then return)
- `boot → warmup → idle`
- `any → crashed` (forced override on heartbeat timeout)

`muted` is an orthogonal overlay — dim tint, not a separate row. Unmuting restores the prior state without transition.

## 5. Event protocol

### SSE wire format — `GET /events`

`Content-Type: text/event-stream`

```
event: session_started
data: {"ts":1776700000.123}

event: tool_fired
data: {"name":"focus_browser","ts":1776700000.456}

event: vad_speech
data: {"active":true,"ts":1776700000.789}

event: daemon_heartbeat
data: {"ts":1776700001.000}
```

Clients connect with `Accept: text/event-stream`. On reconnect, `Last-Event-ID` header rewinds through the 100-event ring buffer; gaps log a WARN but do not halt the sprite.

### Event types

`session_started`, `session_stopped`, `vad_speech`, `transcribing`, `matching`, `llm_thinking`, `tool_fired`, `miss`, `tool_error`, `muted`, `unmuted`, `warmup_start`, `warmup_done`, `daemon_heartbeat` (1 Hz).

### EventBus (in-daemon broker)

- `publish(event_type, payload)` — non-blocking, called from Dispatcher / VADGate / HotkeyCtrl / LLMRouter / startup warmup path.
- `subscribe() → asyncio.Queue` — one queue per FastAPI SSE connection.
- Heartbeat producer = dedicated daemon thread at 1 Hz.
- Bounded per-subscriber queue; overflow increments an `events_dropped` counter (logged, not yet exposed — future ADR).

## 6. Sprite process internals

### Module layout

```
src/voice_sprite/
├── __main__.py           # entry: parse args, load config, start pyglet loop
├── config.py             # TOML load, config.local.toml overlay
├── event_client.py       # httpx SSE consumer → posts pyglet events
├── state_machine.py      # 12 states + transition graph
├── window.py             # pyglet Window subclass (transparent, topmost, click-through)
├── sprite_renderer.py    # char-sheet slice, animation scheduler, frame tick
├── speech_bubble.py      # fading last-command label
├── dpi.py                # ctypes GetDpiForMonitor wrapper
└── win32_flags.py        # ctypes: WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
```

### Windows-specific mechanics (ctypes, applied after window creation)

- `GetWindowLongW(hwnd, GWL_EXSTYLE) |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE` — per-pixel alpha, click-through, no taskbar entry, no focus steal.
- `SetWindowPos(hwnd, HWND_TOPMOST, ...)` — always on top.
- `SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)` — per-pixel alpha sourced from PNG.
- DPI awareness: `SetProcessDpiAwarenessContext(-4)` (per-monitor-v2) at process startup. Final size = `base_size_px × (dpi / 96)`.

### State machine

- Two fields: `current_state`, `target_state`.
- Transition animation plays `current → target`; on last frame, `current = target`, idle loop resumes.
- Event → target-state mapping is a pure data table (no logic in hot path).
- `muted` dims via colour tint, does not alter `current_state`.
- `daemon_heartbeat` timeout (3000 ms default) forces `target_state = crashed` and enters SSE reconnect loop.

### Speech bubble

- `pyglet.text.Label` positioned relative to the sprite's inner corner.
- On `tool_fired{name}` → label text set, 2-second fade-out timer starts. A new `tool_fired` cancels and restarts the timer. Fade configurable via `bubble_fade_ms`.

## 7. Char sheet spec

### Asset layout

```
assets/sprite/
├── charsheet.png           # N × M grid, row = state or transition, col = frame
├── charsheet.toml          # metadata: frame size, rows, per-state/transition anims
└── README.md               # nano-banana prompt template for regeneration
```

### `charsheet.toml` shape

```toml
frame_width = 128
frame_height = 128
fps = 12

[states.idle]
row = 0
frames = 4

[states.listening]
row = 1
frames = 6

# ... rows 0..10 reserved for the 11 states in §4

[transitions."listening->thinking"]
row = 11
frames = 4
once = true          # plays once, then current = target

[transitions."thinking->success"]
row = 12
frames = 3
once = true
```

### Validation (at sprite startup)

- Every declared `row × frames` must fit inside the PNG's pixel bounds.
- Every state in `STATES` (code constant) must have a `[states.*]` entry. Missing entry = hard fail at startup with the offending state name printed.
- Unknown TOML keys = WARN, not fail (forward compatibility with future states).
- Mirrors ADR 0036's startup-validator pattern.

### Hot reload

File-watch `charsheet.png` and `charsheet.toml`. Any modify event triggers in-place reload of the sprite renderer without restarting the sprite process. Keeps the art iteration loop fast.

## 8. Config

Added to `config.toml` defaults; overridable in `config.local.toml` (existing pattern — gitignored).

```toml
[sprite]
enabled = true                # persisted choice
corner = "bottom_right"       # top_left | top_right | bottom_left | bottom_right
base_size_px = 128            # scaled by DPI
offset_x = 16                 # margin from corner edge in DPI-scaled px
offset_y = 16
asset_path = "assets/sprite"
bubble_fade_ms = 2000
heartbeat_timeout_ms = 3000
```

`--no-sprite` flag on `start.ps1` short-circuits subprocess spawn without mutating config. Corner and enabled flag follow the same persistence convention as the input-device choice — written to `config.local.toml` when changed. MVP has no runtime toggle UI; values are set by editing the file.

## 9. `start.ps1` integration

```powershell
# existing daemon spawn unchanged
$daemon = Start-Process -PassThru ...

if (-not $NoSprite -and (Get-Config "sprite.enabled")) {
    Start-Sleep -Milliseconds 500    # let FastAPI bind
    Start-Process -FilePath "uv" -ArgumentList "run","voice-sprite"
}
```

Sprite inherits working directory and env. No log redirection here — sprite writes to `outputs/sprite.log` itself.

## 10. Error handling

| Scenario | Behaviour |
|---|---|
| Sprite can't reach `/events` at startup | Exponential backoff (1s, 2s, 4s, cap 8s). Show `warmup` pose until first event. After 30 s continuous refusal, force `crashed` tint, log WARN, keep retrying. |
| Daemon crashes mid-session | Heartbeat gap > `heartbeat_timeout_ms` → force `crashed`, disconnect SSE, enter reconnect loop. |
| Sprite crashes | Daemon unaffected. `start.ps1` does not respawn. Stack trace written to `outputs/sprite.log`. |
| `charsheet.png` missing or corrupt | Hard fail at startup with the expected path printed to stderr. No placeholder fallback. |
| `charsheet.toml` drift | Validator (§7) fails loud at startup. |
| EventBus overflow | Per-subscriber bounded queue drops oldest; `events_dropped` counter incremented. |
| DPI change at runtime (e.g. monitor unplugged) | Post-MVP. DPI is read once at startup; ignore `WM_DPICHANGED`. |
| Multi-monitor | Post-MVP. Primary monitor only; `SM_CXSCREEN`/`SM_CYSCREEN`. |
| Focus steal | `WS_EX_NOACTIVATE` prevents it; Pyglet's default `SetForegroundWindow` at window create is suppressed post-hoc. |

## 11. Testing strategy

Per `docs/testing-strategy.md`'s four-layer pyramid.

### Layer 1 — unit

- `state_machine.py` — feed synthetic event sequences, assert state transitions cover all 12 states + forced `crashed` override.
- `event_client.py` — mock SSE, assert reconnect + `Last-Event-ID` resume.
- `config.py` — TOML parse + `config.local.toml` overlay.
- `charsheet.toml` validator — malformed grids, missing states, OOB rows.
- `speech_bubble.py` — fade timer cancel / restart.
- `dpi.py` — mocked `GetDpiForMonitor`.
- Daemon-side `EventBus` — publish / subscribe, bounded-queue drop-oldest, 1 Hz heartbeat producer.

### Layer 2 — integration (daemon in-process)

- FastAPI test client subscribes to `/events`, fires synthetic Dispatcher events, asserts SSE frame order + `Last-Event-ID` resume.
- `build_streaming_daemon()` validator asserts new `[sprite]` keys are optional, defaults populate when missing, malformed values crash with a clear message.

### Layer 3 — cross-process (subprocess harness)

- Pytest fixture: launch daemon + sprite as subprocesses with a temp config. Inject events via a daemon test hook. Assert sprite's `outputs/sprite.log` lines match the expected state transitions.
- Kill daemon → assert sprite enters `crashed` within 3.5 s (log line).
- Kill sprite → assert daemon unaffected (heartbeat + tool dispatch keep running).

### Layer 4 — human validation gate (ADR 0009)

- Sprite renders in configured corner, correct size on target DPI.
- Click sprite region → click lands on window beneath (click-through works).
- Trigger each of the 12 states (voice + manual event injection); confirm pose + transition match char sheet.
- Crash resilience: `taskkill` daemon, confirm sprite greys; restart daemon, sprite reconnects.
- DPI sanity: 100 %, 150 %, 200 % scaling.
- Flip `corner` in `config.local.toml`, restart, confirm reposition.

No screen-capture pixel-diff tests — assertions live in the sprite log to survive char-sheet regeneration.

## 12. New dependencies

- `pyglet` (>= 2.x) — sprite-process only.
- `httpx` — already a candidate for the LLM router. Confirm version alignment with existing deps.
- `httpx-sse` — SSE client helper on top of httpx (`httpx` has no native SSE). Sprite-process only.
- `watchdog` — file-watching for char-sheet hot reload. Sprite-process only. (Alternative: poll mtime in the pyglet clock; decide in plan.)
- No new deps on the daemon side; FastAPI SSE is built-in (`StreamingResponse`).

## 13. ADRs to file

Written at the time of the decision, per `CLAUDE.md`:

1. **Sprite as a separate process via SSE** — supersedes the rule that all visual feedback is a daemon concern. References ADR 0013 (dropped WinRT toasts) and 0020 (embedded FastAPI).
2. **Pyglet over Tkinter / PyQt / web-overlay** — click-through + per-pixel alpha + topmost reliability on Windows, plus sprite-sheet primitives.
3. **Char-sheet grid format with sidecar TOML** — regeneratable art pipeline; parallels ADR 0021's sidecar-per-tool pattern.
4. **EventBus + SSE as the daemon's outbound telemetry channel** — replaces ad-hoc log scraping.
5. **Miss chimes retained** — ADR 0014 stays; sprite adds visual, does not replace audio.

## 14. Open questions (defer to plan / human gate)

- Exact transition-graph coverage (which pairs get bespoke animations vs instant swaps) — driven by what the user's nano-banana char sheet actually provides.
- Whether `events_dropped` counter gets a real `/metrics` endpoint in MVP or is log-only.
- File-watch implementation (`watchdog` vs pyglet-clock mtime poll) — settle in plan.

## 15. Out of scope (post-MVP follow-ups)

- Multi-monitor placement.
- Runtime corner / size toggle in web UI.
- DPI-change handling (`WM_DPICHANGED`).
- Sprite → daemon control channel (click to toggle mute, etc.) — today sprite is read-only.
- Rich analytics dashboard (rolling counters, session history, per-tool success rate) — current scope is state + last-command label only.
