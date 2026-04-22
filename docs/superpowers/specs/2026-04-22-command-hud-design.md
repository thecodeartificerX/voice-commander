# Command HUD + Cursor-Follow Sprite — Design

**Date:** 2026-04-22
**Status:** Draft (pending implementation plan)
**Related ADRs (to be filed at implementation time):**
- 0051 — Standalone command HUD overlay (chat-log surface)
- 0052 — Hybrid rule + LLM summarization
- 0053 — Sprite follows cursor across monitors, docks above taskbar

## 1. Problem

Today the voice-commander daemon emits rich logs (`plan step 1/3: focus(target='chrome')`) and publishes granular SSE events (`tool_fired`, `tool_error`, `session_started`, …), but the user-facing surface is just the sprite's short speech bubble plus a miss chime. There is no glanceable record of *what the last command was* or *whether/why it executed*. The sprite itself is also pinned to one corner of the primary monitor — irrelevant on multi-monitor setups.

Two gaps:

1. **No command HUD.** User wants an RPG-style chat log hovering next to the sprite that says `"minimized window"`, `"opened Spotify"`, or `"couldn't find 'sptfy'"` — one simplified line per command cycle, successes and failures alike.
2. **Sprite is stuck.** Should follow the cursor across monitors and dock to the bottom-right of whichever monitor the cursor currently lives on, sitting *above* the taskbar rather than overlapping it.

## 2. Non-goals

- Persistent HUD history across daemon restarts (ephemeral by design)
- Notification-style toasts (ADR 0013 stays in force)
- Replacing the miss chime (ADR 0049 stays in force — sprite supplements, never replaces)
- Full per-step trace in the HUD (one line per cycle only)
- Configurable HUD position independent of sprite (HUD *always* attaches to sprite)
- Interactive HUD (no click/hover UI — overlay is click-through)
- Mouse-hook-based cursor tracking (polling is sufficient and safer)

## 3. Architecture

```
voice-commander daemon (process A)                 voice_sprite (process B)
─────────────────────────────────                  ──────────────────────────
Dispatcher.run_plan()                              httpx-sse client
       │                                                 │
       ├─ fires each tool                                ▼
       ├─ tracks outcome                         PlanOutcome event handler
       └─ publishes plan_outcome ─── SSE ──▶           │
                  │                                      ├─ Summarizer
                  ▼                                      │   (rules first;
       { transcript, steps,                              │    LLM fallback on
         status, failed_step_index,                      │    status=error or
         error_msg, duration_ms }                        │    unknown verb)
                                                         ▼
                                                    ChatLog (ring buffer)
                                                         │
                                                         ▼
                                                    ChatLogRenderer
                                                         │
StreamingDaemon._process_utterance                       ▼
  ├─ publishes plan_outcome(status=miss) on        pyglet Window (shared)
  │  LLMRouter.route() → None                      ├─ SpriteRenderer (existing)
  └─ publishes plan_outcome(status=miss) on        ├─ SpeechBubble (existing)
     confidence-gate drop                          └─ ChatLogRenderer (new)
                                                         ▲
                                                         │ each frame reads sprite bounds
                                                         │
                                                    CursorDock (new)
                                                    polls GetCursorPos at 30 Hz
                                                    → MonitorFromPoint
                                                    → GetMonitorInfoW.rcWork
                                                    → set_location + set_size
```

**Process topology unchanged.** Daemon and sprite remain separate OS processes bridged by FastAPI SSE (ADR 0045). This feature adds one new event type and zero new processes.

**Window topology unchanged in process count, expanded in size.** Single pyglet window hosts sprite, speech bubble, and chat log — renderers are siblings sharing the transparent framebuffer established by ADR 0050. The window itself grows larger than the sprite: width = `sprite + hud_width_px`, height = `sprite + speech_bubble_height_px`. Sprite renders at the window's bottom-right region; HUD renders to the left of the sprite; speech bubble above. Transparent framebuffer ensures the empty regions of the window are fully click-through and invisible. No second HWND. No second process.

## 4. Component contracts

### 4.1 `PlanOutcome` (daemon-side value object)

```python
# src/voice_commander/plan.py (extended)
@dataclass(frozen=True)
class PlanOutcome:
    transcript: str
    steps: tuple[ToolCall, ...]            # empty on status=miss
    status: Literal["ok", "error", "miss"]
    failed_step_index: int | None          # set iff status=error
    error_msg: str | None                  # set iff status=error
    duration_ms: int                       # plan start → outcome publish

    def to_event_dict(self) -> dict[str, Any]: ...
    @classmethod
    def from_event_dict(cls, d: dict[str, Any]) -> "PlanOutcome": ...
```

Serializes to the `plan_outcome` SSE event payload.

### 4.2 `Dispatcher` changes

- Records `start_time = time.perf_counter()` at method entry
- Tracks `failed_step_index`, `error_msg` inside the step loop (None on success)
- On every exit path (success, unknown-tool break, step exception break) builds a `PlanOutcome` and calls `self._publish("plan_outcome", outcome.to_event_dict())`
- Existing granular events (`tool_fired`, `tool_error`) continue to fire unchanged — they drive sprite-state animations

### 4.3 `StreamingDaemon` changes

Publishes `plan_outcome` with `status="miss"` in two places:

1. `_process_utterance`: when `LLMRouter.route()` returns None — miss because routing failed / LLM down / `no_match` sentinel
2. `_process_utterance`: when the confidence gate fires (`result.confidence < min_confidence`) — miss because transcription was too uncertain

In both cases `steps=()`, `failed_step_index=None`, `error_msg=None`, `duration_ms` measured from utterance pickup.

Word-count gate and no-speech-prob gate drops do NOT publish — those are infrastructure noise, not routing outcomes.

### 4.4 `Summarizer` (sprite-side)

```python
# src/voice_sprite/summarizer.py
class Summarizer:
    def __init__(
        self,
        rules: dict[str, Callable[[dict, PlanOutcome], str]],
        chain_detectors: list[Callable[[tuple[ToolCall, ...]], str | None]],
        llm_client: LLMSummaryClient | None,
        llm_fallback_enabled: bool = True,
    ) -> None: ...

    def summarize(self, outcome: PlanOutcome) -> str:
        # Order:
        # 1. status=miss → "no match" (rules only, never LLM)
        # 2. status=ok:
        #    2a. chain detector matches → its phrase
        #    2b. single step in rules → rule(kwargs, outcome)
        #    2c. multi-step where all steps in rules → last-step-wins rule
        #    2d. any step outside rules → LLM fallback (or raw fallback if disabled)
        # 3. status=error:
        #    3a. LLM fallback with full outcome (transcript + failed_step + error_msg)
        #    3b. LLM unavailable or llm_fallback_enabled=false → f"{failed_step.name} failed"
        # 4. LLM returns empty / >80 chars → truncate or raw-rule fallback
```

### 4.5 `summary_rules` (pure data + detectors)

```python
# src/voice_sprite/summary_rules.py
RULES: dict[str, Callable[[dict, PlanOutcome], str]] = {
    "focus":        lambda kw, o: f"focused {kw['target']}",
    "minimize":     lambda kw, o: "minimized window",
    "maximize":     lambda kw, o: "maximized window",
    "close":        lambda kw, o: "closed tab",
    "close_window": lambda kw, o: "closed window",
    "open":         lambda kw, o: f"opened {kw['target']}",
    "type":         lambda kw, o: f'typed "{kw["text"][:30]}"',
    "press":        lambda kw, o: f"pressed {kw['combo']}",
    "wait":         lambda kw, o: f"waited {kw['ms']}ms",
    "click":        lambda kw, o: "clicked",
    "scroll":       lambda kw, o: f"scrolled {kw.get('direction', '')}",
    "no_match":     lambda kw, o: "no match",
}

# Chain detectors — return summary str or None
def detect_search_chain(steps: tuple[ToolCall, ...]) -> str | None:
    # Pattern: focus(browser) → ctrl+t → ctrl+l → type(query) → enter
    # Emits: f'searched {browser} for "{query}"'
    ...

CHAIN_DETECTORS: list = [detect_search_chain]
```

Covers the nine-verb catalog (ADR 0043) plus bonus `scroll`. Adding a new verb means adding one row + one unit test — lowest friction.

### 4.6 `LLMSummaryClient`

```python
# src/voice_sprite/llm_summary_client.py
class LLMSummaryClient:
    def __init__(self, endpoint_url: str, model_id: str, timeout_ms: int) -> None: ...
    def summarize(self, outcome: PlanOutcome) -> str | None: ...
        # system: "Summarize this Windows voice command outcome in ≤6 words,
        #         past tense. Include failure reason if status=error."
        # user: json.dumps(outcome.to_event_dict())
        # tool_choice="none", max_tokens=24, temperature=0
        # Returns None on any error / timeout / empty result
```

Reuses daemon's LM Studio endpoint by default. Separate timeout (`[hud].llm_summary_timeout_ms = 800`) — must not share `[llm].timeout_ms` since summarization is off the hot path.

### 4.7 `ChatLog` + `ChatLogEntry`

```python
# src/voice_sprite/chat_log.py
@dataclass
class ChatLogEntry:
    text: str
    status: Literal["ok", "error", "miss"]
    born_at_s: float           # time.monotonic()

class ChatLog:
    def __init__(self, max_lines: int, hold_ms: int, fade_ms: int) -> None: ...
    def append(self, entry: ChatLogEntry) -> None: ...     # evicts oldest on overflow
    def tick(self, now_s: float) -> None: ...              # evicts fully-faded
    def entries(self) -> list[ChatLogEntry]: ...           # newest-first
    def opacity_of(self, entry: ChatLogEntry, now_s: float) -> float: ...
```

Ring buffer. Per-entry fade curve: opacity=1.0 for `hold_ms`, then linear 1→0 across `fade_ms`, then 0 and eligible for eviction.

### 4.8 `ChatLogRenderer`

```python
# src/voice_sprite/chat_log_renderer.py
class ChatLogRenderer:
    def __init__(
        self,
        chat_log: ChatLog,
        sprite_renderer: SpriteRenderer,   # for sprite-bounds read
        window: pyglet.window.Window,
        font_size: int,
        width_px: int,
        offset_x: int,
        offset_y: int,
    ) -> None: ...

    def draw(self) -> None: ...            # called from Window.on_draw each frame
```

Stateless beyond its `pyglet.text.Label` pool. Reads `chat_log.entries()` each frame; labels' `color.a` computed from `chat_log.opacity_of(entry, now)`. Positions in window-local coords: HUD region spans `x ∈ [0, window.width - sprite_size]`, `y ∈ [0, window.height]`, newest entry at bottom. When CursorDock calls `window.set_location` / `set_size` the whole window (sprite + HUD + bubble) moves and rescales atomically — no inter-renderer coordination needed.

Color map: `ok`→greenish (`#8fd77f`), `error`→red (`#ff7676`), `miss`→amber (`#ffb562`). All on transparent background. No box, no frame — just glyphs (matches ADR 0050 transparent-framebuffer recipe; any opaque fill would defeat it).

### 4.9 `CursorDock`

Anchors the **sprite's bottom-right corner** (NOT the window's top-left) to `rcWork.bottom-right - margin`. Since the pyglet window is larger than the sprite (it also contains the HUD extending leftward and the speech bubble extending upward), CursorDock computes the window origin from the desired sprite origin.

```python
# src/voice_sprite/cursor_tracker.py
class CursorDock:
    def __init__(
        self,
        window: pyglet.window.Window,
        sprite_base_size_px: int,      # charsheet cell size before DPI scale
        window_extra_w_px: int,        # HUD width that extends left of sprite
        window_extra_h_px: int,        # speech-bubble height that extends up from sprite
        margin_x: int,
        margin_y: int,
    ) -> None: ...

    def tick(self, _dt: float) -> None: ...
        # 1. GetCursorPos → POINT; if (0,0) & workstation locked → return
        # 2. MonitorFromPoint(MONITOR_DEFAULTTONEAREST) → HMONITOR
        # 3. If hmon == self._last_hmon: return          (hysteresis)
        # 4. GetMonitorInfoW(hmon) → MONITORINFO.rcWork
        # 5. GetDpiForMonitor(hmon, MDT_EFFECTIVE_DPI) → dpi
        # 6. scale = dpi / 96
        #    sprite_size = int(sprite_base_size_px * scale)
        #    extra_w = int(window_extra_w_px * scale)
        #    extra_h = int(window_extra_h_px * scale)
        #    window_w = sprite_size + extra_w
        #    window_h = sprite_size + extra_h
        #    window.set_size(window_w, window_h)
        # 7. # Anchor: sprite bottom-right = rcWork bottom-right - margin.
        #    # Sprite sits at the bottom-RIGHT of the window; HUD extends LEFT;
        #    # speech bubble extends UP. So window top-left is:
        #    x = rcWork.right  - window_w - margin_x + extra_w   # shift right by extra_w
        #                                                         # so sprite (not HUD) is flush right
        #                                                         # against the margin
        #    y = rcWork.bottom - window_h - margin_y              # sprite sits at window bottom;
        #                                                         # extra_h is above sprite
        # 8. window.set_location(x, y)
        # 9. self._last_hmon = hmon
        #10. Fire on_window_moved hook so SpriteRenderer / ChatLogRenderer
        #    re-read window dimensions for their next draw() call.
```

`window_extra_w_px` and `window_extra_h_px` are read from `cfg.hud.width_px` (+ offset margin) and `cfg.sprite.speech_bubble_height_px` at CursorDock construction. If `[hud].enabled = false`, extras collapse to zero and the window is sprite-sized.

SpriteRenderer and ChatLogRenderer position their content in window-local coords: sprite at `(extra_w, 0)` → `(window_w, sprite_size)`; HUD at `(0, ...)` → `(extra_w - offset_margin, window_h)`. Both re-query `window.width / window.height` each frame so resize-on-DPI-change is picked up automatically.

Scheduled via `pyglet.clock.schedule_interval(dock.tick, 1 / cfg.sprite.follow_poll_hz)` during `__main__` wiring. Default 30 Hz. Ctypes-only — no new pywin32 import on sprite side (keeps startup light).

### 4.10 `dpi` module extension

Add `get_dpi_for_monitor(hmon: int) -> float` wrapping `GetDpiForMonitor`. Audit the existing `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)` call site — it MUST run before the first pyglet window is constructed, otherwise the sprite HWND is locked to system-DPI awareness and `set_location` coords get silently rescaled on monitor cross. Move the call to sprite `__main__` startup if not already there.

## 5. Data flow (happy path — "minimize")

1. User presses Scroll Lock → session opens → speaks "minimize" → Scroll Lock again
2. VAD detects utterance, transcriber returns `text="minimize" confidence=0.82`
3. Confidence gate passes. `LLMRouter.route()` returns `Plan(steps=(ToolCall("minimize", {}),))`
4. `Dispatcher.run_plan()`:
   - publishes `tool_fired {name="minimize"}` (existing)
   - runs `minimize_window()` (succeeds)
   - publishes `plan_outcome {transcript="minimize", steps=[{name:"minimize", kwargs:{}}], status:"ok", failed_step_index:null, error_msg:null, duration_ms:127}`
5. Sprite's httpx-sse client receives `plan_outcome`:
   - reconstructs `PlanOutcome`
   - calls `Summarizer.summarize(outcome)` → rule path → `"minimized window"`
   - builds `ChatLogEntry(text="minimized window", status="ok", born_at_s=now())`
   - appends to `ChatLog`
6. Next `on_draw` frame:
   - `ChatLogRenderer.draw()` reads `chat_log.entries()`, renders one green label `"minimized window"` at sprite-relative position with opacity 1.0
7. 4s later the hold expires; label fades 1→0 across 3s; at opacity 0 the entry is evicted on the next `tick()`

## 6. Data flow (failure paths)

- **Miss — LLM down:** `LLMRouter.route()` returns None. Daemon publishes `plan_outcome{status:"miss", steps:[]}`. Sprite `Summarizer` returns `"no match"`. HUD entry rendered amber.
- **Miss — confidence gate:** transcription confidence < `min_confidence`. Daemon publishes `plan_outcome{status:"miss", steps:[], transcript:result.text}`. Summarizer returns `"no match"` (same phrase; transcript surfaces in logs and `last_plan.json` for post-mortem).
- **Error — step raised:** `minimize()` throws `FocusWindowError`. Dispatcher publishes `plan_outcome{status:"error", failed_step_index:0, error_msg:"FocusWindowError: no visible window"}`. Summarizer goes to LLM fallback → `"minimize failed — no window"`. LLM unavailable → raw rule fallback `"minimize failed"`. HUD entry rendered red.
- **Error — unknown tool:** LLM hallucinated a verb. Dispatcher publishes `plan_outcome{status:"error", error_msg:"unknown tool: foo"}`. Summarizer goes to LLM fallback. HUD entry red.
- **SSE disconnect mid-plan:** existing `event_client.py` reconnect loop handles it. On reconnect, ring-buffer replay via `Last-Event-ID` delivers missed `plan_outcome` events; sprite renders them even if late.
- **Workstation locked:** `GetCursorPos` returns `(0,0)` with success. `CursorDock.tick` detects + returns early; sprite parks until unlock.
- **Monitor unplugged:** `MonitorFromPoint` may return null briefly. Tick skips; retries next frame.
- **Mixed-DPI cross:** step 6 recomputes size per monitor's DPI; sprite stays visually consistent instead of shrinking/growing.

## 7. Config surface

```toml
[sprite]
# Existing keys unchanged (render_scale, y_nudge_px, charsheet, …)
follow_cursor       = true
follow_poll_hz      = 30        # cursor-poll rate
margin_x            = 8         # physical px, subtracted from rcWork.right
margin_y            = 8         # physical px, subtracted from rcWork.bottom

[hud]
enabled             = true      # master toggle; false = no renderer, no SSE consumption
max_lines           = 5         # ring buffer cap
hold_ms             = 4000      # full opacity duration
fade_ms             = 3000      # linear 1→0 after hold
font_size           = 13
width_px            = 220
offset_x            = -230      # relative to sprite left edge (negative = HUD sits to left)
offset_y            = 0         # relative to sprite top
llm_summary_timeout_ms = 800    # per-summary LLM deadline (independent of [llm].timeout_ms)
llm_fallback_enabled   = true   # false = skip LLM fallback, always use raw rule
```

Defaults ship sensible. Escape hatches available but invisible unless the user wants them.

## 8. Testing

### Unit tests

| File | Covers |
|---|---|
| `test_plan_outcome.py` | Dataclass frozen; `to_event_dict` / `from_event_dict` round-trip; field defaults |
| `test_dispatcher.py` (extend) | Publishes `plan_outcome` on success/error/unknown-tool; correct `failed_step_index`; `duration_ms` monotonic |
| `test_streaming_daemon.py` (extend) | Publishes `plan_outcome status=miss` on `route()=None`; publishes on confidence-gate drop; does NOT publish on word-count or no-speech-prob drop |
| `test_chat_log.py` | Ring buffer caps at `max_lines`; `tick()` evicts fully-faded; `entries()` newest-first; opacity curve |
| `test_summary_rules.py` | Every catalog verb has a rule; every rule returns non-empty; `detect_search_chain` fires on canonical pattern, ignores near-misses |
| `test_summarizer.py` | miss bypasses LLM; all-catalog plan uses rules only (LLM client not called); unknown verb → LLM; error → LLM; LLM timeout → raw fallback; LLM empty → raw fallback; LLM >80 chars → truncated |
| `test_llm_summary_client.py` | httpx mocked; connect error / timeout → None; 200 + valid → str; malformed JSON → None |
| `test_chat_log_renderer.py` | Positions relative to sprite bounds; color map by status; label count = entry count; opacity matches curve |
| `test_cursor_tracker.py` | Monkeypatch ctypes; hysteresis (tick no-op when hmon unchanged); docking math for taskbar-bottom/top/left/right; negative-coord virtual screen; DPI rescale triggers `set_size` |
| `test_sprite_config.py` (extend) | `[hud]` + new `[sprite]` keys parsed with defaults; `enabled=false` disables wiring; `follow_cursor=false` disables CursorDock schedule |

### Integration tests

| File | Covers |
|---|---|
| `test_plan_outcome_sse.py` | Inject utterance end-to-end → daemon publishes correctly-shaped `plan_outcome` over `/events` |
| `test_sse_endpoint.py` (extend) | `plan_outcome` survives `Last-Event-ID` reconnect replay |

### HITL gates (Phase close)

1. Say "minimize" with LM Studio up → green `"minimized window"` in HUD
2. Say "open sptfy" (typo) → amber `"no match"` in HUD
3. Say "search cats" → HUD shows `'searched Chrome for "cats"'`
4. Stop LM Studio → miss chime + amber `"no match"`. Restart → recover without sprite restart
5. Stop sprite mid-session → daemon keeps dispatching. Restart sprite → fresh buffer, new entries appear
6. Fire 10 commands rapid-fire → ring buffer caps at `max_lines`, oldest eviction is clean
7. Drag cursor across monitors → sprite snaps to bottom-right of active monitor's work area; HUD stays attached
8. Move Windows taskbar to top / left / right → sprite still docks above-and-inside work area (no overlap with taskbar)
9. Mixed-DPI monitors → sprite rescales across boundary; HUD labels rescale with it
10. Lock workstation → sprite parks; unlock → sprite resumes following cursor
11. `[sprite].follow_cursor = false` → sprite static at primary bottom-right
12. `[hud].enabled = false` → no HUD visible; sprite still follows cursor

### Coverage

80% floor enforced by `pyproject.toml` applies. `chat_log_renderer.py` and `cursor_tracker.py` may need `[tool.coverage.run] omit` exclusion since pyglet-bound and ctypes-bound code is hardware-flavored — decide at implementation time based on how much logic mocks cleanly.

## 9. File plan

**New:**

```
src/voice_commander/
└── plan.py                                     # add PlanOutcome

src/voice_sprite/
├── chat_log.py
├── chat_log_renderer.py
├── summarizer.py
├── summary_rules.py
├── llm_summary_client.py
└── cursor_tracker.py

docs/decisions/
├── 0051-command-hud-overlay.md
├── 0052-hybrid-rule-llm-summarization.md
└── 0053-sprite-follows-cursor-monitor.md

tests/unit/
├── test_plan_outcome.py
├── test_chat_log.py
├── test_summarizer.py
├── test_summary_rules.py
├── test_llm_summary_client.py
├── test_chat_log_renderer.py
└── test_cursor_tracker.py

tests/integration/
└── test_plan_outcome_sse.py
```

**Modified:**

| File | Change |
|---|---|
| `src/voice_commander/dispatcher.py` | Outcome tracking + publish `plan_outcome` |
| `src/voice_commander/daemon.py` | Publish `plan_outcome status=miss` on `route()=None` and confidence-gate drop |
| `src/voice_commander/plan.py` | Add `PlanOutcome` |
| `src/voice_sprite/event_client.py` | Handle `plan_outcome` event → Summarizer → ChatLog |
| `src/voice_sprite/window.py` | Instantiate + draw `ChatLogRenderer` |
| `src/voice_sprite/__main__.py` | Wire ChatLog/Summarizer/LLMSummaryClient/CursorDock; replace hardcoded positions dict |
| `src/voice_sprite/config.py` | `[hud]` section + new `[sprite]` keys |
| `src/voice_sprite/dpi.py` | `get_dpi_for_monitor()`; audit PMv2 call ordering |
| `config.toml` | Default `[hud]` + new `[sprite]` keys |
| `docs/architecture.md` | §10 Command HUD; §11 Cursor-follow sprite; updated sprite diagram |
| `docs/agents/technical-decisions.md` | Rows for ADR 0051/0052/0053 |
| `docs/gotchas.md` | Win11 multi-monitor / DPI / rcWork gotchas |
| `README.md` | HUD description + follow-cursor feature + config knob tables |

## 10. Open questions

None remaining. All architecture, data-shape, failure-path, config, and test decisions are nailed down above. Implementation plan can proceed.

## 11. See also

- ADR 0043 — Nine-verb primitive catalog (drives the rule table contents)
- ADR 0045 — Sprite separate process via SSE (unchanged — HUD does not move that line)
- ADR 0048 — EventBus + SSE outbound telemetry (the transport `plan_outcome` rides on)
- ADR 0049 — Miss chimes retained (HUD supplements, does not replace)
- ADR 0050 — Pyglet transparent-overlay Windows recipe (HUD + sprite share this framebuffer)
- `docs/architecture.md` §7 EventBus, §8 Sprite Companion
