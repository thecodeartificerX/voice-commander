# pyglet Multi-Window Transparency: GL Context Behavior & Pitfalls

**Research date:** 2026-05-17  
**pyglet version in use:** 2.1.14 (Windows 11, Python 3.11)  
**Problem:** Second pyglet transparent/overlay window created at runtime shows opaque black background instead of transparency.

---

## 1. How pyglet Manages Multiple Windows and GL Contexts

### One context per window, shared object space by default

Every `pyglet.window.Window` instance gets its **own independent OpenGL context**. However, by default all windows (and the hidden "shadow" context created when `pyglet.gl` is first imported) share the same **object space** — meaning textures, display lists, shader programs, and framebuffer objects are shared across contexts.

> "Each will be created with its own OpenGL context, however all contexts will share the same texture objects, display lists, shader programs, and so on, by default. Each context has its own state and framebuffers."  
> — [pyglet GL docs, v2.1.14](https://pyglet.readthedocs.io/en/latest/programming_guide/gl.html)

**Critical implication:** GL *state* — `glEnable(GL_BLEND)`, `glBlendFunc(...)`, `glClearColor(...)`, viewport, scissor, stencil — is **per-context**. Setting these on Window A does not affect Window B's context.

### The shadow context

When `pyglet.gl` is imported (happens implicitly when `pyglet.window` is used), a hidden shadow window/context is created. All subsequently created windows share object space with this shadow context. This is what allows textures and batches to be created before any visible window exists.

> "By default (when using the Window constructor to create the context) the most recently created context will be used [as the share target]."  
> — [pyglet context docs](https://pyglet.readthedocs.io/en/latest/programming_guide/context.html)

---

## 2. GL State Is Per-Context — `glClearColor`, `glBlendFunc`, `glEnable` Do Not Transfer

Because each window has its own context and its own GL state machine, if you configure blending/clear-color on Window 1, Window 2 starts with whatever OpenGL defaults apply to a fresh context:

| GL state call | Default value in fresh GL context |
|---|---|
| `glClearColor` | `(0.0, 0.0, 0.0, 0.0)` — black with alpha 0 |
| `glBlendFunc` | `GL_ONE, GL_ZERO` (no blending) |
| `glEnable(GL_BLEND)` | Disabled |

**For a transparent/overlay window:** transparency requires the framebuffer to be cleared to `(0, 0, 0, 0)` (fully transparent black) and blending enabled. If GL state is not re-applied in Window 2's context, the window clears to opaque black or renders without correct alpha blending.

---

## 3. When Is the Correct Context Current? (The Critical Detail)

### During `Window.__init__`

The `Window.__init__` method calls `self._create()` (which creates the OS window and GL context), then immediately calls **`self.switch_to()`**, followed by `self._create_projection()`. So at the end of `__init__`, the new window's context IS current.

Extracted from pyglet source (`pyglet/window/__init__.py`):
```python
def __init__(self, ...):
    # ... config setup, alpha_size=8 if transparent/overlay ...
    self._create()         # creates OS window + GL context
    self.switch_to()       # makes this window's context current
    self._create_projection()
    if visible:
        self.set_visible(True)
        self.activate()
```

**Therefore:** GL state set immediately after `Window(...)` returns (before you yield control back to the event loop) IS applied to the correct context.

### During `draw()` / `on_draw` dispatch

Every frame, pyglet calls `window.draw(dt)` for each window. The `draw()` method is:

```python
def draw(self, dt: float) -> None:
    self.switch_to()                    # <— context switch happens here
    self.dispatch_event('on_draw')
    self.dispatch_event('on_refresh', dt)
    self.flip()
```

So `on_draw` is always called with the window's own context current. Any GL state set inside `on_draw` is correctly scoped to that window.

> "When using pyglet.app.run() for the application event loop, pyglet ensures that the correct window is the active context before dispatching the on_draw() or on_resize() events."  
> — [pyglet GL docs](https://pyglet.readthedocs.io/en/latest/programming_guide/gl.html)

### During `clock.schedule_once` callbacks

This is where the pitfall lies. The event loop `idle()` method runs:

1. `clock.call_scheduled_functions(dt)` — **all scheduled callbacks fire here**
2. `_redraw_windows()` is scheduled separately (via the clock at 1/60s or 0 interval)

The clock tick and callback execution happen in `idle()` **before** the next `_redraw_windows` call. There is **no automatic `switch_to()` call** before a `schedule_once` callback fires. The active GL context at the time of the callback is **whatever was left current from the previous window draw cycle** — typically the *last window* that was drawn in the previous frame (which may be Window 1, the companion sprite).

**This is the core pitfall for the voice-commander scenario:**

When the `schedule_once` callback fires to create Window 2 (the overlay), Window 1's context is still current. `Window.__init__` will then:
- Call `self._create()` — creates Window 2's context
- Call `self.switch_to()` — switches to Window 2's context

At this point Window 2's context IS current. **However**, any GL objects (shapes, labels, batches) that are instantiated *before* `switch_to()` completes inside `__init__` could be bound to the wrong context. More importantly, GL state set in `__init__` or in setup code immediately following `Window(...)` must be done carefully since control may return to the event loop and the context may switch before `on_draw` fires.

---

## 4. Window Transparency Setup on Windows (Win32)

### How `_set_transparency` works

From the pyglet Win32 source (`pyglet/window/win32/__init__.py`), transparency on Windows uses:

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

And the window style mappings for transparent/overlay:
```python
WINDOW_STYLE_TRANSPARENT: (WS_OVERLAPPEDWINDOW, WS_EX_LAYERED),
WINDOW_STYLE_OVERLAY:     (WS_POPUP, WS_EX_LAYERED | WS_EX_TRANSPARENT),
```

`DwmEnableBlurBehindWindow` enables DWM to punch through the window to desktop — this is what makes the OS-level transparency work. `WS_EX_LAYERED` enables per-pixel alpha compositing by the DWM compositor.

**Key note on Windows 11:** The `DwmEnableBlurBehindWindow` API was documented as a no-op from Windows 8 onwards for the actual *blur* effect, but pyglet uses it with a full-window region to enable the per-pixel alpha transparency mode (not the blur visual). This still works on Windows 11 for transparency purposes — but it requires the GL framebuffer to have an alpha channel configured (`alpha_size=8`).

### `visible=False` interaction

When `visible=False` is passed to the constructor, `set_visible(True)` and `activate()` are not called during `__init__`. The window exists as an OS window but is hidden. The `_set_transparency()` call (which sets `DwmEnableBlurBehindWindow` + `WS_EX_LAYERED`) happens inside `_create()` based on the `style` attribute, **before** the visibility state is set. So transparency setup does occur for `visible=False` windows.

When you later call `window.set_visible(True)`, the DWM attributes are already applied. There should be no difference in transparency behavior between `visible=True` and `visible=False` construction, as long as the GL state (clear color, blend) is set correctly before the first draw.

---

## 5. GitHub Issues Found

### Issue #693 — "Transparent Window not working on Windows 11"
- URL: https://github.com/pyglet/pyglet/issues/693
- A single pyglet window with `WINDOW_STYLE_OVERLAY` showed a black background on Windows 11.
- Issue is **closed**; no public explanation of fix, but it was tagged "help wanted" and "windows" — likely resolved when pyglet 2.x improved Win32 overlay handling.
- Relevant: confirms that even a *single* transparent window can fail on Windows 11 if the config is wrong.

### Issue #1271 — "Pyglet Label and Shapes.Rectangle show unexpected blending with transparent window style"
- URL: https://github.com/pyglet/pyglet/issues/1271
- Using `Config(alpha_size=8)` + `style='transparent'` + standard `GL_SRC_ALPHA / GL_ONE_MINUS_SRC_ALPHA` blending caused shapes/labels to blend incorrectly with the desktop background (alpha values of the desktop visible through the window's own shapes).
- **Root cause implied:** The transparent framebuffer means drawn pixels' alpha values interact with DWM compositing. Using `GL_ONE_MINUS_SRC_ALPHA` for the destination means existing framebuffer alpha (0 = transparent) mixes with new draw's alpha — causing color fringing.
- **Workaround known in community:** Use premultiplied alpha blending: `glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)` for drawing on transparent windows, OR use `glClearColor(0, 0, 0, 1)` (alpha=1 for the clear) rather than `glClearColor(0, 0, 0, 0)`.

### Issue #373 — "Creating a second window with shadow context disabled corrupts the context"
- URL: https://github.com/pyglet/pyglet/issues/373
- With `pyglet.options["shadow_window"] = False`, creating a second window causes `GL_INVALID_OPERATION` during context initialization on Intel + macOS/Windows.
- Root cause: context object space not properly isolated when no shadow context exists.
- **Not directly applicable** to the voice-commander scenario (shadow_window is ON by default), but confirms second-window context creation can have edge-case failures.

### Issue #726 — "Multiple window initialization"
- URL: https://github.com/pyglet/pyglet/issues/726
- Objects (shapes) created without ensuring the correct context is current fail during draw for the second window with `GL_INVALID_OPERATION` at `glBindVertexArray`.
- **Workaround confirmed:** Create at least one shape immediately after each window instantiation to "prime" the context before the event loop takes over.

### Issue #1110 — "Sharing objects (shapes) between windows"
- URL: https://github.com/pyglet/pyglet/issues/1110
- Attempts to use the same `Batch` or `Shape` across two windows using shared contexts still fail at draw time for the second window.
- **Conclusion:** Even with shared object space, VAOs are not truly sharable between contexts in OpenGL (this is a GL spec limitation, not pyglet). Each window needs its own shape/batch objects.

### Issue #893 — "Multiple window applications" (workaround label)
- URL: https://github.com/pyglet/pyglet/issues/893
- Confirmed working pattern: drive all windows from a single `on_update` + `on_draw`, using `switch_to()` explicitly before drawing to each window.

---

## 6. Root Cause Analysis for the Voice-Commander Black Window

Combining all findings, there are **two likely simultaneous failure modes** for the second overlay window:

### Failure Mode A: GL state not set in Window 2's context

The `on_draw` handler for Window 2 does not call:
```python
glClearColor(0, 0, 0, 0)   # or (0, 0, 0, 1) depending on blend mode
glEnable(GL_BLEND)
glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
```

Because these were set for Window 1's context, and GL state is per-context, Window 2 starts with default GL state: `glClearColor(0, 0, 0, 0)` is actually correct by default, but `GL_BLEND` is disabled and `glBlendFunc` defaults to `GL_ONE, GL_ZERO`. Without blending enabled and `alpha_size=8` in the GL config, the framebuffer's alpha channel may not be allocated, causing the OS compositor to see an opaque (alpha=1) window.

**The most critical missing piece:** If Window 2 was created without `Config(alpha_size=8)`, the framebuffer has no alpha channel. The DWM compositor then sees no transparency information and renders the window as fully opaque.

### Failure Mode B: Missing `alpha_size=8` in GL config for Window 2

If Window 1 was created with a custom config including `alpha_size=8` and Window 2 was created without an explicit config (or with the default config), Window 2's framebuffer lacks an alpha channel. `DwmEnableBlurBehindWindow` is called (since `style=overlay` triggers it), but the compositor has no per-pixel alpha to work with, so it composites the window as fully opaque — black clear color.

### Failure Mode C: Shapes/objects bound to wrong context (VAO issue)

If shape/label objects for Window 2 are created as part of window `__init__` before `switch_to()` has been called, or in a code path where Window 1's context is still current, their VAOs are registered in Window 1's context. When Window 2's `on_draw` fires (after `switch_to(Window2)`), binding those VAOs causes `GL_INVALID_OPERATION`, which may silently fail or produce garbage rendering.

---

## RECOMMENDED APPROACH

### Step 1: Always pass `Config(alpha_size=8)` to the second window

```python
config = pyglet.gl.Config(alpha_size=8, double_buffer=True)
overlay = pyglet.window.Window(
    width=screen.width,
    height=screen.height,
    style=pyglet.window.Window.WINDOW_STYLE_OVERLAY,
    config=config,
    visible=False,   # hide until ready
)
```

**This is the most common root cause.** Without an 8-bit alpha channel in the framebuffer, DWM cannot composite transparency regardless of any other setting.

### Step 2: Call `switch_to()` + set all GL state immediately after construction, before returning to the event loop

```python
overlay = pyglet.window.Window(style='overlay', config=config, visible=False)
overlay.switch_to()                          # ensure this window's context is active
glEnable(GL_BLEND)
glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
glClearColor(0, 0, 0, 0)                    # fully transparent clear
```

Do this immediately in the `schedule_once` callback after constructing the window. Do NOT defer GL state setup to a later callback or `on_draw` — the context must be primed before the event loop draws it.

### Step 3: Re-apply GL state inside `on_draw`

Because the event loop calls `switch_to()` before `on_draw` (so the context is correct), and because `draw()` itself does `switch_to()`, it is safe and correct to re-apply GL state at the top of `on_draw`:

```python
@overlay.event
def on_draw():
    overlay.clear()                           # uses current context's glClearColor
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    # ... draw shapes/labels ...
```

Or rely on the fact that GL state persists across frames in the same context — set it once after `switch_to()` post-construction (Step 2) and only reset if it gets clobbered.

### Step 4: Create Window 2's shapes AFTER construction (with correct context)

All `pyglet.shapes`, `pyglet.text.Label`, and `pyglet.graphics.Batch` objects used exclusively in Window 2 must be created **after** `Window.__init__` completes (i.e., after `switch_to()` has been called on the new window), and with Window 2's context current:

```python
def create_overlay(dt):
    config = pyglet.gl.Config(alpha_size=8, double_buffer=True)
    overlay = pyglet.window.Window(
        style=pyglet.window.Window.WINDOW_STYLE_OVERLAY,
        config=config,
        visible=False,
    )
    overlay.switch_to()
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    glClearColor(0, 0, 0, 0)

    # Create all shapes NOW, while overlay's context is current
    batch = pyglet.graphics.Batch()
    labels = [pyglet.text.Label(str(i), batch=batch, ...) for i in range(n)]
    
    overlay.set_visible(True)
    # store overlay, batch, labels on some owner object
```

### Step 5: For blend mode on transparent windows — beware Issue #1271

If shapes appear to "bleed" desktop color through them (colors shift depending on background), switch to premultiplied alpha:

```python
glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
```

And ensure shape colors use premultiplied alpha values, or use `(r*a, g*a, b*a, a)` for RGB components.

### Step 6: Do NOT share `Batch` or `Shape` objects between Window 1 and Window 2

VAOs are context-specific and cannot be shared even within a shared object space. Each window must have its own shapes and batches. Textures and shader programs CAN be shared (they live in shared object space), but vertex arrays cannot.

---

## Summary Table

| Question | Answer |
|---|---|
| Does each pyglet window get its own GL context? | Yes — separate state machines, shared object space |
| Is GL state (blend, clear color) per-context? | Yes — must be re-set for each new window's context |
| Does pyglet switch context before `on_draw`? | Yes — `draw()` calls `switch_to()` before dispatching `on_draw` |
| Is context correct during `Window.__init__`? | Yes — `switch_to()` is called during `__init__`, context is current at end of constructor |
| Is context correct during `schedule_once` callback? | Not guaranteed — whichever window last drew is still current; call `switch_to()` explicitly |
| Does `visible=False` affect transparency setup? | No — DWM attributes set regardless of visible param |
| Most common cause of black second overlay window? | Missing `Config(alpha_size=8)` for the second window |
| Second most common cause? | GL blend/clear state not applied to Window 2's context |
| Can shapes be shared between windows? | Textures/shaders yes; VAOs/batches/shapes NO |

---

## Sources

- [pyglet GL interface docs v2.1.14](https://pyglet.readthedocs.io/en/latest/programming_guide/gl.html)
- [pyglet Creating an OpenGL context](https://pyglet.readthedocs.io/en/latest/programming_guide/context.html)
- [pyglet Windowing guide v2.1.14](https://pyglet.readthedocs.io/en/latest/programming_guide/windowing.html)
- [pyglet window module v2.1.14](https://pyglet.readthedocs.io/en/latest/modules/window.html)
- [pyglet application event loop guide](https://pyglet.readthedocs.io/en/latest/programming_guide/eventloop.html)
- [pyglet win32 window source](https://github.com/pyglet/pyglet/blob/master/pyglet/window/win32/__init__.py)
- [pyglet window/__init__.py source](https://github.com/pyglet/pyglet/blob/master/pyglet/window/__init__.py)
- [pyglet app/base.py source](https://github.com/pyglet/pyglet/blob/master/pyglet/app/base.py)
- [Issue #693 — Transparent Window not working on Windows 11](https://github.com/pyglet/pyglet/issues/693)
- [Issue #1271 — Label/Shapes unexpected blending with transparent window](https://github.com/pyglet/pyglet/issues/1271)
- [Issue #373 — Second window with shadow context disabled corrupts context](https://github.com/pyglet/pyglet/issues/373)
- [Issue #726 — Multiple window initialization GL errors](https://github.com/pyglet/pyglet/issues/726)
- [Issue #1110 — Sharing objects between windows](https://github.com/pyglet/pyglet/issues/1110)
- [Issue #893 — Multiple window applications (workaround: switch_to pattern)](https://github.com/pyglet/pyglet/issues/893)
- [Using multiple windows (legacy guide)](https://pythonhosted.org/pyglet/programming_guide/using_multiple_windows.html)
- [DwmEnableBlurBehindWindow — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/nf-dwmapi-dwmenableblurbehindwindow)
- [SetLayeredWindowAttributes — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setlayeredwindowattributes)
