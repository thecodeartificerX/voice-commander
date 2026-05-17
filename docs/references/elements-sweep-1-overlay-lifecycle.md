# Elements-Mode Overlay Lifecycle — Adversarial Sweep #1

**Scope:** `src/voice_sprite/elements_overlay.py`, `src/voice_sprite/__main__.py` (overlay
controller), `src/voice_sprite/window.py` (SpriteWindow), `src/voice_sprite/picker_modal.py`
(show/hide reference).  
**Symptom under investigation:** Overlay works on first `elements` invocation; on second it
renders an opaque black background and stops working.

---

## Findings (ranked by likelihood of being THE bug)

---

### FINDING 1 — `_hide_elements_now()` + `ElementsOverlayWindow()` in the same callback is a DWM timing bomb

**File:** `src/voice_sprite/__main__.py`, lines 228–242  
**Severity:** CRITICAL — top suspect for works-once-then-black  

**Mechanism:**

`_create()` (the `schedule_once` callback) calls `_hide_elements_now()` synchronously and then
immediately calls `ElementsOverlayWindow(monitor, elements)` in the very next statement. When
an overlay already exists (i.e. user says "elements" twice without a hide in between),
`_hide_elements_now()` calls `overlay1.close()`.

`overlay1.close()` triggers:

1. `Win32Window.close()` → `DestroyWindow(hwnd1)` → `UnregisterClassW(...)`.
2. `BaseWindow.close()` → `context.destroy()` → `wglDeleteContext(overlay1._wgl_context)`.
3. `app.event_loop.dispatch_event('on_window_close', overlay1)` is sent **synchronously**.

DWM processes `WM_DESTROY` **asynchronously** — it runs in a separate system process
(`dwm.exe`) and consumes the destruction notification from a message queue. There is no
synchronisation point that forces DWM to finish processing overlay1's destruction before the
next Win32 call runs.

Immediately after `_hide_elements_now()` returns, `ElementsOverlayWindow(...)` is constructed.
Inside pyglet's `Win32Window._create()` (line 142-143 of the pyglet win32 backend):

```python
elif self.style == 'transparent' or self.style == 'overlay':
    self._set_transparency()   # DwmEnableBlurBehindWindow + SetLayeredWindowAttributes(LWA_ALPHA)
```

`_set_transparency()` calls `SetLayeredWindowAttributes(hwnd2, 0, 255, LWA_ALPHA)` on the
brand-new HWND2. DWM receives this call while it may still be processing the destruction of
HWND1 (which was a full-monitor overlay on the same display). On Windows 11 DWM can coalesce
or reorder these notifications, especially for full-monitor overlays where it applies
full-screen-optimisation heuristics.

`apply_win32_flags()` is then called from `_create()` and re-invokes
`DwmEnableBlurBehindWindow(top_hwnd2, bb)` to restore per-pixel alpha. This call races against
DWM's asynchronous processing of overlay1's destruction. If DWM has not yet flushed overlay1's
DWM state by the time it processes overlay2's `DwmEnableBlurBehindWindow`, the internal DWM
per-window record for the new HWND may be in an undefined intermediate state, and the
`fEnable=True` flag is silently ignored or reverted. Result: `SetLayeredWindowAttributes(LWA_ALPHA)`
dominates, and DWM composites the entire window against opaque black.

**Why first invocation is immune:** On first invocation there is no prior overlay to close.
`_hide_elements_now()` is a no-op. DWM sees only the creation path, no destruction race.

**Contrast with picker:** `PickerModalWindow` creates a `_PygletModalWindow` **once** with
`visible=False`, caches it in `self._window`, and then calls `set_visible(True/False)` for
subsequent show/hide operations (`_do_show` / `_do_hide` in `picker_modal.py` lines 172–191).
It **never** calls `window.close()`. There is no destroy+recreate sequence, so no DWM race.

**Recommended fix:** Adopt the picker's pattern. Create a single `ElementsOverlayWindow`
instance on first use and reuse it. On `elements.hide`, call
`overlay.set_visible(False)`. On `elements.show`, call `overlay.set_visible(True)` and update
the tag geometry. This eliminates the destroy+recreate path entirely and the DWM race with it.

If destroy+recreate must be kept: insert a `schedule_once(_create, delay=0.05)` inside
`_hide_elements_now()` rather than creating the new window in the same callback, giving DWM at
least one full event-loop tick (≈ 16 ms) to process the destruction before creation begins.

---

### FINDING 2 — `_set_transparency()` poisons every new overlay; recovery depends entirely on `apply_win32_flags()` not being skipped

**File:** `src/voice_sprite/elements_overlay.py` line 114 (via pyglet `super().__init__()`);  
**File:** `src/voice_sprite/__main__.py` lines 240–241  
**Severity:** HIGH — explains the opaque-black symptom if `apply_win32_flags` is skipped

**Mechanism:**

pyglet's `Win32Window._create()` calls `self._set_transparency()` for every
`WINDOW_STYLE_OVERLAY` window. `_set_transparency()` does:

```python
DwmEnableBlurBehindWindow(self._hwnd, bb)             # enables per-pixel alpha -- good
SetLayeredWindowAttributes(self._hwnd, 0, 255, LWA_ALPHA)  # switches to constant-alpha -- BAD
```

As documented in `win32_flags.py` line 88-90, `SetLayeredWindowAttributes(LWA_ALPHA)` after
`DwmEnableBlurBehindWindow` switches the window from per-pixel-DWM mode to constant-alpha
mode, making DWM composite the entire window against opaque black regardless of
`glClearColor`.

`apply_win32_flags()` in `__main__.py` line 241 calls `DwmEnableBlurBehindWindow` again to
restore per-pixel mode. This is the **only** call that makes the overlay transparent. If
`apply_win32_flags()` is not reached for any reason (exception from `ElementsOverlayWindow.__init__`,
early `return`, logic change), every overlay — first or second — will be black.

**Critical path dependency:** `apply_win32_flags()` is called from `__main__._create()` after
`ElementsOverlayWindow(...)` returns. There is no `try/finally` ensuring it runs even if
`__init__` raises. If pyglet raises internally (e.g., `wglCreateContext` failure, font texture
allocation failure, etc.) on the second overlay, `apply_win32_flags()` is silently skipped.
pyglet's `clock.call_scheduled_functions()` does **not** wrap callbacks in `try/except` — a
raised exception propagates up and terminates the event loop entirely. However, if the window
IS created successfully but `apply_win32_flags` is not reached due to a logic error, a black
window is the result.

**Recommended fix:** Wrap in `try/finally`:

```python
overlay = ElementsOverlayWindow(monitor, elements)
try:
    overlay.apply_win32_flags()
finally:
    _elements_overlay.append(overlay)
```

More robustly: move `apply_win32_flags()` inside `ElementsOverlayWindow.__init__()` so it is
always called as part of construction (as `SpriteWindow.apply_win32_flags()` is called from
`main()` but is still explicitly coupled to construction in the startup sequence).

---

### FINDING 3 — `apply_win32_flags()` applies Win32 extended styles to the VIEW child HWND, not the top-level parent HWND

**File:** `src/voice_sprite/elements_overlay.py` lines 182–187, 199–206  
**File:** `src/voice_sprite/win32_flags.py` lines 89–93  
**Severity:** MEDIUM — causes incorrect/ineffective style flags; does not directly cause black window but means click-through/topmost/noactivate flags are silently misapplied

**Mechanism:**

`ElementsOverlayWindow._overlay_hwnd()` returns `self.canvas.hwnd`. `canvas` is a
`Win32Canvas` constructed with `self._view_hwnd` (the child view window), not `self._hwnd`
(the top-level parent). `canvas.hwnd` is therefore the **child** HWND.

`apply_click_through()` in `win32_flags.py` receives the child HWND and runs:

```python
style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)   # reads child HWND styles
style |= WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)     # sets styles on child HWND
user32.SetWindowPos(hwnd, HWND_TOPMOST, ...)        # makes child HWND topmost
```

`WS_EX_TOOLWINDOW` (no taskbar entry) and `WS_EX_NOACTIVATE` (no focus steal) must be set on
the **top-level** parent `HWND` to have any effect. Applying them to a child window is a
no-op. Similarly, `SetWindowPos` for Z-ordering applies to the top-level.

Only the `GetAncestor(hwnd, GA_ROOT)` call before `DwmEnableBlurBehindWindow` correctly
walks up to the top-level. That one call works; all the others do not.

**Note on SpriteWindow:** `SpriteWindow.apply_win32_flags()` in `window.py` lines 299-300
attempts `self.canvas.hwnd` but falls back to `getattr(self, '_hwnd', None)`. The same HWND
path issue exists there but is documented as a known limitation.

**Recommended fix:** In `ElementsOverlayWindow._overlay_hwnd()`, return `self._hwnd`
(the top-level) directly:

```python
def _overlay_hwnd(self) -> int | None:
    hwnd = getattr(self, '_hwnd', None)
    if hwnd is None:
        hwnd = self.canvas.hwnd if hasattr(self, 'canvas') and self.canvas else None
    return hwnd
```

---

### FINDING 4 — `_elements_overlay` list can silently hold a stale closed-window reference between the `pop()` and the end of `_hide_elements_now()`

**File:** `src/voice_sprite/__main__.py` lines 219–225  
**Severity:** LOW — benign in practice but masks double-close risk

**Mechanism:**

```python
def _hide_elements_now() -> None:
    while _elements_overlay:
        window_to_close = _elements_overlay.pop()
        try:
            window_to_close.close()
        except Exception:
            pass
```

`_elements_overlay.pop()` removes the reference before `close()` is called. If `close()`
raises and is swallowed, `_elements_overlay` is now empty but the underlying pyglet window
is in an unknown state (partially closed). `_show_elements._create()` will call
`_hide_elements_now()` next time and find the list empty, so it will create a new overlay
without cleaning up the half-alive window.

In practice, `close()` almost never raises on Windows. But if it does (e.g., HWND already
destroyed by an external force), the leak is silent.

**Recommended fix:** This is not directly related to the works-once-then-black bug. It is a
defensive hygiene issue. The actual risk would be higher if the list could hold more than one
entry, but the current code ensures it holds at most one.

---

### FINDING 5 — No explicit `self.switch_to()` before GL state calls in `ElementsOverlayWindow.__init__`

**File:** `src/voice_sprite/elements_overlay.py` lines 127–135  
**Severity:** LOW — latent risk; currently safe due to pyglet's internal `switch_to()` guarantee

**Mechanism:**

`ElementsOverlayWindow.__init__` calls `glEnable(GL_BLEND)`, `glBlendFuncSeparate`, and
`glClearColor` immediately after `super().__init__()` with no explicit `self.switch_to()` to
guard the calls. This relies on the invariant that `super().__init__()` leaves the new
window's context as current (enforced by pyglet's `BaseWindow.__init__` at line 156: explicit
`self.switch_to()`, and by `Win32Window._create()` at its line 159: `self.switch_to()`).

This invariant holds in the current pyglet version and is the same pattern used by
`SpriteWindow`. However, it is fragile: any future pyglet change that defers context
activation, or any platform where `set_visible(True)` causes a context switch to a different
window, would silently direct GL state to the wrong context, resulting in the overlay drawing
correctly (its context is untouched) but with wrong blend mode — which on Windows DWM
manifests as opaque black or wrong compositing.

**Recommended fix:** Add an explicit guard:

```python
super().__init__(...)
self.switch_to()  # ensure GL state calls below land on THIS context
gl = pyglet.gl
gl.glEnable(gl.GL_BLEND)
...
```

This matches what `PickerModalWindow.refresh()` does explicitly (line 345: `self.switch_to()`
before any GL work).

---

### FINDING 6 — Potential unordered execution of `_hide` and `_create` callbacks scheduled with `delay=0.0` in the same SSE burst

**File:** `src/voice_sprite/__main__.py` lines 227–247  
**Severity:** LOW — edge case, does not explain the primary symptom

**Mechanism:**

`_hide_elements()` and `_show_elements()` both use `pyglet.clock.schedule_once(..., 0.0)`.
Callbacks scheduled with `delay=0.0` in the same clock tick get equal `next_ts`. pyglet's
clock uses a `heapq` with `_ScheduledIntervalItem.__lt__` comparing only `next_ts`. Python's
`heapq` is **not** stable for equal-priority items; the execution order of two callbacks with
the same `next_ts` is implementation-defined.

If the SSE thread delivers `elements.hide` followed immediately by `elements.show` (e.g., for
a rescan/refresh), both schedule_once calls may land in the same heap position. If `_create`
fires before `_hide_elements_now`, the correct overlay is created and then immediately destroyed
by the hide callback — resulting in no visible overlay, not a black one. This does not match
the "opaque black" symptom but could be confused with "not working" by an observer.

**Recommended fix:** `_show_elements._create()` already calls `_hide_elements_now()` at the
top, so it is self-contained. The issue only arises if `_hide` fires AFTER `_create` completes.
This is solved by the picker-pattern fix (Finding 1): if show/hide are `set_visible` calls,
ordering ambiguity has no destructive effect.

---

## Summary table

| # | Location | Severity | Causes black window? |
|---|----------|----------|----------------------|
| 1 | `__main__.py` _create lines 228–242 | CRITICAL | Yes — DWM async race on destroy+recreate |
| 2 | `__main__.py` line 241 / `elements_overlay.py` line 114 | HIGH | Yes — if apply_win32_flags not reached |
| 3 | `elements_overlay.py` lines 182–206 | MEDIUM | No — style flags go to wrong HWND |
| 4 | `__main__.py` lines 219–225 | LOW | No — benign double-close hygiene |
| 5 | `elements_overlay.py` lines 127–135 | LOW | Latent — GL state to wrong context |
| 6 | `__main__.py` lines 227–247 | LOW | No — causes missing overlay, not black |

---

## Top suspect — Finding 1

The works-once-then-black symptom is most consistent with **Finding 1**: the destroy+recreate
path inside a single `schedule_once` callback triggers a DWM timing race.

- First invocation: no prior overlay → `_hide_elements_now()` is a no-op → `ElementsOverlayWindow`
  created cleanly → `DwmEnableBlurBehindWindow` in `apply_win32_flags` operates on a DWM
  context that has never seen a competing destruction → **works**.
- Second invocation (show without prior hide, or show-after-rapid-hide): `_hide_elements_now()`
  calls `overlay1.close()` → `DestroyWindow` sends DWM a destruction message → before DWM
  processes it, `ElementsOverlayWindow.__init__` creates a new HWND and `_set_transparency()`
  calls `SetLayeredWindowAttributes(LWA_ALPHA)` → `apply_win32_flags()` calls
  `DwmEnableBlurBehindWindow` to fix it → but DWM receives these two operations while still
  processing HWND1's destruction, and may treat the `fEnable=True` call as part of the
  destruction-flush rather than a fresh enable on HWND2 → **`SetLayeredWindowAttributes` wins,
  DWM composites black**.

**The fix is to adopt the picker pattern: one window instance, `set_visible(True/False)`.** This
is the structural change that eliminates the race, eliminates Finding 2's dependency chain, and
aligns the overlay lifecycle with the only window in the codebase (`PickerModalWindow`) that
survives repeated show/hide cycles without breaking.
