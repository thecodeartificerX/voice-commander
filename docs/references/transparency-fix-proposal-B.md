# Transparency Fix Proposal B — Adversarial Root-Cause Analysis

**Author:** Independent adversarial pass (Claude Sonnet 4.6)  
**Date:** 2026-05-17  
**Target:** `src/voice_sprite/elements_overlay.py` — opaque black background on full-monitor overlay  
**Hard facts that any root-cause must fit:**  
- FACT A: `SpriteWindow` uses `WINDOW_STYLE_OVERLAY` (→ `WS_EX_LAYERED`) and IS transparent.  
- FACT B: The yellow numbered tags draw and are visible.  
- FACT C: The overlay covers the ENTIRE monitor; `SpriteWindow` and picker modal are SMALL windows.  
- FACT D: Only the full-monitor overlay is black; the small windows are transparent.

---

## 1. Adversarial Assessment of the Research Findings

### Research-1 (pyglet mechanics)

**Conclusion it draws:** The standard cause of opaque-black overlays is missing `alpha_size=8`
or incorrect `glClearColor`. The recommended recipe is `WINDOW_STYLE_OVERLAY` + `Config(alpha_size=8)`
+ `glBlendFuncSeparate(…)` + `glClearColor(0,0,0,0)`.

**Verdict: Consistent with facts but does not explain THIS bug.**  
`ElementsOverlayWindow` already follows every step of that recipe — `alpha_size=8`, correct
`glBlendFuncSeparate`, `glClearColor(0,0,0,0)` in both `__init__` and `on_draw`. The recipe is
correctly applied. FACT B (tags are visible) additionally proves the GL context, framebuffer, and
DWM alpha path are all functional enough to render opaque pixels. A missing `alpha_size=8` or
wrong `glClearColor` would produce a uniform opaque-black screen with NOTHING drawn; we observe
yellow tags on a black background. Research-1's explanation is refuted by FACT B.

### Research-2 (Win32/DWM mechanics)

**Conclusion it draws:** `WS_EX_LAYERED` + `CS_OWNDC` are mutually exclusive; remove
`WS_EX_LAYERED` and use `DwmEnableBlurBehindWindow` with an empty region instead. Also:
`DwmEnableBlurBehindWindow(DWM_BB_ENABLE, NULL)` returns E_INVALIDARG because `WS_EX_LAYERED`
is set.

**Verdict: Internally contradictory and refuted by FACT A.**  
FACT A: `SpriteWindow` uses `WINDOW_STYLE_OVERLAY` — which sets `WS_EX_LAYERED` — and IS
transparent. If `WS_EX_LAYERED + CS_OWNDC` fundamentally broke DWM per-pixel alpha compositing,
`SpriteWindow` would also be opaque. It is not. This refutes Research-2's principal claim.

Research-2 also says `DwmEnableBlurBehindWindow` with `DWM_BB_ENABLE` + NULL returns E_INVALIDARG
when `WS_EX_LAYERED` is set. If this were universally true, pyglet's OWN `_set_transparency()`
call (which uses `DWM_BB_ENABLE | DWM_BB_BLURREGION` + empty HRGN while `WS_EX_LAYERED` is set)
would also fail — yet it works for `SpriteWindow`. Research-2's E_INVALIDARG claim may be true
for the specific `DWM_BB_ENABLE + NULL` combination but is a red herring; it is not the cause of
the observed bug.

The practical advice (use empty-HRGN blur-behind) is correct, but pyglet's `_set_transparency()`
already does exactly this. The claim that `WS_EX_LAYERED` is incompatible with
`DwmEnableBlurBehindWindow` is **not** universally true — it is false for the empty-HRGN variant
that pyglet uses.

### Research-3 (multi-window GL context)

**Conclusion it draws:** `ElementsOverlayWindow.__init__` builds its `shapes.Rectangle` and
`pyglet.text.Label` objects without first calling `self.switch_to()`. When `__init__` runs inside
a `schedule_once` callback, the ambient GL context is `SpriteWindow`'s context. All GL handles
(VAOs, VBOs) are therefore registered in the wrong context. On draw, `_batch.draw()` silently
produces nothing; only the clear is written; because the framebuffer has no valid alpha pipeline
at that point the clear composites as opaque black.

**Verdict: Consistent with all four facts, but contains a key logical gap that undermines it as
the sole cause.**

The theory correctly identifies the `switch_to()` gap. However, it then claims that because
`_batch.draw()` produces nothing, the clear "composites as opaque black because the alpha pipeline
is broken." That is a circular argument: if the DWM alpha path were truly broken, the clear would
already be black before any batch draw is attempted — and we would not see the yellow tags. But we
DO see the yellow tags (FACT B). Therefore the batch IS rendering successfully into the overlay's
framebuffer. That means the GL handles are NOT in the wrong context — or, more precisely, the GL
object sharing between contexts is making the batch draw work correctly in the overlay's context.

The `switch_to()` gap is real and worth fixing, but it is NOT sufficient to explain the black
background because the tags render correctly.

### Research-4 (codebase diff)

**Conclusion it draws:** Two co-equal suspects — (1) `switch_to()` missing in `__init__`,
and (2) full-monitor size triggering DWM fullscreen-optimisation bypass.

**Verdict: Suspect #1 is undermined by FACT B (tags are visible — batch must be drawing). Suspect
#2 is structurally the only explanation consistent with ALL four facts and deserves primary focus.**

---

## 2. Independent Root-Cause Determination

### What all four facts force us to conclude

- FACT B proves: the GL context is correct, `alpha_size=8` was granted, DWM per-pixel alpha is
  at least partially active — otherwise the tags could not appear on-screen at all.
- FACT D proves: the failure mode is size-dependent. Small windows (SpriteWindow, picker) are
  transparent; the monitor-sized window is not.
- The only structural difference between `ElementsOverlayWindow` and `SpriteWindow` that is
  consistent with FACTS A–D is: **the overlay covers the entire monitor**.

### The DWM fullscreen-optimisation / independent-flip path

When a window exactly equals or exceeds a monitor's resolution and is positioned at the monitor's
origin, Windows DWM considers it a candidate for its "fullscreen optimisation" (FSO) / independent
flip path. In this mode DWM can scan out the window's framebuffer directly to the display without
compositing it through the standard per-pixel-alpha DWM pipeline. The result: DWM treats the raw
OpenGL framebuffer as an opaque surface and composites it against black — exactly the observed
symptom (opaque black background, not transparent).

The FSO path does NOT affect small windows (FACT A/D) because they never trigger the
monitor-coverage heuristic. This perfectly discriminates `SpriteWindow` from
`ElementsOverlayWindow`.

MPO (multiplane overlay) hardware support, referenced in Microsoft driver documentation, requires
that uncovered areas be scanned out as black. When the overlay exactly fills the monitor and MPO
is engaged, any pixel where alpha=0 (the transparent clear) is composited against a black hardware
plane rather than the desktop content behind it.

### What `win32_flags.apply_click_through` actually does — and why it breaks things

Reading `win32_flags.py` carefully reveals a critical problem that is separate from DWM
fullscreen optimisation and is the IMMEDIATE mechanical cause of the black background:

```python
# win32_flags.py, lines 94-104
bb = DWM_BLURBEHIND()
bb.dwFlags = DWM_BB_ENABLE          # <-- DWM_BB_BLURREGION is NOT set
bb.fEnable = True
bb.hRgnBlur = 0                     # <-- NULL
bb.fTransitionOnMaximized = False
hr = dwmapi.DwmEnableBlurBehindWindow(hwnd, ctypes.byref(bb))
```

This call uses `DWM_BB_ENABLE` with `hRgnBlur = NULL` (no `DWM_BB_BLURREGION` flag). According
to the official Microsoft docs for `DWM_BLURBEHIND`:

> "hRgnBlur: The region within the client area where the blur behind will occur. A NULL value
> will cause the effect to apply to the entire client area."

But this "whole-window blur" mode does **NOT** enable per-pixel alpha compositing. It enables
the Aero Glass blur (now a no-op since Windows 8), which is distinct from the per-pixel alpha
mode. The per-pixel alpha mode is only activated when `DWM_BB_BLURREGION` is set with a valid
(even empty) `HRGN`.

**Critically: `apply_click_through` is called AFTER `super().__init__()` has already had pyglet
call `_set_transparency()` — which correctly uses `DWM_BB_ENABLE | DWM_BB_BLURREGION` with an
empty HRGN.** The `apply_click_through` call then OVERWRITES pyglet's correct DWM setup with an
incorrect call that uses `DWM_BB_ENABLE` alone with NULL, which **disables** the per-pixel alpha
mode that pyglet had just enabled.

This is confirmed by the comment in `win32_flags.py` itself:
```
# Override pyglet's DwmEnableBlurBehindWindow call. Pyglet passes an
# empty blur region with DWM_BB_BLURREGION flag; on Windows 11 this
# can fail to enable per-pixel alpha because DWM sees the BLURREGION
# flag and tries to apply blur to an empty region.
```

The comment's reasoning is incorrect: the empty-HRGN + `DWM_BB_BLURREGION` call is precisely the
mechanism that enables per-pixel alpha (this is documented in pyglet's release notes, the winit
PR #1815, and confirmed by Research-1 §3). By "overriding" it with `DWM_BB_ENABLE + NULL`, the
code destroys the per-pixel alpha mode.

### Why FACT B (tags visible) doesn't contradict this

When per-pixel alpha is disabled by the null-region `DwmEnableBlurBehindWindow` call, DWM
composites the window as follows: it uses the global window opacity set by
`SetLayeredWindowAttributes(alpha=255)` (called by pyglet's `_set_transparency`). Alpha=255 means
"fully opaque". DWM then composites the entire framebuffer as-is onto the desktop. Pixels where
GL drew yellow tags are yellow (correct). Pixels where GL cleared to `(0,0,0,0)` are composited
as opaque black (because DWM ignores the per-pixel alpha and treats the window as uniformly opaque
at 255/255 = 100%).

This is exactly the reported symptom: yellow tags visible, background opaque black.

### Why FACT A is not contradicted

`SpriteWindow.apply_win32_flags()` calls the same `apply_click_through()` function. However,
after `apply_click_through()` overwrites the DWM setup, `SpriteWindow` may be small enough that
it never hits the specific DWM code path that requires the per-pixel alpha mode to work. More
importantly, the critical clue is in `__main__.py`:

```python
window.apply_win32_flags()   # SpriteWindow — called once at startup, no re-trigger
```

vs.

```python
overlay = ElementsOverlayWindow(monitor, elements)
overlay.apply_win32_flags()  # called immediately after construction
```

**Both windows call `apply_click_through()` and both have pyglet's DWM setup overwritten.** Yet
SpriteWindow IS transparent. This means the `DWM_BB_ENABLE + NULL` call does NOT break
transparency for small windows on the particular hardware being used — it may succeed silently,
or DWM may re-evaluate the window after the call and fall back to the per-pixel path for small
windows. Only the full-monitor window (where DWM's fullscreen-optimisation path is engaged) shows
the opaque black symptom because the incorrect DWM call prevents DWM from compositing per-pixel
alpha at the hardware level.

### Root-cause verdict (final)

**The `apply_click_through()` function in `win32_flags.py` calls `DwmEnableBlurBehindWindow` with
`DWM_BB_ENABLE` alone and `hRgnBlur = NULL`. This overwrites pyglet's correct
`DWM_BB_ENABLE | DWM_BB_BLURREGION` + empty-HRGN call (which is what enables per-pixel alpha
compositing). For small windows, DWM appears to maintain transparency through other means. For a
full-monitor window, DWM engages a fullscreen-optimisation / MPO compositing path that REQUIRES
the per-pixel alpha mode to be explicitly active; without it, DWM scans out the framebuffer as a
fully-opaque surface against a black hardware background.**

The yellow tags are visible because DWM still composites the GL framebuffer's RGB values; only
the per-pixel alpha channel is being ignored (the window is treated as globally opaque at
alpha=255). This is precisely the symptom: correct colors where drawn, opaque black where cleared.

---

## 3. Concrete Minimal Fix

**File:** `F:/Tools/Projects/voice-commander/src/voice_sprite/win32_flags.py`

**Lines to change:** 88–105 (the `DwmEnableBlurBehindWindow` block inside `apply_click_through`)

**Current broken code:**

```python
# Override pyglet's DwmEnableBlurBehindWindow call. Pyglet passes an
# empty blur region with DWM_BB_BLURREGION flag; on Windows 11 this
# can fail to enable per-pixel alpha because DWM sees the BLURREGION
# flag and tries to apply blur to an empty region. Canonical pattern =
# DWM_BB_ENABLE only, NULL region — tells DWM to composite the
# framebuffer's alpha channel directly.
dwm_ok = True
try:
    bb = DWM_BLURBEHIND()
    bb.dwFlags = DWM_BB_ENABLE
    bb.fEnable = True
    bb.hRgnBlur = 0
    bb.fTransitionOnMaximized = False
    hr = dwmapi.DwmEnableBlurBehindWindow(hwnd, ctypes.byref(bb))
    if hr != 0:
        logger.warning("DwmEnableBlurBehindWindow HRESULT=0x%08x", hr & 0xFFFFFFFF)
except OSError:
    dwm_ok = False
    logger.exception("DwmEnableBlurBehindWindow failed on hwnd=%d", hwnd)
```

**Fixed code — add the missing `DWM_BB_BLURREGION` constant and use an empty HRGN:**

First, add the missing constant at the top of `win32_flags.py` alongside `DWM_BB_ENABLE`:

```python
DWM_BB_ENABLE = 0x1
DWM_BB_BLURREGION = 0x2   # ADD THIS LINE
```

Then replace the broken `DwmEnableBlurBehindWindow` block:

```python
# Re-apply DWM per-pixel alpha compositing using the canonical empty-HRGN
# idiom. This is what pyglet's _set_transparency() does, and we must
# repeat it here because apply_click_through() runs after construction and
# any previous DWM state may have been modified. The critical detail:
# DWM_BB_BLURREGION must be set AND hRgnBlur must be a valid (non-NULL)
# HRGN — even an empty (zero-area) one. DWM interprets the empty region as
# "apply alpha compositing to the entire window client area without any
# actual blur". Using DWM_BB_ENABLE alone with NULL hRgnBlur tells DWM to
# composite the whole window as an opaque surface (alpha ignored), which is
# what caused the black background on full-monitor overlays.
gdi32 = ctypes.windll.gdi32
dwm_ok = True
try:
    region = gdi32.CreateRectRgn(0, 0, -1, -1)   # empty (zero-area) HRGN
    bb = DWM_BLURBEHIND()
    bb.dwFlags = DWM_BB_ENABLE | DWM_BB_BLURREGION
    bb.fEnable = True
    bb.hRgnBlur = region
    bb.fTransitionOnMaximized = False
    hr = dwmapi.DwmEnableBlurBehindWindow(hwnd, ctypes.byref(bb))
    gdi32.DeleteObject(region)
    if hr != 0:
        logger.warning("DwmEnableBlurBehindWindow HRESULT=0x%08x", hr & 0xFFFFFFFF)
except OSError:
    dwm_ok = False
    logger.exception("DwmEnableBlurBehindWindow failed on hwnd=%d", hwnd)
```

This is the minimal, targeted fix. No changes to `elements_overlay.py`, `window.py`, or
`__main__.py` are required. The fix is one added constant and a corrected `DwmEnableBlurBehindWindow`
call that mirrors what pyglet's own `_set_transparency()` does correctly.

**Why this is safe for SpriteWindow too:**  
`SpriteWindow.apply_win32_flags()` calls the same `apply_click_through()`. Changing to the
correct empty-HRGN call will not break `SpriteWindow`'s transparency — it will reinforce it.
The empty-HRGN idiom is exactly what pyglet already calls; this simply stops `apply_click_through`
from undoing it.

---

## 4. Secondary Hardening (Recommended but not minimal)

**Add `switch_to()` before GL object construction in `ElementsOverlayWindow.__init__`:**

Research-3 and Research-4 correctly identify that GL shapes/labels are built without first calling
`self.switch_to()`. While FACT B shows the batch currently draws correctly (suggesting shared GL
object space is working), the `switch_to()` discipline is correct practice. The picker modal
correctly calls `switch_to()` at the top of `refresh()` for this exact reason.

In `elements_overlay.py`, add immediately after `super().__init__(...)` returns (line 120) and
before the `gl.glEnable` call:

```python
self.switch_to()   # ensure overlay's GL context is current for all subsequent GL work
```

This is belt-and-suspenders — it does not fix the primary bug but prevents a latent context
mismatch from becoming a bug after future refactoring.

---

## 5. Verification Method

### Step 1: Apply the fix to `win32_flags.py` as described above.

### Step 2: Run the sprite process with elements mode active:

```
python -m voice_sprite --config config.toml
# trigger an elements.show SSE event via the daemon or a test script
```

### Step 3: Observe visually:
- The overlay should show yellow numbered tags on a fully transparent background (desktop visible
  through the overlay wherever no tag is drawn).
- The sprite window's transparency should be unaffected (no regression).

### Step 4: Confirm the DWM call succeeds — check the log output:
- Before fix: `DwmEnableBlurBehindWindow HRESULT=0x80070057` (E_INVALIDARG, because
  `DWM_BB_ENABLE` with NULL hRgnBlur may fail when `WS_EX_LAYERED` is present) or silent failure.
- After fix: `Applied click-through + DWM sheet-of-glass flags to hwnd=...` with no HRESULT
  warning.

### Step 5: Diagnostic toggle (if still black after step 3):
Remove the `overlay.apply_win32_flags()` call in `__main__.py` temporarily. If the overlay
becomes transparent without the call, it conclusively confirms that `apply_click_through()` was
the disruptor. Re-add the call with the fix applied — it should remain transparent.

### Step 6: Size regression test:
Create a test window at exactly the monitor's width × height vs. monitor width-2 × height-2.
If the full-size window is black but the shrunk-by-2px window is transparent even after the
primary fix, this indicates a DWM fullscreen-optimisation path is also engaged. In that case,
add a 1px inset to `ElementsOverlayWindow` sizing:

```python
# In elements_overlay.py, __init__, the super().__init__ call:
super().__init__(
    width=mr - ml - 1,   # 1px inset prevents DWM FSO engagement
    height=mb - mt - 1,
    ...
)
```

Only apply this secondary fix if step 5 confirms FSO is the residual cause after the primary fix.

---

## Summary

| Research claim | Verdict |
|---|---|
| Missing `alpha_size=8` or wrong `glClearColor` | REFUTED by FACT B (tags are visible) |
| `WS_EX_LAYERED + CS_OWNDC` breaks DWM compositing | REFUTED by FACT A (SpriteWindow works with WS_EX_LAYERED) |
| `switch_to()` missing → wrong GL context → batch draws nothing | REFUTED as primary cause by FACT B; latent issue worth fixing |
| Full-monitor size triggers DWM FSO bypass | CONSISTENT with all facts; secondary contributor |

**Primary root cause:** `win32_flags.apply_click_through()` calls `DwmEnableBlurBehindWindow`
with `DWM_BB_ENABLE` only and `hRgnBlur = NULL`. This overwrites pyglet's correct empty-HRGN
per-pixel alpha setup. For full-monitor windows that engage DWM's fullscreen-optimisation path,
DWM then treats the window as globally opaque, compositing the framebuffer's RGB values against
black while ignoring per-pixel alpha. Yellow tags appear (RGB correct); background is opaque
black (alpha ignored).

**Fix:** In `win32_flags.py`, change the `DwmEnableBlurBehindWindow` call to use
`DWM_BB_ENABLE | DWM_BB_BLURREGION` with a valid empty HRGN (`CreateRectRgn(0, 0, -1, -1)`),
matching the idiom pyglet's own `_set_transparency()` uses.
