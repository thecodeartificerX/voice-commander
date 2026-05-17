# Transparency Codebase Diff: SpriteWindow vs PickerModalWindow vs ElementsOverlayWindow

Generated from deep read of all five source files. All line references are to the current
state of the files in `src/voice_sprite/`.

---

## 1. Full Comparison Table

| Dimension | SpriteWindow (`window.py`) | PickerModalWindow (`picker_modal.py` → `_PygletModalWindow`) | ElementsOverlayWindow (`elements_overlay.py`) | Different? | Could cause opaque-black bg? |
|---|---|---|---|---|---|
| **`style=`** | `WINDOW_STYLE_OVERLAY` (line 111) | `WINDOW_STYLE_BORDERLESS` (line 258) | `WINDOW_STYLE_OVERLAY` (line 117) | Yes — picker uses BORDERLESS | No for elements (same as sprite) |
| **`gl.Config` passed** | `alpha_size=8, double_buffer=True, sample_buffers=0, samples=0` (lines 98-107) | None — no `config=` argument (line 251-259) | `alpha_size=8, double_buffer=True, sample_buffers=0, samples=0` (lines 98-107) | Picker passes no gl.Config | No for elements (same as sprite) |
| **`visible=` at construction** | Not specified → pyglet default `True` (line 108-114) | `visible=False` (line 257) | Not specified → pyglet default `True` (line 114-120) | Picker starts hidden | No for elements (same as sprite) |
| **`glEnable(GL_BLEND)`** | Called in `__init__`, line 125 | Never called | Called in `__init__`, line 128 | Picker omits it | No for elements (same as sprite) |
| **`glBlendFuncSeparate` args** | `(SRC_ALPHA, ONE_MINUS_SRC_ALPHA, ONE, ONE_MINUS_SRC_ALPHA)` — lines 126-131 | Never called | `(SRC_ALPHA, ONE_MINUS_SRC_ALPHA, ONE, ONE_MINUS_SRC_ALPHA)` — lines 129-134 | Picker omits it | No for elements (same as sprite) |
| **`glClearColor` in `__init__`** | `(0, 0, 0, 0)` — line 137 | Not called in `__init__` | `(0, 0, 0, 0)` — line 135 | Picker omits init-time call | No for elements (same as sprite) |
| **`glClearColor` in `on_draw`** | `(0, 0, 0, 0)` every frame — line 200 | `BG_RGBA` = `(16/255, 18/255, 24/255, 245/255)` — lines 534-535 (intentionally opaque) | `(0, 0, 0, 0)` every frame — line 178 | Picker uses opaque color (deliberate) | No for elements (same as sprite) |
| **`on_draw` clear** | `self.clear()` after glClearColor — line 201 | `self.clear()` after glClearColor — line 536 | `self.clear()` after glClearColor — line 179 | No | No |
| **What is drawn** | `pyglet.sprite.Sprite` + optional Labels — lines 227-284 | `pyglet.graphics.Batch` of `shapes.*` + Labels — line 537 | `pyglet.graphics.Batch` of `shapes.Rectangle` + Labels — line 180 | Sprite uses Sprite object; others use Batch | No |
| **Per-object blend on drawn items** | Explicit `blend_src/blend_dest` on Sprite object — lines 235-236 | Not set on batch shapes/labels | Not set on batch shapes/labels | Sprite window sets per-sprite blend explicitly | **Possible** — see analysis |
| **`switch_to()` before GL work** | Not called (single-context scenario, always current) | Called explicitly in `refresh()` — line 345; comment: "refresh() runs from a clock callback whose ambient context is whichever window pyglet drew last (usually the sprite's main window). Constructing Labels/Shapes under the wrong context binds their GL handles there; subsequent on_draw — which runs under *this* window's context — hits GL 0x1282." | **Never called** — shapes/labels built in `__init__` | Elements overlay never switches to its own context before creating GL objects | **YES — HIGH SUSPECT** |
| **When `apply_win32_flags()` called** | After construction, inline in `main()` — line 176; window already visible | In `_ensure_window()` which is called just before `show_noactivate()` — line 202; window still hidden | After construction, inline in `_create()` callback — line 241; window already visible | All three call after construction. Picker applies while hidden. | **Possible for picker**; no difference for elements vs sprite |
| **Window size** | Small: `window_w` × `window_h` (e.g. ~200×200 px at 1x DPI) | Medium: `DEFAULT_W=560` × `DEFAULT_H=502` | Full-monitor: `mr-ml` × `mb-mt` (e.g. 2560×1440) | Elements is full-monitor | **Possible** — see analysis |
| **Window position** | `set_location(x, y)` — bottom-right corner — line 147 | `set_location(x, y)` — centred via `_reposition()` | `set_location(ml, mt)` — monitor top-left — line 148 | No architectural difference | No |
| **Thread of construction** | pyglet main thread (synchronous in `main()`) | pyglet main thread (via `pyglet.clock.schedule_once`) | pyglet main thread (via `pyglet.clock.schedule_once` in `_create`) | Same for elements and picker; sprite is sync | No |
| **`apply_click_through` HWND access** | `canvas.hwnd` with `_hwnd` fallback — lines 298-306 | Directly via `self._hwnd` — line 278 | `canvas.hwnd` with `_hwnd` fallback — lines 184-187 | Picker uses undocumented `_hwnd` directly; elements mirrors sprite | No |
| **Granted alpha_size logged** | Yes — `self.context.config.alpha_size` — line 144 | No check | Yes — `self.context.config.alpha_size` — line 142 | No difference for elements | No |
| **vsync** | `vsync=False` (line 113) | Not specified (default True) | `vsync=False` (line 119) | Picker differs; no difference for elements | No |
| **caption / title bar** | Not specified (no caption) | `caption="vc-picker"` (line 255) | Not specified (no caption) | No difference for elements | No |

---

## 2. Detailed Notes on Key Dimensions

### 2a. `switch_to()` and GL context — the most critical dimension

`picker_modal.py` `refresh()` (lines 338-346) contains this explicit comment and call:

```python
def refresh(self) -> None:
    # Make this window's GL context current before any GL work.
    # refresh() runs from a clock callback whose ambient context is
    # whichever window pyglet drew last (usually the sprite's main
    # window). Constructing Labels/Shapes under the wrong context
    # binds their GL handles there; subsequent on_draw — which runs
    # under *this* window's context — hits GL 0x1282.
    self.switch_to()
```

`ElementsOverlayWindow.__init__` constructs its entire batch of `shapes.Rectangle` and
`pyglet.text.Label` objects starting at line 152, **without ever calling `self.switch_to()`
first**. This batch construction happens inside a `pyglet.clock.schedule_once` callback
(`_create` at line 228 of `__main__.py`). That callback fires during a pyglet event-loop
tick — and at that point the **ambient GL context is almost certainly the SpriteWindow's
context**, because SpriteWindow is the only continuously-drawing window. The
`ElementsOverlayWindow` super().__init__ call at line 114 creates the window's context,
but pyglet does not automatically make a newly-constructed window's context current for
subsequent code in `__init__`. The shapes and labels are therefore allocated in
SpriteWindow's GL context, not in the overlay's context. When `on_draw` later runs under
the overlay's context, those GL handles are either invalid or invisible, and the draw call
produces nothing visible from the batch — leaving only the `glClearColor(0,0,0,0)` + clear,
which under a broken alpha channel would render as opaque black.

### 2b. Full-monitor size and DWM behaviour

`ElementsOverlayWindow` spans an entire monitor (e.g. 2560×1440 or 3840×2160). On Windows
11, full-screen borderless overlay windows created with `WINDOW_STYLE_OVERLAY` go through a
different DWM composition path than small windows. In particular, DWM can decide to route the
full-screen window through a "full-screen optimisation" path that bypasses the layered-window
alpha compositing and instead composites the raw framebuffer against black. This is documented
in several pyglet and game-engine issues: `WINDOW_STYLE_OVERLAY` sets `WS_EX_LAYERED` which
ordinarily enables per-pixel alpha, but DWM's full-screen fast path can override this when
the window covers the entire desktop. The SpriteWindow avoids this because it is never
full-screen.

### 2c. Per-object blend modes on Batch shapes

`SpriteWindow` explicitly sets `blend_src` and `blend_dest` on each `pyglet.sprite.Sprite`
object (lines 235-236). `ElementsOverlayWindow` draws `shapes.Rectangle` and `Label` objects
inside a `Batch` without setting per-object blend modes. If pyglet's shape/label shaders do
not inherit the window-level `glBlendFuncSeparate`, the alpha channel of those shapes may be
written incorrectly to the framebuffer, producing values that DWM later composites as opaque.
However, this would more likely produce visible-but-wrongly-composited tags rather than a
fully black background, so it is a secondary suspect.

---

## 3. Ranked Suspect List

| Rank | Suspect | Confidence | Explanation |
|---|---|---|---|
| 1 | **GL context mismatch — shapes/labels built without `switch_to()` in `__init__`** | **HIGH** | All batch GL objects are created while SpriteWindow's context is current (same as picker bug documented in `refresh()` comment). The overlay's `on_draw` runs under the overlay's own context but finds only corrupt/missing handles. The batch draw produces nothing; the black comes from the framebuffer's clear against an opaque default. The picker explicitly fixed this same problem by adding `switch_to()`. Elements overlay never received that fix. |
| 2 | **Full-monitor size triggering DWM full-screen-optimisation bypass** | **MEDIUM** | DWM can suppress layered per-pixel alpha for windows that cover the entire monitor. This is the only structural difference between `ElementsOverlayWindow` and `SpriteWindow` that the transparent-overlay recipe cannot compensate for. Fix candidates: `SetWindowDisplayAffinity`, `DWM_CLOAKED`, or reducing the overlay to slightly less than full-screen. |
| 3 | **`apply_win32_flags()` called after `__init__` but GL objects constructed during `__init__`** | **LOW-MEDIUM** | For `SpriteWindow`, `apply_win32_flags()` is called after construction and before the first draw tick — this ordering is safe because the sprite GL objects are built lazily in `on_draw`. For `ElementsOverlayWindow`, all GL objects are built eagerly in `__init__` (lines 152-171), which runs before `apply_win32_flags()` (line 241 of `__main__.py`). If `DwmEnableBlurBehindWindow` resets any GL state or triggers a context switch, GL handles created before it are in a different state than handles created after. This is speculative but plausible. |
| 4 | **No per-object blend mode on batch shapes/labels** | **LOW** | SpriteWindow sets `blend_src/blend_dest` explicitly on its Sprite. The batch shapes/labels in `ElementsOverlayWindow` rely on pyglet's default shader blend, which may not inherit the `glBlendFuncSeparate` set in `__init__`. This would corrupt the alpha channel of the drawn shapes but is unlikely to explain a fully black background. |

---

## 4. Single Best Root-Cause Hypothesis

**The shapes and labels are allocated under the wrong GL context.**

When `_create` fires as a `pyglet.clock.schedule_once` callback, the event loop's ambient
GL context is `SpriteWindow`'s context (it is the only window that has been continuously
drawing). `ElementsOverlayWindow.__init__` calls `super().__init__()` which creates the
window and its OpenGL context, but pyglet 2.x does **not** automatically make a newly
created window's context current for code that follows in `__init__`. All of the
`pyglet.shapes.Rectangle` and `pyglet.text.Label` objects created on lines 152-171 of
`elements_overlay.py` therefore have their GL buffers, textures, and shader objects
registered in SpriteWindow's context, not in the overlay's context.

When the overlay's `on_draw` is later called under the overlay's own context:
- `self._batch.draw()` attempts to draw handles that belong to the other context.
- On Windows + OpenGL, using handles from context A while context B is current is
  undefined behaviour — typically the draw call silently produces nothing.
- The only pixels written are by `self.clear()`, which clears to `(0, 0, 0, 0)`.
- But the overlay's framebuffer has no functioning per-pixel alpha compositing (DWM never
  got valid `DwmEnableBlurBehindWindow` cooperation because the batch draw always fails),
  so DWM composites the cleared framebuffer against opaque black.

The **picker modal already discovered and solved this exact bug** by adding `self.switch_to()`
at the top of `refresh()`, with a comment explicitly calling out GL 0x1282 and the wrong-
context problem. `ElementsOverlayWindow` was written after but did not receive the same fix
because its GL objects are built in `__init__` rather than in a separate `refresh()`-style
method.

**Minimum fix:** Add `self.switch_to()` as the first statement inside
`ElementsOverlayWindow.__init__`, immediately after `super().__init__(...)` returns (and
before the `glEnable`/`glBlendFuncSeparate`/`glClearColor` calls and the batch object
construction loop). This makes the overlay's context current for all GL work in `__init__`,
matching the behaviour that the picker modal achieves via `refresh()`.

**Recommended fix (belt and suspenders):** Also investigate DWM full-screen-optimisation
(suspect #2) by testing with a window sized 2px smaller than the monitor on each edge. If
the window is transparent when not full-screen but opaque when full-screen, that confirms
suspect #2 as a co-contributor and a `SetWindowDisplayAffinity` / `WDA_EXCLUDEFROMCAPTURE`
call or `DwmSetWindowAttribute(DWMWA_CLOAK)` will be needed.
