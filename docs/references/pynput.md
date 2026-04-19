# pynput Reference

> Cited from: https://pythonhosted.org/pynput/keyboard.html (2026-04-19)
> Cited from: https://pynput.readthedocs.io/en/latest/keyboard.html (2026-04-19)
> Cited from: https://pypi.org/project/pynput/ (2026-04-19)

---

## Overview

pynput is a Python library for controlling and monitoring input devices (mouse and
keyboard). Voice Commander uses `pynput.keyboard.Listener` to detect global hotkey
presses — specifically `scroll_lock` — to start and stop recording without needing
focus on a window.

## Installation

```bash
pip install pynput
```

No additional system dependencies on Windows. On Linux, requires X11 or Wayland.

## Minimal Working Example (Voice Commander)

```python
from pynput import keyboard
import threading

recording = threading.Event()

def on_press(key):
    """Called in the listener thread when any key is pressed."""
    if key == keyboard.Key.scroll_lock:
        if not recording.is_set():
            recording.set()
            print("Recording started")

def on_release(key):
    """Called in the listener thread when any key is released."""
    if key == keyboard.Key.scroll_lock:
        if recording.is_set():
            recording.clear()
            print("Recording stopped")

# Create and start listener (runs in a background thread)
listener = keyboard.Listener(
    on_press=on_press,
    on_release=on_release,
)
listener.start()

# ... rest of application ...

# Clean up
listener.stop()
listener.join()
```

---

## API Reference

### `keyboard.Listener`

```python
keyboard.Listener(
    on_press=None,
    on_release=None,
    suppress=False,
    **kwargs,      # platform-specific (e.g. win32_event_filter)
)
```

`keyboard.Listener` is a subclass of `threading.Thread`.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `on_press` | Callable[[key], None\|bool] | None | Callback invoked when a key is pressed. Receives one argument: the key |
| `on_release` | Callable[[key], None\|bool] | None | Callback invoked when a key is released. Receives one argument: the key |
| `suppress` | bool | False | If True, suppress the key events from reaching other applications (global key capture) |
| `win32_event_filter` | Callable | None | Windows-only; low-level event filter callback ⚠️ verify |

**Key argument type** passed to callbacks:
- `pynput.keyboard.Key` — for special keys (scroll_lock, ctrl, shift, etc.)
- `pynput.keyboard.KeyCode` — for regular character keys (alphanumeric, symbols)
- `None` — for unknown keys

---

### Listener Methods (inherited from `threading.Thread`)

| Method | Description |
|--------|-------------|
| `listener.start()` | Start the listener thread. Begins receiving events |
| `listener.stop()` | Signal the listener to stop |
| `listener.join(timeout=None)` | Wait for listener thread to finish |
| `listener.running` | bool — True if listener is active |

**Context manager usage:**
```python
with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
    listener.join()  # block until stopped
```
Equivalent to `start()` in try block + `stop()` in finally block.

---

### Stopping the Listener

Three ways to stop:

```python
# 1. From outside: call stop()
listener.stop()

# 2. From callback: return False
def on_press(key):
    if key == keyboard.Key.esc:
        return False  # stops the listener

# 3. From callback: raise StopException
def on_press(key):
    if key == keyboard.Key.esc:
        raise keyboard.Listener.StopException
```

**Important:** Once stopped, a listener instance cannot be restarted. Create a new
instance if you need to resume listening.

---

### `keyboard.Key` Enum

All special key constants available as `keyboard.Key.<name>`:

| Key Constant | Description |
|-------------|-------------|
| `Key.scroll_lock` | Scroll Lock key — **Voice Commander hotkey** |
| `Key.alt` | Alt (either) |
| `Key.alt_l` | Left Alt |
| `Key.alt_r` | Right Alt |
| `Key.alt_gr` | AltGr |
| `Key.backspace` | Backspace |
| `Key.caps_lock` | Caps Lock |
| `Key.cmd` | Windows key / Command |
| `Key.cmd_l` | Left Windows/Command |
| `Key.cmd_r` | Right Windows/Command |
| `Key.ctrl` | Control (either) |
| `Key.ctrl_l` | Left Control |
| `Key.ctrl_r` | Right Control |
| `Key.delete` | Delete |
| `Key.down` | Down arrow |
| `Key.end` | End |
| `Key.enter` | Enter/Return |
| `Key.esc` | Escape |
| `Key.f1` – `Key.f20` | Function keys F1–F20 |
| `Key.home` | Home |
| `Key.insert` | Insert |
| `Key.left` | Left arrow |
| `Key.menu` | Menu key |
| `Key.num_lock` | Num Lock |
| `Key.page_down` | Page Down |
| `Key.page_up` | Page Up |
| `Key.pause` | Pause/Break |
| `Key.print_screen` | Print Screen |
| `Key.right` | Right arrow |
| `Key.shift` | Shift (either) |
| `Key.shift_l` | Left Shift |
| `Key.shift_r` | Right Shift |
| `Key.space` | Space bar |
| `Key.tab` | Tab |
| `Key.up` | Up arrow |

**Comparing keys in callbacks:**
```python
def on_press(key):
    # Compare special keys
    if key == keyboard.Key.scroll_lock:
        ...
    
    # Compare character keys
    try:
        if key.char == 'a':
            ...
    except AttributeError:
        pass  # key is a special Key, not a KeyCode

    # Or use a KeyCode
    if key == keyboard.KeyCode.from_char('a'):
        ...
```

---

### `keyboard.KeyCode`

Represents a regular character key.

```python
keyboard.KeyCode.from_char('a')     # character key 'a'
keyboard.KeyCode.from_vk(65)        # virtual key code
keyboard.KeyCode.from_dead('~')     # dead key
```

Attributes:
- `keycode.char` — str or None — the character representation
- `keycode.vk` — int or None — the virtual key code

---

### Callback Threading Model

**Critical facts:**
- `keyboard.Listener` is a `threading.Thread` — runs in a background thread
- All `on_press` and `on_release` callbacks are invoked **from the listener thread**
- Never do blocking I/O or heavy computation in callbacks
- Use `threading.Event`, `queue.Queue`, or similar for cross-thread communication

```python
import queue
from pynput import keyboard

key_queue = queue.Queue()

def on_press(key):
    key_queue.put(("press", key))  # thread-safe

def on_release(key):
    key_queue.put(("release", key))  # thread-safe

listener = keyboard.Listener(on_press=on_press, on_release=on_release)
listener.start()

# Main thread processes events
while True:
    event, key = key_queue.get()
    if event == "press" and key == keyboard.Key.scroll_lock:
        start_recording()
```

---

### Keyboard Controller (for sending keystrokes)

Voice Commander doesn't use this (uses pyautogui instead), but for reference:

```python
from pynput.keyboard import Controller, Key

controller = keyboard.Controller()
controller.press('a')
controller.release('a')
controller.tap('a')           # press + release
controller.type('hello')      # type a string
controller.press(Key.ctrl)
controller.tap('c')
controller.release(Key.ctrl)
```

---

## Known Gotchas

1. **Callbacks run in listener thread** — any exception in a callback silently kills
   the listener. Wrap callback bodies in try/except.

2. **`suppress=True` captures globally** — this will prevent the key from reaching
   any other application. Only use if intentional.

3. **scroll_lock may be undefined** — on some platforms/keyboards, `Key.scroll_lock`
   may raise `AttributeError` or be `None`. Check `hasattr(keyboard.Key, 'scroll_lock')`.
   ⚠️ verify on target hardware

4. **Cannot restart a stopped listener** — `listener.stop()` then `listener.start()`
   raises a `RuntimeError`. Create a fresh `keyboard.Listener(...)` instance.

5. **Windows requires no special permissions** for global key monitoring (unlike macOS
   which requires Accessibility access).

6. **Key comparison** — `key == keyboard.Key.scroll_lock` works correctly. Do not
   compare with string; `Key` is an enum.

7. **Daemon thread** — the listener thread is a daemon thread, meaning it will be
   killed when the main thread exits. Call `listener.join()` if you need it to finish
   gracefully.

---

## Cited from

- https://pythonhosted.org/pynput/keyboard.html — fetched 2026-04-19
- https://pynput.readthedocs.io/en/latest/keyboard.html (via web search) — 2026-04-19
- https://pypi.org/project/pynput/ — fetched 2026-04-19
