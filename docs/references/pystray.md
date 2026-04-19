# pystray Reference

> Cited from: https://pystray.readthedocs.io/en/latest/usage.html (2026-04-19)
> Cited from: https://pystray.readthedocs.io/en/latest/reference.html (2026-04-19)
> Cited from: https://github.com/moses-palmer/pystray/blob/master/lib/pystray/_base.py (2026-04-19)
> Cited from: https://pypi.org/project/pystray/ (2026-04-19)
> Cited from: Web search results for "pystray Icon Menu MenuItem threading" (2026-04-19)

---

## Overview

pystray is a Python library for creating system tray icons with context menus on
Windows, macOS, and Linux (X11/GTK/AppIndicator/GNOME). Voice Commander uses it to
place a tray icon that shows current status (idle/recording/processing) and provides
a right-click menu for configuration and exit.

## Installation

```bash
pip install pystray
```

Requires `Pillow` (PIL) for icon image creation:
```bash
pip install pystray pillow
```

On Windows no additional system dependencies are required. The Windows backend is
the default when running on Windows.

## Minimal Working Example (Voice Commander)

```python
import pystray
from pystray import Icon, Menu, MenuItem
from PIL import Image, ImageDraw
import threading

def create_icon_image(color: str = "green", size: int = 64) -> Image.Image:
    """Create a simple colored circle icon."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse([2, 2, size - 2, size - 2], fill=color, outline="white")
    return image

def on_exit(icon, item):
    icon.stop()

def on_settings(icon, item):
    print("Open settings")

# Build menu
menu = Menu(
    MenuItem("Voice Commander", None, enabled=False),  # header label
    Menu.SEPARATOR,
    MenuItem("Settings", on_settings),
    MenuItem("Exit", on_exit),
)

# Create icon
icon = Icon(
    name="voice-commander",
    icon=create_icon_image("green"),
    title="Voice Commander — Idle",
    menu=menu,
)

# Run in background thread (Windows allows this)
def run_icon():
    icon.run()

icon_thread = threading.Thread(target=run_icon, daemon=True)
icon_thread.start()

# Update icon from main thread
def set_recording():
    icon.icon = create_icon_image("red")
    icon.title = "Voice Commander — Recording"

def set_idle():
    icon.icon = create_icon_image("green")
    icon.title = "Voice Commander — Idle"
```

---

## API Reference

### `pystray.Icon`

```python
pystray.Icon(
    name: str,
    icon=None,
    title=None,
    menu=None,
    **kwargs,  # platform-specific kwargs
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | str | — | Identifier for the tray icon. Used internally |
| `icon` | PIL.Image | None | The icon image. Must be set before calling `visible = True` |
| `title` | str | None | Tooltip text shown on hover |
| `menu` | pystray.Menu | None | Context menu shown on right-click |
| `**kwargs` | — | — | Platform-specific options (prefixed `win32_`, `darwin_`, etc.) |

---

#### `Icon.run(setup=None)`

```python
icon.run(setup: Callable[[Icon], None] = None)
```

Enters the event loop handling tray icon events. **This method is blocking.**

| Parameter | Type | Description |
|-----------|------|-------------|
| `setup` | Callable | Optional function run in a separate thread once the icon is ready. Use to start background tasks |

```python
def setup(icon):
    icon.visible = True
    # Start your application here
    start_main_app()

icon.run(setup=setup)
```

**Threading note:** On Windows, `run()` CAN be called from a non-main thread safely.
On macOS, it MUST be called from the main thread.

---

#### `Icon.run_detached(setup=None)`

```python
icon.run_detached(setup: Callable[[Icon], None] = None)
```

Prepares the icon for running detached from the main loop. Allows integration with
other frameworks that own the main event loop.

```python
icon.run_detached()
# ... integrate with another main loop or run other blocking code ...
icon.stop()
```

---

#### `Icon.stop()`

```python
icon.stop() -> None
```

Stop the event loop and remove the tray icon. Attempts to join the setup thread
with a timeout.

---

#### `Icon.update_menu()`

```python
icon.update_menu() -> None
```

Refresh the menu to reflect changes in dynamic menu item properties (e.g., checked
state, visibility, enabled state). Call after modifying menu item states.

---

#### `Icon.notify(message, title=None)`

```python
icon.notify(message: str, title: str = None) -> None
```

Display a system notification balloon from the tray icon.

| Parameter | Description |
|-----------|-------------|
| `message` | Notification body text |
| `title` | Notification title. Defaults to `icon.title` |

---

#### `Icon.remove_notification()`

```python
icon.remove_notification() -> None
```

Remove the currently displayed notification.

---

#### `Icon.visible` (property)

```python
icon.visible: bool  # read/write
```

Controls whether the tray icon is visible.

- Setting to `True` requires `icon.icon` to be set; raises `ValueError` otherwise
- Setting to `False` hides the icon from the tray

```python
icon.visible = True   # show
icon.visible = False  # hide
```

---

#### `Icon.icon` (property)

```python
icon.icon: PIL.Image  # read/write
```

The current icon image. Update dynamically to reflect state changes:
```python
icon.icon = create_icon_image("red")  # change to red when recording
```

#### `Icon.title` (property)

```python
icon.title: str  # read/write
```

Tooltip text shown when hovering over the tray icon.

---

### `pystray.Menu`

```python
pystray.Menu(*items)
```

A menu consisting of `MenuItem` instances and/or separators.

| Parameter | Type | Description |
|-----------|------|-------------|
| `*items` | MenuItem or SEPARATOR | Menu items. Can also be a callable returning an iterable |

**Special value:**
```python
Menu.SEPARATOR  # horizontal separator line between items
```

**Dynamic menu (callable):**
```python
def get_menu_items():
    yield MenuItem("Status: Recording" if recording else "Status: Idle", None, enabled=False)
    yield MenuItem("Exit", on_exit)

menu = Menu(get_menu_items)
```

---

### `pystray.MenuItem`

```python
pystray.MenuItem(
    text,
    action,
    checked=None,
    radio=False,
    default=False,
    visible=True,
    enabled=True,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `text` | str or Callable | — | Menu item label. Can be a callable returning str for dynamic labels |
| `action` | Callable or Menu | — | Called when item is activated. Pass `None` for non-clickable items. Pass a `Menu` for submenu |
| `checked` | bool or Callable | None | Checkbox state. `None` = no checkbox. Callable = dynamic state |
| `radio` | bool | False | If True, use radio button instead of checkbox |
| `default` | bool | False | If True, this is the default item (activated on left-click) |
| `visible` | bool or Callable | True | Item visibility. Callable = dynamic visibility |
| `enabled` | bool or Callable | True | Item enabled state. Callable = dynamic enabled state |

**Callback signature for action:**
```python
def action(icon: pystray.Icon, item: pystray.MenuItem) -> None:
    ...
```

**Examples:**
```python
# Simple item
MenuItem("Exit", lambda icon, item: icon.stop())

# Checked item (checkbox)
MenuItem("Enable sounds", on_toggle_sounds, checked=lambda item: sounds_enabled)

# Radio group ⚠️ verify exact API
MenuItem("Small", on_size_small, checked=lambda item: size == "small", radio=True)
MenuItem("Large", on_size_large, checked=lambda item: size == "large", radio=True)

# Submenu
MenuItem("Options", Menu(
    MenuItem("Setting A", on_setting_a),
    MenuItem("Setting B", on_setting_b),
))

# Disabled header/label
MenuItem("Voice Commander v1.0", None, enabled=False)
```

---

## Threading Model

### Windows

On Windows, the pystray event loop can run in **any thread** (unlike macOS which
requires the main thread). Recommended patterns:

**Pattern 1: Background daemon thread (simplest)**
```python
import threading

icon = Icon("my-app", icon=img, title="App", menu=menu)
t = threading.Thread(target=icon.run, daemon=True)
t.start()

# Main thread continues; icon runs in background
# Call icon.stop() to cleanly exit
```

**Pattern 2: run_detached (for frameworks with their own loop)**
```python
icon.run_detached()
# Your framework's main loop here
icon.stop()  # cleanup when done
```

**Pattern 3: setup callback (icon starts, then runs your app)**
```python
def setup(icon):
    icon.visible = True
    your_main_application_loop()  # blocks
    icon.stop()

icon.run(setup=setup)  # blocks until setup finishes and stop() is called
```

---

## Windows Tray Icon Size

Windows tray icons are rendered at:
- **16×16** pixels — standard taskbar tray (small screens)
- **32×32** pixels — high-DPI displays (125%+ scaling)
- **64×64** pixels — recommended source size; Windows scales down

Windows will scale the image automatically. Provide a square image.

Recommended approach with Pillow:
```python
from PIL import Image, ImageDraw

def make_icon(size=64, color="green") -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = 4
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=color,
        outline=(255, 255, 255, 200),
        width=2,
    )
    return img
```

For loading from file:
```python
icon_img = Image.open("assets/icon.png").convert("RGBA")
icon = Icon("app", icon=icon_img)
```

---

## Known Gotchas

1. **macOS requires main thread** — `icon.run()` must be called from the main thread
   on macOS. On Windows it can be called from any thread.

2. **`visible = True` before `icon` is set raises `ValueError`** — always set
   `icon.icon = image` before setting `icon.visible = True`.

3. **Daemon thread exits with process** — if using `daemon=True`, the icon is removed
   abruptly. Call `icon.stop()` first for clean teardown.

4. **Menu callbacks run in the tray thread** — don't do heavy work in menu callbacks.
   Use `threading.Thread` or `queue.Queue` to delegate.

5. **Dynamic menu requires `update_menu()`** — if you change a callable's return value,
   you must call `icon.update_menu()` to force a refresh.

6. **Icon image must stay referenced** — if the PIL Image object is garbage-collected,
   the icon may disappear or crash.

7. **Left-click default action** — set `default=True` on a `MenuItem` to make it
   the action triggered on left-click (not just right-click).

8. **`notify()` support varies** — balloon notifications via `icon.notify()` work on
   Windows but behaviour differs on macOS and Linux.

---

## Cited from

- https://pystray.readthedocs.io/en/latest/usage.html (via web search) — 2026-04-19
- https://pystray.readthedocs.io/en/latest/reference.html (via web search) — 2026-04-19
- https://github.com/moses-palmer/pystray/blob/master/lib/pystray/_base.py — fetched 2026-04-19
- https://pypi.org/project/pystray/ — 2026-04-19
