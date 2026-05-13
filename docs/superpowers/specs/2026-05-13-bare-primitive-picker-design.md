# Bare-Primitive Picker Framework — Design Spec

**Date:** 2026-05-13
**Status:** Approved, pending implementation plan
**Related ADRs to author:** `docs/decisions/0083-bare-primitive-picker.md`

## Problem

Today, a bare primitive verb said without an argument is a soft miss:

- `focus` alone → `VerbRouter` sees `head="focus"`, no tail, `raw_tail_tool` requires a tail, returns `None` → miss chime.
- `open` alone → same path → miss chime.
- `press` / `type` / `scroll` / `click` / `wait` — same structural shape; bare invocation is either no-op (`click`, `scroll` default to a target action) or a miss.

That is a wasted affordance. A bare verb is the user saying "I want to do *this kind of thing*, but I have not picked the target yet — help me." A power-user voice launcher should answer with an inline disambiguation rather than punishing the user with a chime.

User intent (verbatim, captured during brainstorming): say "focus" alone → a numbered list of recent windows appears on screen → say the number → that window is focused → picker closes → session continues. Same pattern should generalize: future "browser tab select primitive" or "open" with no target should plug into the same framework with no new infra.

## Goal

Ship a **pluggable bare-primitive picker framework** plus a single registered provider for `focus`. Concretely:

1. When a registered primitive verb is uttered without an argument, instead of returning `None`, route to a registered `BarePickerProvider` that produces a numbered list of candidate `PickerItem`s.
2. Render the list as a centered, always-on-top modal on the active monitor via the existing sprite process (pyglet, click-through, voice-only).
3. The next utterance in the same voice session is intercepted by the daemon's pipeline, coerced to an integer ("three" / "3" / "number 3"), and dispatched as that item's pre-bound `Plan`.
4. The original voice session continues after selection (picker is a sub-state, not a new session).
5. Adding a new picker later (e.g. for a `tab` primitive) is one file: register a provider, framework handles the rest.

Non-goals (deferred):

- Tab / browser-tab pickers, OCR-based pickers, screen-region pickers — out of scope for this slice. Framework must accommodate them; we do not ship them.
- Mouse / keyboard fallback selection in the modal — voice-only by design.
- Persistent MRU across daemon restarts — in-memory only.

## Decisions (locked during brainstorming)

| # | Decision | Choice |
|---|---|---|
| D1 | Picker UI surface | Centered always-on-top modal on active monitor |
| D2 | Item cap | 5 |
| D3 | Source list strategy | MRU ring buffer driven by `WinEventHook` on `EVENT_SYSTEM_FOREGROUND` |
| D4 | Picker session semantics | Sub-state of the running voice session (mic stays hot; original session continues after selection) |
| D5 | Generalization shape | Pluggable `BarePickerProvider` registry, `@bare_picker(verb)` decorator |
| D6 | Render path | Daemon owns state; sprite process renders modal via SSE (`picker.open` / `picker.close`) |

Defaults locked without explicit user prompt (justified inline, override-able in TOML):

- Cancel words: `["cancel", "nevermind", "stop"]` — common spoken aborts.
- Timeout: 5 s of silence → auto-close with a miss chime.
- Out-of-range number / non-number while picker active → miss chime, picker stays open until timeout or cancel.
- Pressing Scroll Lock during picker → abort, close picker, end session normally (preserves the user's universal "stop everything" muscle memory).
- MRU filters out: current foreground hwnd, the daemon's own admin window, the sprite window, the picker modal window.

## Architecture

### One-line flow under the picker framework

```
HotkeyCtrl ─toggle─▶ StreamingRecorder ─ndarray─▶ Transcriber ─text─▶ Pipeline ─┬─ picker.active? → coerce → dispatch
  pynput            sounddevice + soxr +          faster-whisper                │
                    silero-vad                    (CUDA, small.en)              └─ else → VerbRouter ─┬─ bare verb + provider registered → __picker.open
                                                                                                     └─ else → existing path

PickerSession ───EventBus("picker.open", items)───▶ sprite ─▶ picker_modal.show()
PickerSession ───EventBus("picker.close")───────────▶ sprite ─▶ picker_modal.hide()

MruTracker (WinEventHook EVENT_SYSTEM_FOREGROUND) ─push hwnd─▶ ring buffer (cap = 5 + filtered)
                                                                       │
                                                              focus_picker() reads top N
```

### Component map

```
src/voice_commander/picker/
├─ __init__.py
├─ types.py        # PickerItem, PickerProvider type alias
├─ registry.py     # BarePickerRegistry + @bare_picker decorator + auto-discovery
├─ session.py      # PickerSession state machine (active, items, opened_at, timer)
├─ mru.py          # MruTracker — SetWinEventHook + ring buffer + filter rules
└─ coerce.py       # coerce_number(text, max_n) -> int | None

src/voice_commander/tools/
└─ focus_picker.py # @bare_picker("focus") def focus_picker() -> list[PickerItem]

src/voice_commander/verb_router.py    # edit: bare-verb + registry-hit → synthetic Plan to __picker.open
src/voice_commander/daemon.py         # edit: pipeline intercepts transcripts while picker.active

src/voice_sprite/picker_modal.py      # pyglet always-on-top centered window
src/voice_sprite/event_client.py      # edit: handle picker.open / picker.close events
```

### Subsystem contracts

**`picker.types.PickerItem`**

```python
@dataclass(frozen=True)
class PickerItem:
    label: str              # what the modal shows next to the number (e.g. "Chrome — voice-commander")
    action: Plan            # the Plan dispatched when the user picks this item
```

**`picker.types.PickerProvider`**

```python
PickerProvider = Callable[[], list[PickerItem]]
```

A zero-arg callable. The framework calls it the moment a bare verb is matched; the provider returns a fresh list (no caching — every open re-queries MRU / state). Empty list is legal; the framework will treat it as "nothing to pick" and chime miss.

**`picker.registry.BarePickerRegistry`**

```python
class BarePickerRegistry:
    def register(self, verb: str, provider: PickerProvider) -> None: ...
    def get(self, verb: str) -> PickerProvider | None: ...
    def has(self, verb: str) -> bool: ...
    def verbs(self) -> tuple[str, ...]: ...
```

Plus a module-level `@bare_picker(verb: str)` decorator that registers on the singleton registry. Discovery follows the existing `tools/` auto-import pattern.

**`picker.session.PickerSession`**

```python
class PickerSession:
    def open(self, verb: str, items: list[PickerItem]) -> None: ...
    def close(self) -> None: ...
    def cancel(self) -> None: ...                # close + info-status, no dispatch
    def handle_transcript(self, text: str) -> Plan | None: ...  # called by pipeline while active
    def tick(self, now: float) -> None: ...      # called by 1 Hz heartbeat — fires timeout if elapsed
    @property
    def active(self) -> bool: ...
    @property
    def items(self) -> tuple[PickerItem, ...]: ...
```

`handle_transcript` semantics:

- Cancel word match → `close()` + return `None`.
- `coerce_number(text, len(items))` succeeds with `n` in `[1, len(items)]` → return `items[n-1].action`, then `close()`.
- Out-of-range or non-number → miss chime via `FeedbackSink`, return `None` (caller does not dispatch, picker stays open).

State emits `picker.open` and `picker.close` events on the daemon's `EventBus` so the sprite can mirror.

**`picker.mru.MruTracker`**

```python
class MruTracker:
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def top(self, n: int) -> list[dict]: ...     # [{hwnd, pid, proc_name, title}, ...]
```

Implementation: spawn a dedicated message-pump thread that installs `SetWinEventHook(EVENT_SYSTEM_FOREGROUND, ...)`. On each foreground change, push the hwnd to a left-end ring buffer (capacity ≥ cap × 3 to absorb filter losses). `top(n)` walks newest → oldest, filters out:

- Dead hwnds (`win32gui.IsWindow(hwnd)` false)
- Hwnds belonging to the daemon process itself, the sprite process, or the picker modal window (resolved by pid + hwnd registration)
- Current foreground hwnd (`win32gui.GetForegroundWindow()` at query time)

Until `n` survivors or the buffer is exhausted.

**`picker.coerce.coerce_number`**

```python
def coerce_number(text: str, max_n: int) -> int | None: ...
```

Table-driven. Recognizes:

- Bare digits: `"3"`, `"3."`, `"three."`
- Spelled: `"one"` … `"nine"` (cap of 5 means only 1–5 strictly required; build 1–9 anyway)
- Prefixed: `"number three"`, `"option 3"`, `"the third"`, `"third one"`
- Ordinals: `"first"` … `"fifth"`

Returns the integer if it's in `[1, max_n]`, else `None`.

**`tools/focus_picker.py`**

```python
@bare_picker("focus")
def focus_picker() -> list[PickerItem]:
    top = mru_tracker.top(config.picker.focus.cap)
    return [
        PickerItem(
            label=_format_label(entry),     # "Chrome — voice-commander"
            action=Plan(steps=(ToolCall("focus", {"_hwnd": entry["hwnd"]}),), raw_response=...)
        )
        for entry in top
    ]
```

The action `Plan` calls `focus` with a pre-resolved hwnd, bypassing `resolve_window`'s fuzzy match (no re-resolution required — we already have the exact hwnd). This needs the `focus` tool to accept a `_hwnd` shortcut path; covered in implementation.

**`voice_sprite/picker_modal.py`**

A separate pyglet `Window` (sibling of the sprite window, not nested):

- Centered on the active monitor (`MonitorFromPoint(GetCursorPos(), MONITOR_DEFAULTTONEAREST)` work area).
- Always-on-top (`WS_EX_TOPMOST`), click-through (`WS_EX_TRANSPARENT | WS_EX_LAYERED`), tool window (no taskbar entry), no focus-stealing (`SetWindowLongW` styles applied at creation, see `voice_sprite/win32_flags.py` pattern).
- Render: dark panel (matches sprite chat-log palette), yellow "FOCUS — SAY NUMBER" header, numbered rows `1. <label>`, footer `say "cancel" to exit`.
- Shown on `picker.open` SSE, hidden on `picker.close`. Items payload arrives as `{n, label}` only — actions stay daemon-side.

### Daemon pipeline integration

Inside the existing pipeline worker (current location: `daemon.py`, the transcript-handling stage between `Transcriber` and `VerbRouter`):

```python
def handle_transcript(text: str) -> None:
    bus.publish("transcript", {"text": text, ...})
    if picker_session.active:
        plan = picker_session.handle_transcript(text)
        if plan is not None:
            dispatcher.run_plan(plan)
        return
    plan = verb_router.route(text)
    if plan is None:
        feedback.on_miss(text)
        return
    dispatcher.run_plan(plan)
```

The picker session takes precedence over the verb router for as long as it's open. This is the key invariant: while the picker is active, every utterance is *only* a candidate selection — not a new command.

### VerbRouter integration

In `verb_router.py`, after `_match_registered_command` and after primitive lookup:

```python
if not tail and head in self._picker_registry.verbs():
    return Plan(
        steps=(ToolCall("__picker.open", {"verb": head}),),
        raw_response={"router": "verb", "verb": head, "tail": "", "bare_picker": True},
    )
```

`__picker.open` is a daemon-internal tool that calls the registered provider, opens the session, and emits the SSE event. It is hidden from `/graph/palette` and `tools/registry.all_visible()` so authors cannot wire it into graphs.

### Config

```toml
[picker]
enabled = true              # master kill-switch
timeout_sec = 5
cancel_words = ["cancel", "nevermind", "stop"]

[picker.focus]
cap = 5                     # 1-9
exclude_foreground = true
exclude_self = true         # filter daemon + sprite + modal hwnds
```

Wired through the existing `Config` class with sensible defaults so a missing `[picker]` section behaves like the table above.

## Data flow (single utterance lifecycle)

```
1.  user presses Scroll Lock → session opens
2.  user: "focus." → transcript → Pipeline
3.  picker.active? false → VerbRouter.route("focus.")
4.  VerbRouter: head="focus", no tail, registry.has("focus") ✓
5.  → Plan(steps=[ToolCall("__picker.open", {"verb":"focus"})])
6.  Dispatcher.run_plan → __picker.open
7.  provider = registry.get("focus") → focus_picker()
8.  focus_picker reads mru.top(5) → [PickerItem×5]
9.  picker_session.open("focus", items)
10. EventBus.publish("picker.open", {items: [{n:1,label:...}, ...]})
11. sprite SSE handler → picker_modal.show(payload)
12. user: "three" → transcript → Pipeline
13. picker.active? true → picker_session.handle_transcript("three")
14. coerce_number("three", 5) = 3 → return items[2].action
15. Dispatcher.run_plan(action) → focus(_hwnd=...)
16. picker_session.close() → EventBus.publish("picker.close")
17. sprite SSE handler → picker_modal.hide()
18. session still alive → next utterance routed normally
```

## Error handling & edge cases

| Case | Behavior |
|---|---|
| Provider returns empty list | Skip modal entirely. `info-status` "no candidates for focus". Miss chime. Session continues. |
| Cancel word while picker open | `picker_session.cancel()`. Modal hides. No dispatch. Info-status "picker cancelled". |
| Out-of-range number ("seven" w/ 5 items) | Miss chime. Picker stays open until timeout or cancel. |
| Non-number utterance ("hello") | Miss chime. Picker stays open. |
| Silence ≥ `timeout_sec` | Picker auto-closes. Miss chime. Info-status "picker timed out". |
| Scroll Lock pressed while picker open | Abort. Modal hides. Session ends as it normally would. |
| Dead hwnd at dispatch time | `focus` tool raises `FocusWindowError`. Standard error path. `infra` error bucket. |
| Modal already showing (rapid re-trigger) | Idempotent: re-using same `picker_session.open()` resets timer + replaces items. |
| Picker active across hot-reload | Hot-reload of registry must not crash an open session. Session holds its items as a snapshot. |
| Daemon dies while sprite shows modal | Sprite's SSE reconnect loop sees no `picker.open` re-emit; sprite auto-hides modal after 30 s heartbeat-loss. |

## Observability

Add to `observability/errors.py` no new error categories; failures inside the picker flow map to existing buckets (`program` for coercion bugs, `infra` for hwnd-gone-stale).

New span types written to `runs.db`:

- `picker.open` — kwargs `{verb, item_count}`
- `picker.select` — kwargs `{verb, n, label, hwnd}`
- `picker.cancel` — kwargs `{verb, reason}` where `reason ∈ {"word", "timeout", "scroll_lock", "out_of_range_repeated"}`

Existing `tool_fired` / `plan_outcome` spans cover the dispatched `focus(_hwnd=...)` call as usual.

## Testing strategy

Layered against the existing pyramid (`docs/testing-strategy.md`):

**Unit**

- `coerce_number` — table-driven: digits 1–9, words one–nine, "number 3", "the third", "third one", "third option", "three.", "three!", "thirty" (rejected), "" (rejected), out-of-range > max_n (rejected).
- `MruTracker` — fake `SetWinEventHook` callback driver; push N hwnds, assert `top(k)` returns last k in reverse order; assert dead-hwnd filter; assert self-exclusion filter; assert current-foreground filter.
- `BarePickerRegistry` — `@bare_picker("focus")` decoration; duplicate-verb registration error; `has`/`get`/`verbs` round-trip.
- `PickerSession` — state transitions: `open → handle_transcript("three")` → returns action + closes; `open → handle_transcript("hello")` → returns `None` + stays open + emits miss; `open → tick(now+6s)` → timeout closes; `open → cancel()` → closes no dispatch; `open → open` is idempotent.

**Integration**

- `verb_router.route("focus")` with picker registry containing `focus` → returns `Plan` with `__picker.open` step.
- `verb_router.route("focus chrome")` (tail present) → unchanged behavior, routes through `raw_tail`.
- Daemon pipeline harness: enqueue transcript "focus", assert `picker_session.active` becomes true; enqueue "three", assert dispatcher receives correct `focus(_hwnd=...)` call.
- EventBus round-trip: `picker_session.open` → asserts subscriber receives `picker.open` event with items payload.

**End-to-end (manual)**

- Run daemon + sprite. Switch between 5+ apps to populate MRU. Press Scroll Lock, say "focus.", verify modal renders with 5 entries on the active monitor. Say "three.", verify the corresponding window gains focus and the modal closes. Verify the session is still alive by saying another command before pressing Scroll Lock.
- Negative manual cases: bare "focus" with only 1 window open (empty MRU after self-exclusion) → miss chime, no modal; "focus." then "seven." → miss chime, modal stays; "focus." then "cancel." → modal closes silently; "focus." then 5 s silence → modal auto-closes; "focus." then Scroll Lock → modal closes + session ends.

## ADR + docs work

- Author `docs/decisions/0083-bare-primitive-picker.md` capturing D1–D6 with rationale.
- Append summary row to `docs/agents/technical-decisions.md`.
- Update `CLAUDE.md` "Current state" paragraph to describe the picker behavior + framework.
- Update `docs/architecture.md` with the new `picker/` package contract and the sprite modal renderer.
- Update `docs/libraries.md` if any new dependency is introduced (none planned — `pywin32` already covers `SetWinEventHook`).

## Out of scope (deferred features the framework must accommodate)

- **`tab` picker.** When the foreground window is a known browser, show a numbered list of open tabs and switch to the chosen one. Will register as `@bare_picker("tab")` once a tab-enumeration tool exists. No infra change.
- **`open` picker.** Bare `open` shows a numbered shortlist of frequently-opened apps (configurable in TOML). Same shape.
- **OCR-driven pickers.** Click-target pickers built on `ocr_region(...)`. Same shape — the provider just returns whatever items it wants.

## Open questions

None remaining after the brainstorming pass. Implementation plan will pin down:

- Exact threading model for `MruTracker` (dedicated Win32 message pump thread vs. piggybacking on an existing pump).
- Whether `__picker.open` is implemented as a true tool registered in the standard registry (hidden from palette) or a dispatcher special-case. Either works; pick the one that needs the least surface-area change.
- Label formatter rules for `_format_label(entry)` — proc-name vs. window-title preference, truncation length.
