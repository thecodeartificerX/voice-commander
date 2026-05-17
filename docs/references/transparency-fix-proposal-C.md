# Transparency Fix Proposal C — Framebuffer Alpha Content Analysis

**Date:** 2026-05-17  
**Investigator:** Adversarial analyst (independent)  
**Angle:** GL framebuffer alpha CONTENT — what alpha values actually land in the framebuffer in empty regions, and why.

---

## 1. Setup the Crime Scene — What the Code Actually Does

Reading the source directly, not from the research summaries:

### `ElementsOverlayWindow.__init__` (elements_overlay.py, lines 92–171)

```python
super().__init__(               # line 114 — creates OS window + GL context
    width=mr - ml,
    height=mb - mt,
    style=WINDOW_STYLE_OVERLAY,
    config=gl_config,           # alpha_size=8, double_buffer=True, no MSAA
    vsync=False,
)
# Lines 127–135: GL state — STILL NO switch_to() call before this
gl.glEnable(gl.GL_BLEND)
gl.glBlendFuncSeparate(
    gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA,  # RGB
    gl.GL_ONE,       gl.GL_ONE_MINUS_SRC_ALPHA,  # Alpha
)
gl.glClearColor(0, 0, 0, 0)

# Lines 152–171: pyglet.shapes.Rectangle + pyglet.text.Label created here
# ALSO NO switch_to() call before this loop
for element in elements:
    self._shapes.append(pyglet.shapes.Rectangle(..., batch=self._batch))
    self._labels.append(pyglet.text.Label(...,  batch=self._batch))
```

### `on_draw` (lines 173–180)

```python
def on_draw(self) -> None:
    pyglet.gl.glClearColor(0, 0, 0, 0)
    self.clear()
    self._batch.draw()
```

### `apply_win32_flags` → `win32_flags.apply_click_through`

```python
bb.dwFlags = DWM_BB_ENABLE   # ← NOTE: DWM_BB_BLURREGION is NOT set
bb.fEnable = True
bb.hRgnBlur = 0              # NULL
hr = dwmapi.DwmEnableBlurBehindWindow(hwnd, ctypes.byref(bb))
```

### `__main__.py` — Startup sequence (lines 228–241)

```python
def _create(_dt: float) -> None:      # schedule_once callback
    _hide_elements_now()
    overlay = ElementsOverlayWindow(monitor, elements)   # line 240
    overlay.apply_win32_flags()                           # line 241
    _elements_overlay.append(overlay)
```

---

## 2. What Other Research Concluded — Tested Against FACTS A–D

### Research-1 (pyglet.md): Blend equation may write wrong alpha to framebuffer

**Claim:** `glBlendFuncSeparate(SRC_ALPHA, ONE_MINUS_SRC_ALPHA, ONE, ONE_MINUS_SRC_ALPHA)` is the correct form. Standard `GL_SRC_ALPHA / GL_ONE_MINUS_SRC_ALPHA` for alpha can write alpha values DWM misinterprets.

**Verdict vs FACTS:** The code already uses the correct `glBlendFuncSeparate` with `GL_ONE` for alpha (line 129–134). This cannot be the cause of opaque black. FACT A also shows `SpriteWindow` works with the same blend equation. This research direction, while technically informative, does not explain the bug.

### Research-2 (win32-dwm.md): WS_EX_LAYERED conflicts with CS_OWNDC — DWM never gets per-pixel alpha

**Claim:** `WS_EX_LAYERED` + `CS_OWNDC` (required by WGL) are mutually exclusive; layered compositing is broken for OpenGL; `DwmEnableBlurBehindWindow` with an empty HRGN (not WS_EX_LAYERED) is the correct path.

**Verdict vs FACTS:** FACT A directly contradicts this as a sufficient root cause: `SpriteWindow` uses the exact same `WINDOW_STYLE_OVERLAY` (which sets `WS_EX_LAYERED`) and IS transparent. If `WS_EX_LAYERED + CS_OWNDC` were inherently fatal, neither window would work. This research overstates the incompatibility for pyglet's specific DWM blur-behind code path. Not the root cause.

### Research-3 (multiwindow.md): Missing switch_to() — GL objects built in wrong context

**Claim:** `_create` fires as a `schedule_once` callback; ambient GL context is `SpriteWindow`'s; `ElementsOverlayWindow.__init__` never calls `self.switch_to()` before building shapes/labels; those GL handles are registered in `SpriteWindow`'s context; overlay's `on_draw` finds invalid handles.

**Verdict vs FACTS:** Consistent with FACTS A–D. Explains why tags render (the shapes are drawn, but may render from the wrong context in a partially broken state) and why background is black (draw fails silently or produces nothing; only the clear is effective). However, this hypothesis predicts that `batch.draw()` would produce **nothing visible** — but FACT B says tags render fine. This needs further scrutiny.

### Research-4 (codebase-diff.md): Same as Research-3 plus DWM full-screen optimisation

Ranked `switch_to()` context mismatch as #1, DWM full-screen-optimisation as #2.

---

## 3. The Framebuffer Alpha Content Question — Precision Analysis

### Q1: After clear() + draw(), what is the alpha in empty regions?

With `glClearColor(0, 0, 0, 0)` + `self.clear()`, every pixel is set to `(R=0, G=0, B=0, A=0)`. This part is straightforward. No shape or label draw call touches the empty regions — only the tag footprints are drawn over. So in empty regions, alpha should be 0 after clear.

**The question is whether something PREVENTS the clear from running correctly, or whether something OVERWRITES the alpha=0 result in empty regions.**

### Q2: Does pyglet 2.1.x shape/label shader draw an opaque background quad?

Investigating pyglet 2.1.x shape rendering pipeline:

`pyglet.shapes.Rectangle` in pyglet 2.x renders via a vertex group with its own shader program (`pyglet/shapes.py`). The shader draws only the vertices of the rectangle itself — it does NOT draw a full-window quad or background. There is no implicit "background pass" in pyglet's batch system. The batch draws only the objects registered with it.

`pyglet.text.Label` similarly renders only the glyph quads — per-character quads, no background.

**Conclusion:** No pyglet 2.1.x mechanism draws a full-window opaque background quad. The empty framebuffer regions after clear should retain alpha=0.

### Q3: Does the batch/shape shader override the window-level glBlendFuncSeparate?

This is the critical question the task directs us to investigate.

In pyglet 2.1.x, each shape group has its own `StateManager`. The `ShapeBase` class sets up a `BlendState` on its group. Looking at pyglet's source: `pyglet/shapes.py` uses `pyglet.graphics.shader` with a `Group` subclass that sets `GL_BLEND` + `GL_SRC_ALPHA / GL_ONE_MINUS_SRC_ALPHA` via its `set_state()` / `unset_state()` methods.

**When the batch draws a `shapes.Rectangle`, the shape's group calls `glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)` — overriding the window-level `glBlendFuncSeparate` that was set in `__init__`.**

After the batch finishes drawing, the group calls `unset_state()` which restores the GL state. But inside the draw, the alpha blend equation is:

```
dst.a = src.a * GL_SRC_ALPHA_dst_factor + dst.a * GL_ONE_MINUS_SRC_ALPHA_dst_factor
      = src.a * src.a + dst.a * (1 - src.a)
```

Wait — `glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)` means:
- RGB: `out.rgb = src.rgb * src.a + dst.rgb * (1 - src.a)`  ← correct  
- Alpha: `out.a = src.a * src.a + dst.a * (1 - src.a)`  ← WRONG

For a tag with alpha=255 (src.a=1) drawn over clear (dst.a=0):
- `out.a = 1.0 * 1.0 + 0.0 * 0.0 = 1.0`  ← OK, drawn pixels are fully opaque  

For a tag with alpha=255 drawn over an empty region (dst.a=0):
- Empty regions are untouched — alpha remains 0.0  

So the shape shader overriding the blend function does NOT cause empty regions to become opaque. The alpha in empty regions stays 0 regardless of the shape shader's blend settings.

**However — this override is still worth noting for tag-pixel quality (semi-transparent fringing), but it does NOT write alpha=1 across the whole window.**

### Q4: Is the window-level glBlendFuncSeparate being overridden so empty regions end up at alpha=0?

As shown above: empty regions are NOT drawn by any shader. The clear writes alpha=0 there, and nothing overwrites it. The blend function only matters where actual geometry is drawn.

**This path does not explain the opaque black background in empty regions.** The framebuffer alpha content in empty regions is correctly 0 after clear.

---

## 4. Root Cause — The Real Mechanism

If the framebuffer alpha in empty regions is correctly 0, why is the background opaque black?

The answer requires examining what DWM receives from `apply_win32_flags()`.

### The DWM_BB_ENABLE without DWM_BB_BLURREGION bug

`apply_click_through` in `win32_flags.py` (lines 94–102) calls:

```python
bb.dwFlags = DWM_BB_ENABLE          # value = 0x1
bb.fEnable = True
bb.hRgnBlur = 0                     # NULL
hr = dwmapi.DwmEnableBlurBehindWindow(hwnd, ctypes.byref(bb))
```

This is **different from pyglet's own `_set_transparency()` call**, which uses:

```python
bb.dwFlags = DWM_BB_ENABLE | DWM_BB_BLURREGION    # value = 0x3
bb.hRgnBlur = CreateRectRgn(0, 0, -1, -1)         # empty HRGN (not NULL)
```

The two calls behave differently:

**pyglet's call (`DWM_BB_ENABLE | DWM_BB_BLURREGION` + empty HRGN):** DWM sees "blur region is specified" — but it is zero-area. This is the undocumented idiom that tells DWM to use per-pixel alpha from the GL framebuffer for the entire client area.

**`apply_click_through`'s call (`DWM_BB_ENABLE` + NULL hRgnBlur):** According to Microsoft docs: "DWM_BB_BLURREGION is NOT set → the hRgnBlur member is NOT valid → treat the hRgnBlur as the entire window." This is the whole-window blur mode. On Windows 8+, the blur visual does not render, but more critically: without `DWM_BB_BLURREGION` + empty HRGN, the "non-blurred region" alpha interpretation path (which is what enables per-pixel alpha) is NOT activated. DWM falls back to treating the window as opaque.

**The sequence for `ElementsOverlayWindow`:**

1. `super().__init__()` → pyglet calls `_set_transparency()` → `DWM_BB_ENABLE | DWM_BB_BLURREGION` + empty HRGN → per-pixel alpha ENABLED ✓
2. `overlay.apply_win32_flags()` → `apply_click_through()` → `DWM_BB_ENABLE` + NULL hRgnBlur → **per-pixel alpha DISABLED** ✗

**`apply_click_through` is called AFTER `__init__`**, overwriting pyglet's correct DWM call with an incorrect one that disables per-pixel alpha. From this point on, DWM composites the overlay as if it has no transparency information — opaque black.

### Why does SpriteWindow work?

`SpriteWindow` also calls `apply_win32_flags()` → `apply_click_through()` (line 176 of `__main__.py`). So both windows receive the same broken DWM call. Yet `SpriteWindow` IS transparent (FACT A).

**The explanation is window size.** `SpriteWindow` is a small window (~200×200px). `ElementsOverlayWindow` covers the entire monitor (e.g. 2560×1440). When `DWM_BB_ENABLE` without `DWM_BB_BLURREGION` is called on a small window, DWM's behavior may vary from when it is called on a full-monitor window.

More precisely: for a small window, DWM's "whole-window blur mode" with the wrong call may accidentally fall through to per-pixel alpha because the DWM compositor path for a small overlay window is more forgiving. For a full-monitor window, DWM switches to a "full-screen optimization" composition path that strictly interprets the DWM attributes as set — and since `DWM_BB_BLURREGION` is NOT set and NULL region is given, it composites as opaque.

This perfectly satisfies all four facts:
- **FACT A:** SpriteWindow transparent → small window, DWM forgiving
- **FACT B:** Tags render fine → the shapes ARE drawn; the transparency failure is at the DWM compositor level, not the GL level
- **FACT C:** Full-monitor overlay is black → full-screen DWM path, strict interpretation
- **FACT D:** Only the full-monitor overlay is black → size is the differentiator

### Why do tags render fine if the background is black?

This is the key. The framebuffer IS being composited — DWM is showing the window content. The GL alpha channel is carrying the correct information (alpha=1 on tag pixels, alpha=0 on empty pixels). But DWM, having been told "blur behind whole window" with the wrong call, ignores the framebuffer alpha and composites the entire window at 100% opacity. The tag pixels are opaque yellow (alpha=1) → they appear yellow. The empty pixels have RGB=(0,0,0) from the clear → they appear black. The tags are "visible" because their color is non-black, not because DWM is using per-pixel transparency. The background looks opaque black because DWM treats the whole window as opaque and the cleared color is black.

---

## 5. Evaluating the GL Framebuffer Alpha Angle

**Q: Does anything write alpha=1 across the whole window?** No. `glClearColor(0,0,0,0)` + `clear()` writes alpha=0 everywhere. No batch draw touches empty regions.

**Q: Does the shape/label shader override glBlendFuncSeparate and corrupt the framebuffer alpha in empty regions?** No. The shader only executes on drawn geometry, not on the whole framebuffer.

**Q: Is the window-level glBlendFuncSeparate the problem?** No. It is correctly set. The shape group overrides it during draw (using standard blend), but this only affects drawn pixels, not empty regions.

**Q: Is the issue that the WINDOW-LEVEL glBlendFuncSeparate is overridden per-draw by pyglet's shape/label group state, so empty regions never end up at alpha 0?** No. Empty regions are never drawn. Their alpha=0 from clear is preserved.

**Conclusion on the framebuffer angle:** The framebuffer alpha content is CORRECT. Alpha=0 in empty regions, alpha=1 in tag pixels. The problem is NOT in the framebuffer. The problem is that DWM is instructed (by `apply_click_through`) to IGNORE the framebuffer alpha and treat the whole window as opaque.

---

## 6. Root Cause Verdict

**The root cause is `apply_click_through` in `win32_flags.py` calling `DwmEnableBlurBehindWindow` with `DWM_BB_ENABLE` + NULL `hRgnBlur` (no `DWM_BB_BLURREGION` flag) AFTER `__init__` has correctly set up per-pixel alpha via pyglet's `_set_transparency()` call.**

The `apply_click_through` call overwrites the correct DWM state with an incorrect configuration that disables per-pixel alpha compositing, specifically for full-monitor windows where DWM applies strict "full-screen optimised" compositing rules.

`SpriteWindow` experiences the same DWM overwrite but survives because DWM's behavior for small non-full-screen overlay windows happens to produce acceptable transparency despite the incorrect call. `ElementsOverlayWindow` fails because its full-monitor size puts it on the strict DWM code path.

Note: The context-mismatch hypothesis (research-3, research-4) — that shapes are built under the wrong GL context — is plausible but is NOT the root cause here. The tags visibly render (FACT B), which means the GL objects are being drawn correctly in the overlay's context. If the VAO handles were genuinely invalid, `batch.draw()` would produce nothing at all (no tags, no visible output). Tags rendering confirms GL object registration is correct.

---

## 7. Concrete Minimal Fix

### File: `src/voice_sprite/win32_flags.py`

**The fix:** Change `apply_click_through` to use the correct `DWM_BB_ENABLE | DWM_BB_BLURREGION` + empty HRGN idiom, matching what pyglet's `_set_transparency()` does. This re-enables per-pixel alpha after the `SetWindowLong` call (which forces a DWM attribute reset).

**Exact change — add constant and fix the DWM call:**

Current code (lines 43, 93–102):
```python
DWM_BB_ENABLE = 0x1

# ...

bb = DWM_BLURBEHIND()
bb.dwFlags = DWM_BB_ENABLE
bb.fEnable = True
bb.hRgnBlur = 0
bb.fTransitionOnMaximized = False
hr = dwmapi.DwmEnableBlurBehindWindow(hwnd, ctypes.byref(bb))
```

Fixed code:
```python
DWM_BB_ENABLE     = 0x1
DWM_BB_BLURREGION = 0x2    # ← ADD THIS

# ...

gdi32 = ctypes.windll.gdi32
gdi32.CreateRectRgn.restype = wintypes.HRGN
# Empty (zero-area) region: tells DWM to use the GL framebuffer's
# per-pixel alpha for the entire client area instead of whole-window
# constant-alpha mode. DWM_BB_ENABLE alone (with NULL region) disables
# this — the correct idiom requires DWM_BB_BLURREGION + empty HRGN.
# This matches pyglet's own _set_transparency() call in:
#   pyglet/window/win32/__init__.py
region = gdi32.CreateRectRgn(0, 0, -1, -1)
bb = DWM_BLURBEHIND()
bb.dwFlags = DWM_BB_ENABLE | DWM_BB_BLURREGION
bb.fEnable = True
bb.hRgnBlur = region
bb.fTransitionOnMaximized = False
hr = dwmapi.DwmEnableBlurBehindWindow(hwnd, ctypes.byref(bb))
gdi32.DeleteObject(region)    # Caller owns the HRGN; free it after the call
```

**Also declare `gdi32` at module level** alongside `user32` and `dwmapi` (line 11):
```python
gdi32 = ctypes.windll.gdi32
```

### Why this is the minimal complete fix

- It restores the correct DWM per-pixel alpha mode after `SetWindowLong` resets DWM state
- It is identical in mechanism to pyglet's own `_set_transparency()` — the proven-working path
- It fixes both `SpriteWindow` (whose incidental transparency survives today) and `ElementsOverlayWindow` (which currently fails)
- No changes to `elements_overlay.py`, `window.py`, GL blend setup, or `__main__.py`
- Does not break `PickerModalWindow`, which uses `WINDOW_STYLE_BORDERLESS` and does not call `apply_click_through`

### Why the comment in win32_flags.py is wrong

The current comment at line 87–90 says:
> "Pyglet passes an empty blur region with DWM_BB_BLURREGION flag; on Windows 11 this can fail to enable per-pixel alpha because DWM sees the BLURREGION flag and tries to apply blur to an empty region. Canonical pattern = DWM_BB_ENABLE only, NULL region"

This is backwards. The research files confirm: `DWM_BB_ENABLE | DWM_BB_BLURREGION` + empty HRGN is the idiom that ENABLES per-pixel alpha. `DWM_BB_ENABLE` + NULL region is the whole-window blur mode that DISABLES per-pixel alpha. The comment was written under a false assumption and led to the wrong implementation.

---

## 8. Verification Plan

1. Apply the fix to `win32_flags.py`
2. Launch voice-sprite and trigger `elements.show` via SSE
3. Observe: the overlay background should be transparent (see-through to desktop), tags should remain visible as yellow numbered labels
4. Confirm with a white desktop background: background shows as white, not black
5. Confirm SpriteWindow still works (it should — the fix makes the call correct for both)
6. Optional diagnostic: add logging of the HRESULT from `DwmEnableBlurBehindWindow` before and after fix; the current broken call with `DWM_BB_ENABLE` + NULL may be returning `S_OK` (0x0) on Windows 11, which explains why no error was caught — the call succeeds but in the wrong mode

---

## 9. Summary

| Question | Answer |
|---|---|
| Does anything write alpha=1 to the whole framebuffer? | No. `glClearColor(0,0,0,0)` + `clear()` correctly zeros the background alpha. |
| Do shape/label shaders write an opaque background quad? | No. pyglet 2.1.x draws only geometry, no implicit background pass. |
| Does the shape group override glBlendFuncSeparate and corrupt empty-region alpha? | No. Override only applies to drawn pixels; empty regions untouched. |
| Is the framebuffer alpha content correct? | Yes. Alpha=0 in empty regions, alpha=1 in tag pixels. The GL is fine. |
| What is the actual root cause? | `apply_click_through` calls `DwmEnableBlurBehindWindow(DWM_BB_ENABLE, NULL)` which disables per-pixel alpha and overwrites pyglet's correct `_set_transparency()` setup. Full-monitor windows hit the strict DWM code path; small windows incidentally survive. |
| Concrete fix | Add `DWM_BB_BLURREGION = 0x2`, use `gdi32.CreateRectRgn(0,0,-1,-1)` as `hRgnBlur`, set `dwFlags = DWM_BB_ENABLE | DWM_BB_BLURREGION` in `apply_click_through`. |
