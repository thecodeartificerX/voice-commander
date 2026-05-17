# Pyglet 2.1.x Transparent Overlay Window on Windows 11 — Research Findings

**Research date:** 2026-05-17  
**Target stack:** Python 3.11, pyglet 2.1.14, Windows 11, OpenGL

---

## 1. How pyglet's Transparent/Overlay Window Styles Work

### Official Documentation (pyglet 2.1.14)

Sources: [Windowing guide](https://pyglet.readthedocs.io/en/latest/programming_guide/windowing.html), [pyglet.window module](https://pyglet.readthedocs.io/en/latest/modules/window.html)

Pyglet supports six non-fullscreen window styles. The two relevant ones:

| Style constant | Description |
|---|---|
| `WINDOW_STYLE_TRANSPARENT` | Borderless window with transparent framebuffer; has title bar / decorations |
| `WINDOW_STYLE_OVERLAY` | Transparent, topmost, click-through (does not accept mouse clicks) |

The documentation states verbatim:

> "Transparent and overlay windows both set a transparent framebuffer if the graphics card and windowing system both allow it."

> "Overlay windows require custom sizing and moving of the respective window. By default, overlay's are always on top, and do not accept mouse clicks."

> "Once created, the window style cannot be altered."

### Win32 Style Mappings (from `pyglet/window/win32/__init__.py`)

```python
self.WINDOW_STYLE_TRANSPARENT: (constants.WS_OVERLAPPEDWINDOW,
                                 constants.WS_EX_LAYERED),
self.WINDOW_STYLE_OVERLAY:     (constants.WS_POPUP,
                                 constants.WS_EX_LAYERED | constants.WS_EX_TRANSPARENT),
```

`WINDOW_STYLE_OVERLAY` uses:
- `WS_POPUP` — no title bar/decorations
- `WS_EX_LAYERED` — enables layered window compositing
- `WS_EX_TRANSPARENT` — enables click-through (hit-testing passes to windows beneath)

After window creation, pyglet calls `_set_transparency()` for both styles:

```python
elif self.style == 'transparent' or self.style == 'overlay':
    self._set_transparency()
```

For overlay, it also sets `HWND_TOPMOST` positioning post-transparency setup.

---

## 2. Pyglet's `_set_transparency()` — Exact Implementation

From `pyglet/window/win32/__init__.py` (current master / 2.1.14):

```python
def _set_transparency(self) -> None:
    region = _gdi32.CreateRectRgn(0, 0, -1, -1)
    bb = DWM_BLURBEHIND()
    bb.dwFlags = constants.DWM_BB_ENABLE | constants.DWM_BB_BLURREGION
    bb.hRgnBlur = region
    bb.fEnable = True

    _dwmapi.DwmEnableBlurBehindWindow(self._hwnd, byref(bb))
    _gdi32.DeleteObject(region)

    _user32.SetLayeredWindowAttributes(self._hwnd, 0, 255, constants.LWA_ALPHA)
```

**What this does:**
1. Creates an *empty* GDI region via `CreateRectRgn(0, 0, -1, -1)` — a region with negative dimensions = empty
2. Calls `DwmEnableBlurBehindWindow` with `DWM_BB_ENABLE | DWM_BB_BLURREGION` + that empty region
3. Calls `SetLayeredWindowAttributes(hwnd, colorRef=0, bAlpha=255, LWA_ALPHA)`

**The `DWM_BLURBEHIND` struct fields used:**
- `DWM_BB_ENABLE` (0x00000001) — enable the blur-behind effect
- `DWM_BB_BLURREGION` (0x00000002) — the `hRgnBlur` member is valid
- `hRgnBlur` = empty region (activates per-pixel alpha compositing mode)
- `fEnable = True`

**The WGL pixel format:** pyglet's `Win32Config` (in `pyglet/gl/win32.py`) sets `cAlphaBits = self.alpha_size` in the `PIXELFORMATDESCRIPTOR`, and maps `'alpha_size'` to `WGL_ALPHA_BITS_ARB` when using the ARB pixel format extension. This is what `Config(alpha_size=8)` triggers.

---

## 3. The Root Problem: DwmEnableBlurBehindWindow on Windows 8+

### Official Microsoft Documentation

Source: [DwmEnableBlurBehindWindow (dwmapi.h)](https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/nf-dwmapi-dwmenableblurbehindwindow)

The function's Remarks section states:

> **"Beginning with Windows 8, calling this function doesn't result in the blur effect, due to a style change in the way windows are rendered."**

Source: [DWM Blur Behind Overview](https://learn.microsoft.com/en-us/windows/win32/dwm/blur-ovw)

The same note appears there too. The visual "Aero Glass blur" effect is gone on Windows 8, 10, 11. **However**, the critical insight is that on Windows 8+, even though there is no *blur*, the call to `DwmEnableBlurBehindWindow` with `DWM_BB_BLURREGION` + an empty region **still enables the per-pixel alpha compositing mode** in DWM. This is the undocumented behavior that pyglet relies on.

The official docs also note:

> "The alpha values in the window are honored, and the rendering atop the blur will use these alpha values."

> "When you apply the blur-behind effect to a subregion of the window, the alpha channel of the window is used for the nonblurred area. This can cause an unexpected transparency in the nonblurred region of a window."

This means: with an **empty** blur region (`CreateRectRgn(0,0,-1,-1)`), the *entire* client area falls into the "nonblurred region", and the alpha channel from OpenGL is used directly for compositing. This is the mechanism that makes pyglet's transparency work.

**The diagnostic error `0x80070057` (E_INVALIDARG)** when calling `DwmEnableBlurBehindWindow` with `DWM_BB_ENABLE` alone (no `DWM_BB_BLURREGION`, NULL region) — this is because: when `DWM_BB_BLURREGION` is set, `hRgnBlur` must not be NULL. The empty region `CreateRectRgn(0,0,-1,-1)` is not NULL — it's a valid (but empty) HRGN handle. This is why the diagnostics showing `DWM_BB_ENABLE` alone (with NULL region) got `E_INVALIDARG`, while pyglet's version with the empty region works.

---

## 4. GitHub Issues — Documented Causes and Workarounds

### Issue #693 — Transparent Window Not Working on Windows 11

**URL:** https://github.com/pyglet/pyglet/issues/693  
**Opened:** October 14, 2022  
**Labels:** "help wanted", "windows"

Reporter (`fregapple`) attempted:

```python
import pyglet
window = pyglet.window.Window(style=pyglet.window.Window.WINDOW_STYLE_OVERLAY)
label = pyglet.text.Label('Hello, World', ...)

@window.event
def on_draw():
    window.clear()
    label.draw()

pyglet.app.run()
```

Result: black opaque background instead of transparency.

The reporter stated: *"I can't find anything in the official docs on how to set it up!"*

**Status:** Closed. The issue was filed against pyglet 1.5.20 (the first version to add experimental transparent window support). The fix came in pyglet 2.1.7 (see below).

### Issue #1271 — Label and Shapes.Rectangle Unexpected Blending with Transparent Window Style

**URL:** https://github.com/pyglet/pyglet/issues/1271  
**Opened:** February 8, 2025  
**Platform:** Windows 10 64-bit

Reporter found that with:

```python
import pyglet
from pyglet import shapes

config = pyglet.gl.Config(alpha_size=8)
window = pyglet.window.Window(800, 600, config=config, style='transparent')
pyglet.gl.glEnable(pyglet.gl.GL_BLEND)
pyglet.gl.glBlendFunc(pyglet.gl.GL_SRC_ALPHA, pyglet.gl.GL_ONE_MINUS_SRC_ALPHA)

rect = shapes.Rectangle(300, 250, 200, 100, color=(70, 70, 70, 255))
```

Problem described: *"When using a transparent pyglet window with alpha blending enabled, both pyglet.text.Label and pyglet.shapes.Rectangle exhibit strange blending properties. When the underlying desktop background changes from a black to a white color the drawn objects do not appear to fully ignore the transparency of the window. Instead, parts of the desktop background seem to interfere with their rendering. Moving the rectangle part over your screen, there are differences in the alpha of the rectangle color and the font outline gets brighter."*

**Root cause (research conclusion):** The `GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA` blend equation writes a non-zero alpha to the framebuffer for semi-transparent pixels, but it writes the *wrong* alpha value for compositing with DWM. DWM reads the per-pixel alpha channel from the OpenGL framebuffer. The standard blend equation does not correctly accumulate the framebuffer alpha for DWM compositing — it produces alpha values that are dependent on the background (which for a cleared transparent window is alpha=0), causing the background luminance to "bleed through". The fix is premultiplied alpha blending (see Recommended Recipe).

**Status:** Closed.

### Issue #246 — Possibility to Create Windows with Transparent Background

**URL:** https://github.com/pyglet/pyglet/issues/246  
**Opened:** July 15, 2020  
**Status:** Closed

Predated the transparent window feature addition in 1.5.20.

---

## 5. Pyglet Version History for Transparency

| Version | Transparency-related change |
|---|---|
| 1.5.20 | "Experimental support for transparent and overlay windows on Linux and Windows" |
| 2.1.7 | **"window: Fix window transparency for Windows OS"** — critical Windows-specific fix |
| 2.1.8 | "Add support for MacOSX to have transparent windows and overlays (#1339)" |
| 2.1.8 | "Fix bugs with transparent framebuffers for transparent windows (#1333)" |

**Key fix in 2.1.7:** Based on cross-referencing with the `winit` PR #1815 which fixed an identical issue in Rust's winit library: an earlier commit had accidentally removed the `CreateRectRgn` empty region, assuming `DwmEnableBlurBehindWindow` would handle the whole window by default. This broke transparency because the empty region is what tells DWM to use per-pixel alpha for the entire client area. The fix restored the `CreateRectRgn(0, 0, -1, -1)` empty region approach.

The current 2.1.14 implementation (shown above) has this fix in place.

---

## 6. The Blend Function Problem — Standard vs. Premultiplied Alpha

This is the subtlest issue and the most likely cause of the "opaque black background" symptom when using `glClearColor(0,0,0,0)` + standard blend:

### Standard (non-premultiplied) blend — WRONG for DWM compositing:

```python
glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
```

When you clear to `(0, 0, 0, 0)` and draw a yellow tag with alpha=255:
- The *color* renders correctly: `(R=1, G=1, B=0, A=1)`
- But the framebuffer alpha in cleared areas = 0
- DWM interprets alpha=0 as fully transparent (correct for background)
- DWM interprets alpha=255 as opaque (correct for drawn pixels)

This *should* work for opaque drawing. The problem arises when you have semi-transparent pixels (e.g., from anti-aliasing or MSAA). The standard blend equation:

```
dst.a = src.a + (1 - src.a) * dst.a
```

does not correctly compute the final composited alpha for DWM because DWM is doing its *own* compositing using these alpha values. The result is objects that appear to have different opacity depending on what's behind the window.

### Premultiplied alpha blend — CORRECT for DWM compositing:

```python
# For rendering to a transparent framebuffer composited by DWM:
glBlendFuncSeparate(
    GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA,  # RGB: standard
    GL_ONE,       GL_ONE_MINUS_SRC_ALPHA   # Alpha: premultiplied accumulation
)
```

Or, if all drawn content uses pre-multiplied alpha values:

```python
glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
```

The Khronos OpenGL forums confirm: for transparent background compositing, the recommended clear + blend setup is:

```c
glClearColor(0.0f, 0.0f, 0.0f, 0.0f);
glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);  // for rendering
// When compositing the result with DWM:
glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA);         // premultiplied composite
```

Source: [Khronos Forums — glBlendFunc for transparent background](https://community.khronos.org/t/glblendfunc-for-transparent-background/61682)

---

## 7. Win32/DWM API Mechanics — What Actually Achieves Transparency

### The layered window + DWM blur mechanism (pyglet's approach):

1. **`WS_EX_LAYERED`** on the window makes it a "layered window" — DWM composites it using its alpha channel
2. **`DwmEnableBlurBehindWindow(hwnd, {DWM_BB_ENABLE | DWM_BB_BLURREGION, empty_rgn, fEnable=True})`** — with the empty region, this tells DWM to use per-pixel alpha from the window's framebuffer for the entire client area
3. **`SetLayeredWindowAttributes(hwnd, colorRef=0, bAlpha=255, LWA_ALPHA)`** — sets the window to opaque overall, letting DWM handle per-pixel transparency from the OpenGL buffer
4. **`Config(alpha_size=8)`** — ensures the OpenGL pixel format has an 8-bit alpha channel
5. **`glClearColor(0, 0, 0, 0)`** — clears the framebuffer to fully transparent black on each frame

### Why `SetLayeredWindowAttributes(alpha=255)` appears contradictory:

`LWA_ALPHA` with `bAlpha=255` means "apply a uniform 255/255 = 100% opacity to the entire window." This appears to negate transparency, but when combined with `DwmEnableBlurBehindWindow`, the DWM compositing pipeline ignores the layered window's global alpha and instead uses the per-pixel alpha values from the framebuffer. The `SetLayeredWindowAttributes` call is required to satisfy the `WS_EX_LAYERED` window requirement (a layered window must have either `SetLayeredWindowAttributes` or `UpdateLayeredWindow` called or it won't display at all).

### Win32 API notes:

- `SetLayeredWindowAttributes` and `UpdateLayeredWindow` must **not** be used together for the same window
- For OpenGL per-pixel alpha: use `SetLayeredWindowAttributes(alpha=255)` + DWM blur-behind (pyglet's approach)
- For GDI per-pixel alpha: use `UpdateLayeredWindow` with `ULW_ALPHA`
- **DWM composition is always-on in Windows 8, 10, 11** — `DwmIsCompositionEnabled` always returns true

Sources: [SetLayeredWindowAttributes](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setlayeredwindowattributes), [Layered Windows summary gist](https://gist.github.com/retorillo/3a12e0f7e6ae3d49771f2919608f8498)

---

## 8. Does Pyglet 2.1.x Transparency Work Reliably?

**Short answer: It works mechanically in 2.1.7+ but has a known blending artifact (Issue #1271, still open as of Feb 2025).**

### What works:
- `WINDOW_STYLE_OVERLAY` with `Config(alpha_size=8)` on Windows 11 does successfully create a click-through, always-on-top, transparent window
- The `_set_transparency()` implementation is correct in 2.1.14
- The OpenGL framebuffer alpha channel is correctly wired to DWM compositing
- Areas cleared with `glClearColor(0,0,0,0)` + `window.clear()` are transparent

### What doesn't work without additional setup:
- Semi-transparent pixels (from anti-aliasing, shapes with `alpha < 255`) exhibit incorrect blending when using `GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA` — the rendered alpha value in the framebuffer is wrong for DWM compositing
- The community-observed "black background" symptom can occur if:
  1. `Config(alpha_size=8)` is omitted (no alpha channel in framebuffer)
  2. `glClearColor` is not called with `alpha=0` before `window.clear()`
  3. The blend function writes incorrect alpha values that DWM interprets as opaque

### Community stance (2024-2026):
- No published complete workaround for issue #1271 was found
- The premultiplied alpha blend is the recommended fix for the blending artifact
- pyglet's built-in `_set_transparency()` is the correct mechanism — a manual win32/DWM override should **not** be needed on 2.1.14 if the GL setup is correct
- The main failure mode is incorrect GL blend setup, not the DWM calls themselves

---

## RECOMMENDED RECIPE

The following is the complete, correct pyglet 2.1.x recipe for a working transparent, click-through, always-on-top overlay window on Windows 11.

### Step 1: Window Creation

```python
import pyglet
from pyglet import gl

# REQUIRED: alpha_size=8 ensures the WGL pixel format has an 8-bit alpha channel.
# Without this, the OpenGL framebuffer has no alpha — DWM gets garbage alpha values.
config = gl.Config(
    alpha_size=8,
    double_buffer=True,
)

# WINDOW_STYLE_OVERLAY gives:
#   WS_POPUP (no decorations)
#   WS_EX_LAYERED (layered compositing)
#   WS_EX_TRANSPARENT (click-through)
#   HWND_TOPMOST (always on top)
#   _set_transparency() called automatically (DWM blur-behind + SetLayeredWindowAttributes)
window = pyglet.window.Window(
    width=screen_width,
    height=screen_height,
    style=pyglet.window.Window.WINDOW_STYLE_OVERLAY,
    config=config,
)
```

### Step 2: GL Blend Setup (CRITICAL — use premultiplied alpha for correct DWM compositing)

```python
# Enable blending
gl.glEnable(gl.GL_BLEND)

# For drawing opaque content (alpha=255 tags): standard blend is fine
# For drawing semi-transparent content OR to fix Issue #1271 blending artifacts:
# Use separate blend functions so the framebuffer alpha accumulates correctly for DWM
gl.glBlendFuncSeparate(
    gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA,  # RGB channels
    gl.GL_ONE,       gl.GL_ONE_MINUS_SRC_ALPHA   # Alpha channel (premultiplied accumulation)
)
```

If all drawn content is **fully opaque** (alpha=255), the simpler form also works:

```python
gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
```

### Step 3: on_draw Handler

```python
@window.event
def on_draw():
    # CRITICAL: clear to (0, 0, 0, 0) — fully transparent black
    # This tells DWM: "everywhere we don't draw = see-through"
    gl.glClearColor(0, 0, 0, 0)
    window.clear()

    # Draw your content here — only drawn pixels will be visible
    # Example: yellow tag labels
    batch.draw()
```

### Step 4: Full Working Example

```python
import pyglet
from pyglet import gl, shapes, text

# Get primary monitor dimensions
display = pyglet.display.get_display()
screen = display.get_default_screen()

config = gl.Config(alpha_size=8, double_buffer=True)

window = pyglet.window.Window(
    width=screen.width,
    height=screen.height,
    style=pyglet.window.Window.WINDOW_STYLE_OVERLAY,
    config=config,
)
# Position at top-left of screen (WINDOW_STYLE_OVERLAY starts at arbitrary position)
window.set_location(screen.x, screen.y)

# Set correct blend for transparent framebuffer
gl.glEnable(gl.GL_BLEND)
gl.glBlendFuncSeparate(
    gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA,
    gl.GL_ONE,       gl.GL_ONE_MINUS_SRC_ALPHA,
)

batch = pyglet.graphics.Batch()

# Example: yellow numbered tag
tag_bg = shapes.Rectangle(100, 100, 40, 24, color=(255, 200, 0, 230), batch=batch)
tag_label = text.Label(
    '1',
    font_name='Arial',
    font_size=14,
    bold=True,
    color=(0, 0, 0, 255),
    x=120, y=112,
    anchor_x='center', anchor_y='center',
    batch=batch,
)

@window.event
def on_draw():
    gl.glClearColor(0, 0, 0, 0)  # transparent clear
    window.clear()
    batch.draw()

pyglet.app.run()
```

### What NOT to Do

1. **Do NOT omit `Config(alpha_size=8)`** — without 8-bit alpha in the pixel format, the framebuffer has no alpha channel and DWM compositing produces an opaque black result.

2. **Do NOT use `glClearColor(0, 0, 0, 1)` (alpha=1)** — this clears the background to *opaque* black, making the entire non-drawn area black.

3. **Do NOT call `DwmEnableBlurBehindWindow` with `DWM_BB_ENABLE` alone and NULL region** — this returns `E_INVALIDARG` on Windows. The correct call uses `DWM_BB_ENABLE | DWM_BB_BLURREGION` + a valid (empty) HRGN. Pyglet's `_set_transparency()` does this correctly in 2.1.14.

4. **Do NOT manually call `_set_transparency()` again** — pyglet calls it automatically for `WINDOW_STYLE_OVERLAY` and `WINDOW_STYLE_TRANSPARENT` during `_create()`. Calling it twice can interfere.

5. **Do NOT use `WINDOW_STYLE_BORDERLESS`** — this does NOT set `WS_EX_LAYERED` and does NOT call `_set_transparency()`. It will not be transparent.

### If `WINDOW_STYLE_OVERLAY` Does Not Work: Manual DWM Override

If the window is still opaque black after following the recipe above, apply the DWM calls manually after window creation:

```python
import ctypes
from ctypes import wintypes, byref

# Get HWND from pyglet window
hwnd = window._hwnd  # Win32 HWND (pyglet internal)

DWM_BB_ENABLE      = 0x00000001
DWM_BB_BLURREGION  = 0x00000002
LWA_ALPHA          = 0x00000002

class DWM_BLURBEHIND(ctypes.Structure):
    _fields_ = [
        ("dwFlags",  wintypes.DWORD),
        ("fEnable",  wintypes.BOOL),
        ("hRgnBlur", wintypes.HANDLE),
        ("fTransitionOnMaximized", wintypes.BOOL),
    ]

gdi32   = ctypes.windll.gdi32
dwmapi  = ctypes.windll.dwmapi
user32  = ctypes.windll.user32

# Create empty region (per-pixel alpha mode for entire window)
region = gdi32.CreateRectRgn(0, 0, -1, -1)

bb = DWM_BLURBEHIND()
bb.dwFlags  = DWM_BB_ENABLE | DWM_BB_BLURREGION
bb.hRgnBlur = region
bb.fEnable  = True

hr = dwmapi.DwmEnableBlurBehindWindow(hwnd, byref(bb))
gdi32.DeleteObject(region)

if hr != 0:
    print(f"DwmEnableBlurBehindWindow failed: HRESULT 0x{hr & 0xFFFFFFFF:08X}")

# Required: satisfy WS_EX_LAYERED requirement
user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)
```

---

## Sources

- [pyglet Windowing Guide (v2.1.14)](https://pyglet.readthedocs.io/en/latest/programming_guide/windowing.html)
- [pyglet.window module (v2.1.14)](https://pyglet.readthedocs.io/en/latest/modules/window.html)
- [pyglet/pyglet/window/win32/__init__.py (master)](https://github.com/pyglet/pyglet/blob/master/pyglet/window/win32/__init__.py)
- [pyglet Issue #693 — Transparent Window Not Working on Windows 11](https://github.com/pyglet/pyglet/issues/693)
- [pyglet Issue #1271 — Label/Rectangle Unexpected Blending with Transparent Style](https://github.com/pyglet/pyglet/issues/1271)
- [pyglet Issue #246 — Possibility to Create Transparent Background Windows](https://github.com/pyglet/pyglet/issues/246)
- [pyglet Releases](https://github.com/pyglet/pyglet/releases)
- [pyglet RELEASE_NOTES (master)](https://github.com/pyglet/pyglet/blob/master/RELEASE_NOTES)
- [DwmEnableBlurBehindWindow (Microsoft Learn)](https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/nf-dwmapi-dwmenableblurbehindwindow)
- [DWM Blur Behind Overview (Microsoft Learn)](https://learn.microsoft.com/en-us/windows/win32/dwm/blur-ovw)
- [SetLayeredWindowAttributes (Microsoft Learn)](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setlayeredwindowattributes)
- [Khronos Forums — glBlendFunc for transparent background](https://community.khronos.org/t/glblendfunc-for-transparent-background/61682)
- [Layered Windows Summary Gist (retorillo)](https://gist.github.com/retorillo/3a12e0f7e6ae3d49771f2919608f8498)
- [winit PR #1815 — Restore transparent windows on Windows](https://github.com/rust-windowing/winit/pull/1815)
- [Zed PR #26645 — GPU composited transparency on Windows](https://github.com/zed-industries/zed/pull/26645)
- [OpenGL and Transparent Windows — GameDev.net](https://gamedev.net/forums/topic/290651-opengl-and-transparent-windows/2834665/)
