# pyautogui Reference

> Cited from: https://pyautogui.readthedocs.io/en/latest/keyboard.html (2026-04-19)
> Cited from: https://pyautogui.readthedocs.io/en/latest/ (2026-04-19)
> Cited from: https://github.com/asweigart/pyautogui/blob/master/pyautogui/__init__.py (2026-04-19)
> Cited from: https://github.com/asweigart/pyautogui/blob/master/pyautogui/_pyautogui_win.py (2026-04-19)

---

## Overview

PyAutoGUI is a cross-platform GUI automation library that allows Python scripts to
programmatically control the mouse and keyboard. Voice Commander uses it to dispatch
keyboard shortcuts corresponding to matched voice commands — e.g., sending `Ctrl+C`,
`Alt+Tab`, or typing text.

## Installation

```bash
pip install pyautogui
```

Dependencies: `pygetwindow`, `pymsgbox`, `pytweening`, `pyscreeze`, `mouseinfo`.
On Windows, also requires `pywin32`.

## Minimal Working Example (Voice Commander)

```python
import pyautogui
import time

# Safety settings (configure at startup)
pyautogui.FAILSAFE = True   # move mouse to (0,0) to abort
pyautogui.PAUSE = 0.05      # 50ms pause after each call (default 0.1)

# Send keyboard shortcut
pyautogui.hotkey("ctrl", "c")         # copy
pyautogui.hotkey("alt", "tab")        # switch window
pyautogui.hotkey("ctrl", "shift", "t")  # reopen closed tab

# Hold a key down then release it
pyautogui.keyDown("ctrl")
pyautogui.keyDown("c")
time.sleep(0.05)
pyautogui.keyUp("c")
pyautogui.keyUp("ctrl")

# Type text
pyautogui.write("Hello, world!", interval=0.02)

# Press a single key
pyautogui.press("enter")
pyautogui.press(["left", "left", "left"])  # multiple presses
```

---

## API Reference

### `pyautogui.hotkey(*keys, interval=0.0)`

```python
pyautogui.hotkey(*keys, interval=0.0)
```

Simulates a keyboard shortcut by pressing all specified keys in order, then releasing
them in reverse order.

| Parameter | Type | Description |
|-----------|------|-------------|
| `*keys` | str | One or more key name strings to press simultaneously |
| `interval` | float | Seconds to wait between each key press/release. Default 0.0 |

```python
pyautogui.hotkey("ctrl", "c")               # Ctrl+C
pyautogui.hotkey("ctrl", "alt", "delete")   # Ctrl+Alt+Delete
pyautogui.hotkey("win", "d")               # Show desktop
pyautogui.hotkey("alt", "f4")              # Close window
```

Execution order: keys are pressed `ctrl → alt → delete` then released `delete → alt → ctrl`.

---

### `pyautogui.keyDown(key)`

```python
pyautogui.keyDown(key: str)
```

Simulates pressing a key down without releasing it. Keeps the key in a held state.

| Parameter | Type | Description |
|-----------|------|-------------|
| `key` | str | Key name from `KEYBOARD_KEYS` |

```python
pyautogui.keyDown("shift")
pyautogui.press("a")    # types "A"
pyautogui.keyUp("shift")
```

---

### `pyautogui.keyUp(key)`

```python
pyautogui.keyUp(key: str)
```

Simulates releasing a key. Must be called after `keyDown` to complete the press cycle.

| Parameter | Type | Description |
|-----------|------|-------------|
| `key` | str | Key name from `KEYBOARD_KEYS` |

---

### `pyautogui.press(keys, presses=1, interval=0.0)`

```python
pyautogui.press(keys, presses=1, interval=0.0)
```

Press and release one or more keys.

| Parameter | Type | Description |
|-----------|------|-------------|
| `keys` | str or list[str] | Single key or list of keys to press sequentially |
| `presses` | int | Number of times to repeat. Default 1 |
| `interval` | float | Seconds between presses. Default 0.0 |

```python
pyautogui.press("enter")
pyautogui.press(["left", "left", "up"], interval=0.05)
pyautogui.press("f5", presses=3)
```

---

### `pyautogui.write(message, interval=0.0)` (alias: `typewrite`)

```python
pyautogui.write(message: str, interval: float = 0.0)
```

Types a string of characters by simulating individual key presses.

| Parameter | Type | Description |
|-----------|------|-------------|
| `message` | str | String to type. Only printable ASCII characters supported |
| `interval` | float | Seconds between each character. Default 0.0 |

**Limitation:** Cannot type special keys (F-keys, arrows, etc.) — use `press()` instead.

```python
pyautogui.write("hello world")
pyautogui.write("search query", interval=0.05)  # slower, more reliable
```

**Unicode caveat:** Only ASCII characters are supported via `write()`. For Unicode,
use clipboard paste pattern:
```python
import subprocess
# Windows clipboard paste approach
import pyperclip
pyperclip.copy("Unicode text: 中文")
pyautogui.hotkey("ctrl", "v")
```

---

### `pyautogui.hold(*keys)`

Context manager to hold keys during a block:

```python
with pyautogui.hold("shift"):
    pyautogui.press(["a", "b", "c"])  # types "ABC"

with pyautogui.hold("ctrl"):
    pyautogui.press("c")              # Ctrl+C
```

---

## KEYBOARD_KEYS (Complete Key Name List)

Valid key names for `hotkey()`, `keyDown()`, `keyUp()`, `press()`:

**Special characters:** `\t`, `\n`, `\r`, ` ` (space)

**Printable ASCII:** All characters `! " # $ % & ' ( ) * + , - . / 0-9 : ; < = > ? @ A-Z [ \ ] ^ _ \` a-z { | } ~`

**Function keys:** `f1`, `f2`, ..., `f24`

**Navigation:**
```
home, end, pageup, pagedown,
left, right, up, down
```

**Editing:**
```
backspace, delete, insert,
tab, enter, return (alias for enter),
esc, escape (alias for esc),
space
```

**Modifier keys:**
```
shift, shiftleft, shiftright,
ctrl, ctrlleft, ctrlright,
alt, altleft, altright,
win, winleft, winright,
command (macOS alias for win)
```

**Lock keys:**
```
capslock, numlock, scrolllock
```

**Numpad:**
```
num0, num1, ..., num9,
add, subtract, multiply, divide, decimal,
numpad0, numpad1, ..., numpad9 (⚠️ verify these aliases)
```

**Special:**
```
printscreen, pause, break,
volumemute, volumedown, volumeup,
browserback, browserfavorites, browserforward,
browserhome, browserrefresh, browsersearch, browserstop,
launchapp1, launchapp2, launchmail, launchmediacenter,
mediaprevioustrack, medianexttrack, mediaplaypause, mediastop
```

Full list available at runtime:
```python
import pyautogui
print(pyautogui.KEYBOARD_KEYS)  # list of all valid key strings
```

---

## Global Configuration Variables

### `pyautogui.FAILSAFE`

```python
pyautogui.FAILSAFE = True  # default
```

When `True`, moving the mouse cursor to the top-left corner `(0, 0)` raises
`pyautogui.FailSafeException`, aborting the automation. **Always keep enabled
during development.**

```python
try:
    pyautogui.hotkey("ctrl", "alt", "delete")
except pyautogui.FailSafeException:
    print("Fail-safe triggered — user moved mouse to corner")
```

```python
pyautogui.FAILSAFE_POINTS = [(0, 0)]  # default, corners that trigger fail-safe
```

### `pyautogui.PAUSE`

```python
pyautogui.PAUSE = 0.1  # default: 0.1 seconds
```

Pause duration inserted after **every** public pyautogui function call.
Set to `0` or a small value for fast automation:

```python
pyautogui.PAUSE = 0.0   # no pause between calls (fastest)
pyautogui.PAUSE = 0.05  # 50ms (Voice Commander recommended)
```

---

## Platform Differences

| Feature | Windows | macOS | Linux |
|---------|---------|-------|-------|
| `hotkey()` | Win32 API via ctypes | Quartz | xdotool/Xtst |
| Unicode typing | Limited (clipboard recommended) | Full | Full |
| Elevated app control | Limited (UAC blocks) | Requires permissions | X11 env |
| `keyDown` persistence | Full | Full | Full |

**Windows-specific notes:**
- Uses `ctypes.windll.user32.keybd_event()` with virtual key codes
- `VkKeyScanA()` maps ASCII chars (32-127) to virtual key codes automatically
- Modifier key handling: bits extracted with `divmod()` to send modifier+key pairs
- Cannot control elevated applications (UAC) without running Python as Administrator

---

## Voice Commander Usage Patterns

```python
import pyautogui

pyautogui.PAUSE = 0.05  # small pause is reliable on most systems

# Command dispatcher pattern
COMMAND_ACTIONS = {
    "copy": lambda: pyautogui.hotkey("ctrl", "c"),
    "paste": lambda: pyautogui.hotkey("ctrl", "v"),
    "cut": lambda: pyautogui.hotkey("ctrl", "x"),
    "undo": lambda: pyautogui.hotkey("ctrl", "z"),
    "redo": lambda: pyautogui.hotkey("ctrl", "y"),
    "select all": lambda: pyautogui.hotkey("ctrl", "a"),
    "save": lambda: pyautogui.hotkey("ctrl", "s"),
    "close window": lambda: pyautogui.hotkey("alt", "f4"),
    "switch window": lambda: pyautogui.hotkey("alt", "tab"),
    "minimize": lambda: pyautogui.hotkey("win", "down"),
    "maximize": lambda: pyautogui.hotkey("win", "up"),
    "screenshot": lambda: pyautogui.hotkey("win", "shift", "s"),
    "volume up": lambda: pyautogui.press("volumeup"),
    "volume down": lambda: pyautogui.press("volumedown"),
    "mute": lambda: pyautogui.press("volumemute"),
}

def execute_command(command_name: str) -> bool:
    action = COMMAND_ACTIONS.get(command_name)
    if action:
        action()
        return True
    return False
```

---

## Known Gotchas

1. **`PAUSE` applies to every call** — even in production, leave `PAUSE = 0.05` as
   zero-pause can cause missed keystrokes on slower systems.

2. **`write()` only handles ASCII** — passing unicode to `write()` silently skips
   unsupported characters or raises an error.

3. **`hotkey()` with 3+ keys** — some key combinations may not work reliably if the
   system has hotkey interceptors (e.g., Logitech G-Hub, OBS).

4. **UAC-elevated windows** — pyautogui cannot send keys to processes running with
   higher privileges unless Python itself is elevated.

5. **`keyDown` without `keyUp`** — leaving a key held (e.g., after a crash) will keep
   the key pressed until manually released or the process ends.

6. **`FAILSAFE = False`** — never disable in development; you lose the emergency abort.

7. **Virtual key vs scan code** — `keybd_event()` is used on Windows, not
   `SendInput()`. Some applications may not respond to `keybd_event()`. ⚠️ verify
   for DirectX/games.

---

## Cited from

- https://pyautogui.readthedocs.io/en/latest/keyboard.html — fetched 2026-04-19
- https://pyautogui.readthedocs.io/en/latest/ — fetched 2026-04-19
- https://github.com/asweigart/pyautogui/blob/master/pyautogui/__init__.py — fetched 2026-04-19
- https://github.com/asweigart/pyautogui/blob/master/pyautogui/_pyautogui_win.py — fetched 2026-04-19
- https://deepwiki.com/asweigart/pyautogui/3.2-keyboard-control — fetched 2026-04-19
