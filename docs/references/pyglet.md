# pyglet 2.x Reference

> Cited from: https://github.com/pyglet/pyglet (2026-04-22)
> Cited from: https://pyglet.readthedocs.io/en/latest/modules/window.html (2026-04-22)
> Cited from: https://pyglet.readthedocs.io/en/latest/modules/sprite.html (2026-04-22)
> Cited from: https://pyglet.readthedocs.io/en/latest/modules/text/index.html (2026-04-22)
> Cited from: https://pyglet.readthedocs.io/en/latest/modules/clock.html (2026-04-22)
> Cited from: https://pyglet.readthedocs.io/en/latest/modules/app.html (2026-04-22)
> Cited from: https://github.com/pyglet/pyglet/issues/693 (2026-04-22)

---

## Overview

`pyglet` is a cross-platform windowing, graphics, and multimedia library for Python. Voice Commander's sprite companion feature uses pyglet 2.x for:

- Creating borderless, transparent overlay windows on Windows
- Loading and slicing sprite sheets
- Rendering sprites with color/opacity blending
- Text rendering with Labels
- Frame-synchronized animation via `clock.schedule_interval()`

This document covers every public API needed for the sprite companion implementation.

## Installation

```bash
pip install pyglet
```

pyglet 2.x requires Python 3.8+. No additional dependencies needed for Windows; Linux and macOS may require additional system libraries for audio/GL.

---

## Table of Contents

1. [Window Creation](#window-creation)
2. [Getting Win32 HWND](#getting-win32-hwnd)
3. [Image Loading and Sprite Sheets](#image-loading-and-sprite-sheets)
4. [Sprites](#sprites)
5. [Text Labels](#text-labels)
6. [Clock and Scheduling](#clock-and-scheduling)
7. [Event Loop](#event-loop)
8. [Display and Screen](#display-and-screen)
9. [Windows-Specific Gotchas](#windows-specific-gotchas)
10. [Version Compatibility](#version-compatibility)

---

## Window Creation

### `pyglet.window.Window`

Creates a platform-native window for rendering.

```python
import pyglet

window = pyglet.window.Window(
    width=800,
    height=600,
    caption="My Window",
    resizable=False,
    style=pyglet.window.Window.WINDOW_STYLE_BORDERLESS,
    vsync=True,
)
```

#### Constructor Signature

```python
Window(
    width=None,                    # int: window width (default: 640)
    height=None,                   # int: window height (default: 480)
    caption="pyglet",              # str: window title bar text
    resizable=False,               # bool: allow user to resize window
    style=WINDOW_STYLE_DEFAULT,    # Window style constant (see below)
    fullscreen=False,              # bool: create fullscreen window
    visible=True,                  # bool: show window immediately
    vsync=True,                    # bool: enable vertical sync (limits FPS to monitor refresh)
    display=None,                  # Display: which display to create on (default: primary)
    screen=None,                   # Screen: which screen to create on
    config=None,                   # GLConfig: OpenGL context config
    context=None,                  # GLContext: OpenGL context (advanced)
)
```

#### Window Styles

pyglet supports six window styles (non-fullscreen):

| Style | Constant | Description |
|-------|----------|-------------|
| Default | `WINDOW_STYLE_DEFAULT` | OS-standard window with title bar, border, minimize/maximize buttons |
| Dialog | `WINDOW_STYLE_DIALOG` | Dialog-style window (smaller buttons, no minimize) |
| Tool | `WINDOW_STYLE_TOOL` | Tool window (minimal decorations, smaller title bar) |
| Borderless | `WINDOW_STYLE_BORDERLESS` | No window decorations, no way to resize or move by user |
| Transparent | `WINDOW_STYLE_TRANSPARENT` | **Windows-only.** Borderless with transparent background framebuffer. Requires compatible graphics card. |
| Overlay | `WINDOW_STYLE_OVERLAY` | **Windows-only.** Overlay window (always-on-top, transparent). Similar to `WINDOW_STYLE_TRANSPARENT`. |

#### Transparent Backgrounds on Windows

To create a transparent overlay window on Windows:

```python
window = pyglet.window.Window(
    width=400,
    height=400,
    style=pyglet.window.Window.WINDOW_STYLE_TRANSPARENT,
    resizable=False,
    caption="Transparent Overlay",
)
```

**Important:** The `WINDOW_STYLE_TRANSPARENT` and `WINDOW_STYLE_OVERLAY` styles **only work on Windows**. They set a transparent framebuffer if both the graphics card and windowing system support it. On Windows 11, there are known compatibility issues (see [Windows-Specific Gotchas](#windows-specific-gotchas)).

For a borderless-but-opaque window on all platforms, use `WINDOW_STYLE_BORDERLESS`.

#### Window Attributes and Methods

| Attribute | Type | Description |
|-----------|------|-------------|
| `.width` | int | Current window width in pixels |
| `.height` | int | Current window height in pixels |
| `.caption` | str | Window title bar text (settable) |
| `.resizable` | bool | Whether window can be resized |
| `.style` | str | Current window style |
| `.visible` | bool | Whether window is visible (settable) |
| `.fullscreen` | bool | Whether window is fullscreen (settable) |

| Method | Signature | Description |
|--------|-----------|-------------|
| `.set_minimum_size(width, height)` | `(int, int) -> None` | Set minimum resizable window size |
| `.set_maximum_size(width, height)` | `(int, int) -> None` | Set maximum resizable window size |
| `.close()` | `() -> None` | Close the window and destroy resources |
| `.clear()` | `(float, float, float, float) -> None` | Clear the window with RGBA color (each 0.0–1.0) |
| `.flip()` | `() -> None` | Swap front and back buffers (auto-called at end of event handlers, optional to call manually) |

#### Event Handlers

Bind event handlers by decorating methods:

```python
@window.event
def on_draw():
    """Called every frame. Render your scene here."""
    window.clear()
    # ... draw sprites, labels, etc.

@window.event
def on_close():
    """Called when user closes the window."""
    pyglet.app.exit()

@window.event
def on_key_press(symbol, modifiers):
    """Called when a key is pressed."""
    if symbol == pyglet.window.key.ESCAPE:
        pyglet.app.exit()

@window.event
def on_mouse_motion(x, y, dx, dy):
    """Called when mouse moves. dx, dy are deltas."""
    pass

@window.event
def on_mouse_press(x, y, button, modifiers):
    """Called when mouse button pressed."""
    # button: 1=left, 2=middle, 4=right
    pass
```

---

## Getting Win32 HWND

### Accessing the Window Handle on Windows

To get the native Win32 `HWND` (required for interop with Win32 APIs like `SetParent`, `SetWindowPos`, etc.):

```python
import pyglet

window = pyglet.window.Window(width=400, height=400)

# Main window handle (the actual window)
hwnd = window._hwnd

# Client area / view window handle (where rendering happens)
view_hwnd = window._view_hwnd

print(f"Main HWND: {hwnd}")
print(f"View HWND: {view_hwnd}")
```

#### Important Caveats

1. **Private attributes:** `_hwnd` and `_view_hwnd` are **private** (underscore-prefixed). They are **not part of the public API** and may change in future pyglet versions.

2. **Windows-only:** These attributes only exist on Windows. Accessing them on Linux/macOS will raise `AttributeError`.

3. **Type:** Both are integers (ctypes-wrapped Win32 `HWND` values).

4. **Use case:** Typically used for Win32 interop with `ctypes`, `pywin32`, or other Windows APIs.

#### Example: Parent a pyglet Window to Another Window

```python
import ctypes
import pyglet

window = pyglet.window.Window(width=400, height=400, style=pyglet.window.Window.WINDOW_STYLE_BORDERLESS)

# Parent to another HWND (e.g., a Qt window)
other_hwnd = 0x12345678  # some other window handle
SetParent = ctypes.windll.user32.SetParent
SetParent(window._hwnd, other_hwnd)
```

---

## Image Loading and Sprite Sheets

### `pyglet.image.load(path)`

Loads an image file from disk. Supports PNG, JPG, GIF, BMP, and other formats.

```python
import pyglet

# Load from file path
image = pyglet.image.load("path/to/image.png")
print(f"Image dimensions: {image.width}x{image.height}")

# Load from absolute path
image = pyglet.image.load("/absolute/path/to/sprite_sheet.png")
```

#### Return Type

`pyglet.image.load()` returns an `AbstractImage` object. The concrete type depends on the file:

- **`ImageData`** — most common; in-memory image data
- **`Texture`** — GPU-resident texture (for OpenGL rendering)

For sprite companion purposes, treat the return as an `AbstractImage` — the type is transparent to users.

#### Image Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `.width` | int | Image width in pixels |
| `.height` | int | Image height in pixels |
| `.texture` | Texture | Get the GPU texture (auto-created on first access) |

---

### Sprite Sheet Slicing with `get_region()`

To extract a rectangular region from an image (e.g., one animation frame from a sprite sheet):

```python
import pyglet

sprite_sheet = pyglet.image.load("sprite_sheet.png")

# Extract a 64x64 frame at position (x=0, y=128)
frame = sprite_sheet.get_region(
    x=0,
    y=128,
    width=64,
    height=64,
)

# frame is also an AbstractImage, can be drawn or sliced further
print(f"Frame: {frame.width}x{frame.height}")
```

#### `get_region()` Signature

```python
AbstractImage.get_region(
    x: int,      # x-coordinate of bottom-left corner (pixels from left)
    y: int,      # y-coordinate of bottom-left corner (pixels from bottom)
    width: int,  # width of region (pixels)
    height: int, # height of region (pixels)
) -> AbstractImage
```

#### Important: Origin is Bottom-Left

Sprite sheet coordinates in pyglet use **bottom-left as origin (0, 0)**, following OpenGL convention. This differs from many image editors which use top-left. To extract a frame at visual row 2 (64-pixel frames, top-to-bottom), use:

```python
visual_row = 2
y_coord = sprite_sheet.height - (visual_row + 1) * frame_height

frame = sprite_sheet.get_region(x=0, y=y_coord, width=64, height=64)
```

#### Creating a Sprite Grid (Common Pattern)

```python
def extract_sprite_grid(image_path, frame_width, frame_height):
    """Extract all frames from a sprite sheet into a 2D grid."""
    sheet = pyglet.image.load(image_path)
    grid = []
    
    rows = sheet.height // frame_height
    cols = sheet.width // frame_width
    
    for row in range(rows):
        row_frames = []
        for col in range(cols):
            y = sheet.height - (row + 1) * frame_height
            x = col * frame_width
            frame = sheet.get_region(
                x=x,
                y=y,
                width=frame_width,
                height=frame_height,
            )
            row_frames.append(frame)
        grid.append(row_frames)
    
    return grid

# Usage
frames = extract_sprite_grid("character_sheet.png", frame_width=64, frame_height=64)
print(f"Extracted {len(frames)} rows, {len(frames[0])} cols")
```

---

## Sprites

### `pyglet.sprite.Sprite`

A sprite is a 2D image that can be positioned, rotated, scaled, and colored in a window.

```python
import pyglet

image = pyglet.image.load("character.png")
sprite = pyglet.sprite.Sprite(
    img=image,
    x=100,
    y=200,
)

@window.event
def on_draw():
    window.clear()
    sprite.draw()
```

#### Constructor Signature

```python
Sprite(
    img: AbstractImage,     # Image to draw
    x=0,                    # float: x position (pixels from left)
    y=0,                    # float: y position (pixels from bottom)
    z=0,                    # float: z-depth (for sorting/layering, default 0)
    batch=None,             # Batch: optional batch for efficient drawing
    group=None,             # Group: optional rendering group
    subpixel=False,         # bool: allow sub-pixel positioning (smoother, slightly slower)
)
```

#### Sprite Attributes and Methods

| Attribute | Type | Description | Settable |
|-----------|------|-------------|----------|
| `.image` | AbstractImage | The sprite's image | Yes |
| `.x` | float | X position (pixels from left) | Yes |
| `.y` | float | Y position (pixels from bottom) | Yes |
| `.z` | float | Z-depth for sorting | Yes |
| `.rotation` | float | Rotation in degrees (0–360, counter-clockwise) | Yes |
| `.scale` | float | Scale factor (1.0 = normal size, 2.0 = 2x larger) | Yes |
| `.scale_x` | float | Horizontal scale (independent of `scale`) | Yes |
| `.scale_y` | float | Vertical scale (independent of `scale`) | Yes |
| `.color` | (int, int, int) | RGB color tint (0–255 per channel). Alpha via `.opacity` | Yes |
| `.opacity` | int | Alpha value 0–255. 255 = opaque, 0 = fully transparent | Yes |
| `.width` | int | Sprite width including scale | Read-only |
| `.height` | int | Sprite height including scale | Read-only |
| `.visible` | bool | Whether sprite is drawn | Yes |

| Method | Signature | Description |
|--------|-----------|-------------|
| `.draw()` | `() -> None` | Draw the sprite to the current window |
| `.delete()` | `() -> None` | Delete the sprite and free its resources |

#### Color and Opacity Example

```python
sprite = pyglet.sprite.Sprite(img, x=100, y=100)

# Set RGB tint (0–255 per channel)
sprite.color = (255, 0, 0)  # Red tint

# Set opacity (0–255)
sprite.opacity = 128  # 50% transparent

# Both together
sprite.color = (255, 255, 255)  # White (no tint)
sprite.opacity = 255             # Fully opaque

# Fade effect
for alpha in range(255, 0, -1):
    sprite.opacity = alpha
    # ... draw frame ...
```

#### Efficient Rendering with Batches

Drawing many sprites individually (calling `.draw()` for each) is slow. Use a `Batch` to render them all at once:

```python
import pyglet

batch = pyglet.graphics.Batch()

sprites = [
    pyglet.sprite.Sprite(img1, x=0, y=0, batch=batch),
    pyglet.sprite.Sprite(img2, x=50, y=50, batch=batch),
    pyglet.sprite.Sprite(img3, x=100, y=100, batch=batch),
]

@window.event
def on_draw():
    window.clear()
    batch.draw()  # Draws all sprites in one call
```

---

## Text Labels

### `pyglet.text.Label`

Renders text strings with customizable font, size, color, and alignment.

```python
import pyglet

label = pyglet.text.Label(
    text="Hello, World!",
    x=100,
    y=100,
    font_name="Arial",
    font_size=14,
    color=(255, 255, 255, 255),  # RGBA: white, fully opaque
    anchor_x="center",
    anchor_y="center",
)

@window.event
def on_draw():
    window.clear()
    label.draw()
```

#### Constructor Signature

```python
Label(
    text: str = "",                    # Text to display
    x: float = 0,                      # X position
    y: float = 0,                      # Y position
    width=None,                        # int: max width for wrapping (None = no wrap)
    height=None,                       # int: max height for clipping
    anchor_x: str = "left",            # Horizontal anchor: "left", "center", "right"
    anchor_y: str = "bottom",          # Vertical anchor: "bottom", "baseline", "center", "top"
    font_name: str = None,             # Font name (e.g., "Arial", "Times New Roman")
    font_size: float = 12,             # Font size in points
    bold: bool = False,                # Bold text
    italic: bool = False,              # Italic text
    color: tuple = (255, 255, 255, 255), # RGBA color (0–255 per channel)
    batch: Batch = None,               # Optional batch for efficient rendering
    group: Group = None,               # Optional rendering group
)
```

#### Label Attributes and Methods

| Attribute | Type | Description | Settable |
|-----------|------|-------------|----------|
| `.text` | str | Text content | Yes |
| `.x` | float | X position | Yes |
| `.y` | float | Y position | Yes |
| `.width` | int | Max width (for wrapping) | Yes |
| `.height` | int | Max height (for clipping) | Yes |
| `.font_name` | str | Font name | Yes |
| `.font_size` | float | Font size in points | Yes |
| `.bold` | bool | Bold flag | Yes |
| `.italic` | bool | Italic flag | Yes |
| `.color` | (int, int, int, int) | RGBA color | Yes |
| `.anchor_x` | str | Horizontal anchor | Yes |
| `.anchor_y` | str | Vertical anchor | Yes |

| Method | Signature | Description |
|--------|-----------|-------------|
| `.draw()` | `() -> None` | Draw the label to the current window |

#### Color with Alpha

The `color` attribute is a 4-tuple of `(R, G, B, A)` where each component is 0–255:

```python
label = pyglet.text.Label(
    text="Semi-transparent red",
    x=100,
    y=100,
    color=(255, 0, 0, 128),  # Red with 50% opacity
)

# Modify color after creation
label.color = (255, 255, 255, 255)  # White, fully opaque
```

#### Anchor Points

- **`anchor_x`:** `"left"`, `"center"`, or `"right"` — determines which part of the text aligns with `x`
- **`anchor_y`:** `"bottom"`, `"baseline"`, `"center"`, or `"top"` — determines which part of the text aligns with `y`

```python
# Center the text at (400, 300)
label = pyglet.text.Label(
    text="Centered",
    x=400,
    y=300,
    anchor_x="center",
    anchor_y="center",
)
```

#### Available Fonts

System-installed fonts are available by name. Common fonts on Windows:

- Arial, Helvetica, Times New Roman, Courier New, Verdana, Trebuchet MS
- `None` (or not specified) uses the default system font

To list available fonts (platform-dependent):

```python
import pyglet.font
print(pyglet.font.get_system_fonts())  # Returns a set of font names
```

---

## Clock and Scheduling

### `pyglet.clock.schedule_interval()`

Schedules a function to be called repeatedly at fixed time intervals. Calls are synchronized with the event loop's frame tick.

```python
import pyglet

def update(dt):
    """Called ~60 times per second (if FPS ~60)."""
    print(f"Time since last call: {dt:.4f}s")

pyglet.clock.schedule_interval(update, 1/60)  # Call 60 times per second
# or
pyglet.clock.schedule_interval(update, 0.016)  # ~60 FPS (16.67 ms per frame)
```

#### Signature

```python
pyglet.clock.schedule_interval(
    func: Callable[[float], None],
    interval: float,
) -> ScheduledEvent
```

#### Callback Signature

```python
def scheduled_function(dt: float) -> None:
    """
    dt: elapsed time in seconds since the last call.
    
    Due to scheduling latency, load, and timer imprecision, dt may be
    slightly more or less than the requested interval.
    """
    pass
```

The `dt` parameter is **always passed**, even if you don't use it. It enables **frame-rate-independent** updates (e.g., moving objects at velocity regardless of FPS).

#### Example: Frame-Rate-Independent Movement

```python
import pyglet

class MovingSprite:
    def __init__(self, sprite, velocity_x, velocity_y):
        self.sprite = sprite
        self.vx = velocity_x  # pixels per second
        self.vy = velocity_y
    
    def update(self, dt):
        """Move sprite by velocity * dt."""
        self.sprite.x += self.vx * dt
        self.sprite.y += self.vy * dt

sprite = pyglet.sprite.Sprite(image, x=100, y=100)
mover = MovingSprite(sprite, velocity_x=100, velocity_y=0)

# Schedule update at ~60 FPS
pyglet.clock.schedule_interval(mover.update, 1/60)
```

#### Unscheduling

To stop a scheduled callback:

```python
event_id = pyglet.clock.schedule_interval(update, 1/60)
# later...
pyglet.clock.unschedule(event_id)
```

#### Clock API Reference

| Function | Signature | Description |
|----------|-----------|-------------|
| `schedule_interval(func, interval)` | `(Callable, float) -> ScheduledEvent` | Schedule function to repeat at interval |
| `schedule_once(func, delay)` | `(Callable, float) -> ScheduledEvent` | Schedule function to run once after delay |
| `unschedule(event)` | `(ScheduledEvent) -> None` | Unschedule a scheduled event |
| `tick(poll=True)` | `(bool) -> float` | Manually tick the clock and return elapsed time |

---

## Event Loop

### `pyglet.app.run()`

Starts the event loop. **Blocking call** that does not return until the application exits.

```python
import pyglet

window = pyglet.window.Window()

@window.event
def on_draw():
    window.clear()

pyglet.app.run()  # Blocks here until pyglet.app.exit() is called
print("App exited")
```

#### Key Characteristics

1. **Blocking:** `run()` does not return until `exit()` is called. All code after `run()` is unreachable unless `exit()` is called.

2. **Single-threaded:** `app.run()` **must be called from the same thread that imports `pyglet.app`**. All pyglet rendering and event processing happens on this thread.

3. **Automatic clock tick:** The event loop calls `pyglet.clock.tick()` once per frame, which triggers all scheduled callbacks.

4. **Frame rate:** Frames are synced to the display's refresh rate (typically 60 Hz) unless `vsync=False` is set on the window.

#### Exiting

```python
pyglet.app.exit()  # Set the exit flag; loop will stop after current frame

# Or in an event handler:
@window.event
def on_key_press(symbol, modifiers):
    if symbol == pyglet.window.key.ESCAPE:
        pyglet.app.exit()
```

#### Threading Implications

Pyglet's rendering pipeline is **not thread-safe**. Never call:
- Window methods (except `flip()`)
- Sprite/Label `.draw()` methods
- `pyglet.image.load()`
- `pyglet.clock.schedule_*()`

...from any thread other than the one running `app.run()`.

**Safe cross-thread communication:** Use a `queue.Queue` or `threading.Event` to communicate with the main thread:

```python
import threading
import queue
import pyglet

work_queue = queue.Queue()

def background_work():
    """Runs in a separate thread."""
    for i in range(10):
        work_queue.put(f"Task {i}")

def process_work(dt):
    """Called from the main pyglet thread."""
    try:
        task = work_queue.get_nowait()
        print(f"Processing: {task}")
    except queue.Empty:
        pass

window = pyglet.window.Window()
pyglet.clock.schedule_interval(process_work, 1/60)

thread = threading.Thread(target=background_work, daemon=True)
thread.start()

pyglet.app.run()
```

---

## Display and Screen

### Getting Screen Information

```python
from pyglet import canvas

display = canvas.get_display()
screen = display.get_default_screen()

print(f"Screen resolution: {screen.width}x{screen.height}")
print(f"Screen position: ({screen.x}, {screen.y})")
```

#### API Reference

| Function | Signature | Return | Description |
|----------|-----------|--------|-------------|
| `canvas.get_display()` | `() -> Display` | Display object | Get the default display |
| `Display.get_default_screen()` | `() -> Screen` | Screen object | Get the primary screen |
| `Display.get_screens()` | `() -> [Screen]` | List of Screen objects | Get all connected screens |

#### Screen Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `.width` | int | Screen width in pixels |
| `.height` | int | Screen height in pixels |
| `.x` | int | Global X position of screen's top-left corner |
| `.y` | int | Global Y position of screen's top-left corner |

#### Multi-Monitor Example

```python
from pyglet import canvas

display = canvas.get_display()
screens = display.get_screens()

for i, screen in enumerate(screens):
    print(f"Screen {i}: {screen.width}x{screen.height} @ ({screen.x}, {screen.y})")

# Create window on second screen
if len(screens) > 1:
    window = pyglet.window.Window(width=400, height=400, screen=screens[1])
```

---

## Windows-Specific Gotchas

### 1. Transparent Windows on Windows 11

**Status:** Known issue. Transparent windows (`WINDOW_STYLE_TRANSPARENT` or `WINDOW_STYLE_OVERLAY`) have limited support on Windows 11.

**Workaround:**
- Test thoroughly on target Windows versions (Windows 10 vs. 11 may behave differently).
- Consider fallback to `WINDOW_STYLE_BORDERLESS` with a fixed opaque background if transparency fails.
- Check the pyglet GitHub issue tracker for updates: https://github.com/pyglet/pyglet/issues/693

**Test snippet:**
```python
import pyglet

try:
    window = pyglet.window.Window(
        width=400,
        height=400,
        style=pyglet.window.Window.WINDOW_STYLE_TRANSPARENT,
    )
    print("Transparent window created successfully")
except Exception as e:
    print(f"Transparent window failed: {e}")
    # Fallback
    window = pyglet.window.Window(
        width=400,
        height=400,
        style=pyglet.window.Window.WINDOW_STYLE_BORDERLESS,
    )
```

### 2. DPI and High-DPI Displays

**Issue:** On Windows with DPI scaling (e.g., 125%, 150% on 4K monitors), pyglet may not automatically scale graphics correctly.

**Impact:** Sprites and text may appear small or distorted on high-DPI displays.

**Mitigation:**
- Be aware of `window.width` and `window.height` — these are in logical pixels, not physical pixels.
- On Windows, the actual framebuffer size may be larger due to DPI scaling (not directly exposed in pyglet 2.x public API).
- Test on high-DPI displays.

### 3. Multi-Monitor Setup

**Issue:** On multi-monitor setups, pyglet may not correctly detect all screens if they have different DPI scales or refresh rates.

**Mitigation:**
- Always enumerate screens using `Display.get_screens()` and validate before creating windows.
- Do not assume a single monitor.
- Test placement logic on actual multi-monitor hardware.

### 4. Private Attributes (_hwnd, _view_hwnd)

**Issue:** Accessing `window._hwnd` or `window._view_hwnd` is using private APIs that are not guaranteed to remain stable across pyglet versions.

**Mitigation:**
- Use only if absolutely necessary for Win32 interop.
- Document the dependency clearly (comment in code).
- Consider pinning pyglet to a specific version if relying on these attributes.

### 5. Display Probe at Import Time

**Issue:** `pyglet.app` may probe the display at import time, which can cause hangs or failures on systems with missing or broken display drivers.

**Mitigation:**
- Import pyglet as early as possible in your application (before other I/O).
- Ensure the target system has at least basic graphics support.

### 6. Window Handle Lifetime

**Issue:** The `_hwnd` handle is only valid while the window exists. Calling it after `.close()` will crash.

**Mitigation:**
- Keep a reference to the pyglet window as long as you need the HWND.
- Do not store the HWND and use it after the window is closed.

```python
# WRONG
window = pyglet.window.Window()
hwnd = window._hwnd
window.close()
win32gui.SetWindowPos(hwnd, ...)  # CRASH: hwnd is now invalid

# CORRECT
window = pyglet.window.Window()
hwnd = window._hwnd
win32gui.SetWindowPos(hwnd, ...)
window.close()  # Now safe to close
```

---

## Version Compatibility

### Tested on

- **pyglet 2.1.13, 2.1.14** (latest as of 2026-04-22)
- **Windows 10/11** (primary target)
- **Python 3.8+**

### API Stability

| API | Stability | Notes |
|-----|-----------|-------|
| `Window(...)` | Stable | Core windowing API, unlikely to change |
| `WINDOW_STYLE_*` constants | Stable | Public enum, unlikely to change |
| `WINDOW_STYLE_TRANSPARENT` | Stable but **platform-limited** | Windows-only; behavior varies by Windows version |
| `window._hwnd`, `window._view_hwnd` | **Unstable** | Private attributes; no guarantee of forward compatibility |
| `image.load()`, `get_region()` | Stable | Mature API |
| `Sprite`, `.color`, `.opacity` | Stable | Well-established API |
| `Label` | Stable | Mature text rendering API |
| `clock.schedule_interval()` | Stable | Core timing API |
| `app.run()` | Stable | Event loop is fundamental |
| `canvas.get_display()`, screen APIs | Stable | Public display API |

### Migration Path (2.x to 3.x)

pyglet 3.0 is in alpha (as of 2026-04-22). The sprite companion should target pyglet 2.x until 3.x is released and stabilized. When upgrading, key areas to validate:

- Window style constants may be renamed or reorganized
- Private attributes may be removed or changed
- Sprite/Label APIs are expected to remain stable

---

## Summary Snippets for Quick Reference

### Minimal Window + Sprite + Clock

```python
import pyglet

# Load image and create sprite
image = pyglet.image.load("character.png")
sprite = pyglet.sprite.Sprite(image, x=100, y=100)

# Create window
window = pyglet.window.Window(width=800, height=600)

# Schedule updates
def update(dt):
    sprite.x += 50 * dt  # Move 50 pixels/second

pyglet.clock.schedule_interval(update, 1/60)

# Draw
@window.event
def on_draw():
    window.clear()
    sprite.draw()

# Run
pyglet.app.run()
```

### Transparent Overlay Window

```python
import pyglet

window = pyglet.window.Window(
    width=400,
    height=400,
    style=pyglet.window.Window.WINDOW_STYLE_TRANSPARENT,
    caption="Overlay",
)

@window.event
def on_draw():
    window.clear()
    # Draw your overlay here

pyglet.app.run()
```

### Sprite Sheet Extraction

```python
import pyglet

sheet = pyglet.image.load("sprites.png")
frame_width, frame_height = 64, 64

frames = []
for row in range(sheet.height // frame_height):
    for col in range(sheet.width // frame_width):
        y = sheet.height - (row + 1) * frame_height
        x = col * frame_width
        frame = sheet.get_region(x=x, y=y, width=frame_width, height=frame_height)
        frames.append(frame)

print(f"Extracted {len(frames)} frames")
```

### Getting Win32 HWND for Interop

```python
import pyglet

window = pyglet.window.Window(width=400, height=400)
hwnd = window._hwnd

# Use hwnd with Win32 APIs
import ctypes
# ... ctypes.windll.user32.SetParent(hwnd, parent_hwnd) ...

pyglet.app.run()
```

---

## Cited from

- https://github.com/pyglet/pyglet — source repository, 2026-04-22
- https://pyglet.readthedocs.io/en/latest/modules/window.html — Window API reference
- https://pyglet.readthedocs.io/en/latest/modules/sprite.html — Sprite API reference
- https://pyglet.readthedocs.io/en/latest/modules/text/index.html — Text Label API reference
- https://pyglet.readthedocs.io/en/latest/modules/clock.html — Clock scheduling API
- https://pyglet.readthedocs.io/en/latest/modules/app.html — Event loop API
- https://github.com/pyglet/pyglet/issues/693 — Windows 11 transparent window issue
- https://pyglet.readthedocs.io/en/latest/programming_guide/windowing.html — Window style overview
- https://pyglet.readthedocs.io/en/latest/programming_guide/image.html — Image loading and sprite sheets
- https://pyglet.readthedocs.io/en/latest/programming_guide/text.html — Text rendering guide
- https://pyglet.readthedocs.io/en/latest/programming_guide/time.html — Clock and scheduling guide
