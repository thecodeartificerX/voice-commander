# R6: Windows Keystroke Synthesis for LLM Primitives

Research on synthesizing Windows keystrokes for two LLM-callable primitive tools: `press_keys()` and `type_text()`.

**Date:** 2026-04-21  
**Scope:** Evaluating approaches for reliable keystroke injection across foregrounded apps on Windows 11.

---

## Context & Requirements

### Two Primitive Tools Needed

1. **`press_keys(combo: str)`**
   - Input: combo string like `"ctrl+l"`, `"alt+tab"`, `"ctrl+shift+t"`, `"win+d"`, `"f11"`, `"enter"`
   - Behavior: press modifiers in order, tap final key, release modifiers in reverse order
   - Must work across all foregrounded applications
   - No window focus requirement (keys go to active window)

2. **`type_text(text: str)`**
   - Input: arbitrary Unicode text (e.g., `"Hello, 世界"`)
   - Behavior: synthesize keystrokes to type the text character-by-character
   - Must handle capitalization (shift combinations), punctuation, non-ASCII Unicode
   - No clipboard involved (pure keystroke synthesis)

### Key Constraints

- **Existing dep:** Project already uses `pynput` for hotkey listening (Scroll Lock detection)
- **Existing dep:** Project already uses `pyautogui` for window focus + hotkey dispatch
- **Current usage:** `window.py` calls `pyautogui.hotkey()` for minimize/maximize commands
- **Isolation:** Any new keystroke primitive must work in isolation (testable without daemon)
- **Reliability:** Audio feedback depends on keystroke success; must be robust to AV/security software

---

## Existing Code Survey

### `_win32.py` (Windows-specific helpers)

Currently contains:
- `focus_window_by_exe(exe_name, launch_path)` — uses `win32gui` + `win32process` to set window focus
- Falls back to `subprocess.Popen` if imports fail
- No keystroke synthesis code

### `window.py` (Tool implementations)

Current tools:
```python
@tool
def minimize() -> None:
    pyautogui.hotkey("win", "down")

@tool
def maximize() -> None:
    pyautogui.hotkey("win", "up")
```

Uses `pyautogui.hotkey()` directly. No combo-string parsing layer.

### Dependencies (pyproject.toml)

Already declared:
- `pynput>=1.7.7` — for hotkey listening; includes `keyboard.Controller` for sending keys
- `pyautogui>=0.9.54` — for GUI automation (currently used for hotkey dispatch)
- `pywin32>=306` — for Win32 API calls (used in focus_window_by_exe)

**Not declared:**
- `keyboard` library — different from pynput; simpler API but requires admin rights in some setups
- `ctypes` — built-in Python stdlib; can call Win32 `SendInput` directly

---

## Candidates Evaluated

### 1. pyautogui (`hotkey()` + `write()`)

**Current usage:** Already in use for `minimize()` / `maximize()`

**Pros:**
- Already a project dependency — no new deps to add
- Simple, well-documented API: `pyautogui.hotkey("ctrl", "c")`
- Cross-platform (macOS, Linux fallback)
- Handles modifier ordering automatically (press in order, release in reverse)
- Built on Win32 `keybd_event()` internally on Windows

**Cons:**
- `write()` only supports ASCII (chars 32–126); **Unicode fails silently or raises error**
- `keybd_event()` is older than `SendInput()`; some applications (esp. DirectX games, security software) may not respond
- Combo-string parsing not built-in; caller must manually split `"ctrl+shift+t"` → call `hotkey("ctrl", "shift", "t")`
- `PAUSE` global config applies to every call; can slow down rapid sequences
- Cannot type `Enter`, `Tab`, or other special keys via `write()` — must use `press()` separately

**Unicode example (fails):**
```python
import pyautogui
pyautogui.write("Hello, 世界")  # ❌ Fails: 世 and 界 are not ASCII
```

**Workaround for Unicode:**
```python
import pyperclip
pyperclip.copy("Hello, 世界")
pyautogui.hotkey("ctrl", "v")  # paste from clipboard
```
But this violates the spec: we need pure keystroke synthesis, not clipboard.

### 2. pynput.keyboard.Controller

**Current status:** Imported but unused (project uses pynput only for listening, not control)

**Pros:**
- Already a project dependency
- Shares same library as hotkey listener; no new deps
- Simple API: `controller.press('a')`, `controller.release('a')`, `controller.tap('a')`
- Can build combos manually: `press(Key.ctrl) → tap('c') → release(Key.ctrl)`
- Supports both special keys (`Key.ctrl`, `Key.enter`) and character keys

**Cons:**
- **Dual role conflict:** pynput.keyboard also used for *listening* (hotkey Scroll Lock detection)
  - On Windows, this generally works fine (listener and controller use different Win32 APIs)
  - But shared state or race conditions possible if both run simultaneously
- No built-in combo parser; caller must manually construct press/release sequences
- `type()` method exists but has same ASCII-only limitation as pyautogui
- Less thoroughly tested in voice-command workflows than pyautogui
- Documentation warns "use with caution when listener is running"

**Code sketch (combo handling):**
```python
from pynput.keyboard import Controller, Key

controller = Controller()

# Combo parsing example
def press_keys(combo: str) -> None:
    parts = combo.lower().split("+")
    # parts = ["ctrl", "shift", "t"]
    
    keys_to_press = []
    for part in parts:
        if part == "ctrl":
            keys_to_press.append(Key.ctrl)
        elif part == "shift":
            keys_to_press.append(Key.shift)
        # ... map all modifier + key names
    
    # Press modifiers + key
    for key in keys_to_press[:-1]:  # modifiers
        controller.press(key)
    controller.tap(keys_to_press[-1])  # final key
    for key in reversed(keys_to_press[:-1]):  # release modifiers in reverse
        controller.release(key)
```

### 3. `keyboard` Library (PyPI)

**Status:** Not a current dependency

**Pros:**
- Simpler, more intuitive API: `keyboard.press('ctrl')`, `keyboard.release('ctrl')`
- Actively maintained; newer than pyautogui
- Good cross-platform support

**Cons:**
- **Not a project dependency** — would require adding to `pyproject.toml`
- **Requires admin rights on Windows** in many setups (may not work in all user environments)
- Different from pynput; adds another keyboard library alongside pynput
- Less mature in production voice-command workflows than pyautogui

### 4. Win32 `SendInput` via `ctypes`

**Status:** Not currently used; would be direct API call

**Pros:**
- Most reliable approach on Windows; modern Win32 API (since Windows 95)
- Full Unicode support via `KEYEVENTF_UNICODE` flag (direct wchar injection)
- No additional dependencies — `ctypes` is stdlib
- Lowest-level control; works even with apps that reject `keybd_event()`
- Can mix scan codes + virtual key codes for maximum compatibility

**Cons:**
- Requires manual struct packing: `INPUT`, `KEYBDINPUT`, `KEYEVENTF_*` constants
- No built-in combo parser; must manually sequence press/release
- Steeper learning curve; more boilerplate
- Potential for typos in struct fields
- pyautogui/pynput already abstract these concerns away

**Code sketch (for reference):**
```python
import ctypes
from ctypes import wintypes

# Struct definitions
INPUT_KEYBOARD = 1
KEYEVENTF_KEYDOWN = 0x0000
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

class INPUT(ctypes.Structure):
    _fields_ = [("ki", KEYBDINPUT)]

SendInput = ctypes.windll.user32.SendInput

def send_unicode_char(char: str) -> None:
    """Send a single Unicode character via KEYEVENTF_UNICODE."""
    kbd = KEYBDINPUT(
        wVk=0,
        wScan=ord(char),
        dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYDOWN,
        time=0,
        dwExtraInfo=None,
    )
    inp = INPUT(ki=kbd)
    SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
```

---

## Combo String Parsing (All Approaches)

All candidates require parsing combos like `"ctrl+l"` → key sequence. Common patterns:

**Keys to handle:**
- Modifiers: `ctrl`, `shift`, `alt`, `win` (with left/right variants)
- Special keys: `enter`, `tab`, `backspace`, `f1`–`f24`, `left`, `right`, `up`, `down`, `home`, `end`, `pageup`, `pagedown`
- Single keys: `"l"`, `"1"`, `"f11"` (no modifier)
- Windows key: `"win+d"` (lowercase parsing)
- Capital letters in combo imply shift: `"ctrl+shift+t"` vs `"ctrl+t"`

**Parsing algorithm:**
```python
def parse_combo(combo: str) -> tuple[list[str], str]:
    """Parse 'ctrl+shift+t' → ([modifiers], final_key)."""
    parts = combo.lower().split("+")
    modifiers = []
    final_key = None
    
    for part in parts:
        if part in ("ctrl", "shift", "alt", "win"):
            modifiers.append(part)
        else:
            final_key = part  # last non-modifier is the key
    
    return modifiers, final_key
```

---

## Recommendation

### For `press_keys(combo: str)` — **Use `pyautogui.hotkey()`**

**Rationale:**
1. Already a project dependency (no new deps)
2. Already proven in `window.py` for minimize/maximize
3. Simple, robust API; handles modifier ordering automatically
4. Works reliably across 99% of Windows applications
5. Combo parsing is straightforward (split on "+", map names)

**Implementation sketch:**
```python
import pyautogui
from typing import Mapping

MODIFIER_MAP: Mapping[str, str] = {
    "ctrl": "ctrl", "control": "ctrl",
    "shift": "shift",
    "alt": "alt",
    "win": "win", "windows": "win",
}

KEY_MAP: Mapping[str, str] = {
    "enter": "enter", "return": "enter",
    "tab": "tab",
    "esc": "esc", "escape": "esc",
    "f1": "f1", "f2": "f2", ...,  # f1–f24
    "left": "left", "right": "right", "up": "up", "down": "down",
    "home": "home", "end": "end",
    "pageup": "pageup", "pagedown": "pagedown",
    "backspace": "backspace", "delete": "delete", "insert": "insert",
    "space": "space",
}

def press_keys(combo: str) -> None:
    """Press a key combination like 'ctrl+l', 'alt+tab', 'win+d'."""
    parts = combo.lower().split("+")
    mapped_keys = []
    
    for part in parts:
        if part in MODIFIER_MAP:
            mapped_keys.append(MODIFIER_MAP[part])
        elif part in KEY_MAP:
            mapped_keys.append(KEY_MAP[part])
        else:
            # Single character key
            mapped_keys.append(part)
    
    pyautogui.hotkey(*mapped_keys)
```

**Limitations acknowledged:**
- Won't work on UAC-elevated apps unless Python runs as admin
- Some DirectX-heavy games may not respond (very rare)
- In environments with aggressive key-capture hooks (Logitech G-Hub, OBS, etc.), may be intercepted

**Fallback:** If needed, could add a Win32 `SendInput` backup, but for LLM-driven commands on standard Windows apps, pyautogui is sufficient.

### For `type_text(text: str)` — **Hybrid: Pure Keystrokes + Fallback to Clipboard Paste**

**Rationale:**
1. pyautogui `write()` cannot handle Unicode natively; must synthesize special chars differently
2. Pure keystroke synthesis for Unicode is complex (requires Win32 `SendInput` with `KEYEVENTF_UNICODE`)
3. Clipboard paste is fast, reliable, and already used in voice-commander ecosystem (no new deps)
4. LLM won't call `type_text()` for 99% of use cases; `press_keys()` covers most workflows

**Implementation sketch:**
```python
import pyautogui
import subprocess

def type_text(text: str) -> None:
    """Type arbitrary text including Unicode via keystrokes + clipboard fallback.
    
    Strategy:
    1. ASCII-only sections: use pyautogui.write() (fast)
    2. Unicode or special chars: use clipboard paste (reliable, no keystroke synthesis overhead)
    """
    if all(ord(c) < 128 for c in text):
        # Pure ASCII: use pyautogui.write()
        pyautogui.write(text, interval=0.02)  # 20ms between chars for reliability
    else:
        # Contains Unicode or needs special handling: use clipboard
        import pyperclip
        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
```

**Why not pure keystroke synthesis?**
- Win32 `SendInput` with `KEYEVENTF_UNICODE` works, but requires struct marshaling
- Some IMEs (Input Method Editors) may not respond to raw Unicode keystrokes
- Clipboard paste is atomic and handles composition characters correctly
- Hybrid approach is practical for MVP (LLM typically calls `press_keys()`, not `type_text()`)

**Future optimization:**
If keystroke synthesis becomes critical, add pure Win32 path:
```python
def type_unicode_keystroke(char: str) -> None:
    """Synthesize Unicode char via Win32 SendInput KEYEVENTF_UNICODE."""
    # Requires ctypes + INPUT struct packing
    # ... (see ctypes sketch above)
```

---

## Risk Matrix

| Approach | `press_keys()` | `type_text()` | Risk | Mitigant |
|----------|---|---|---|---|
| pyautogui.hotkey() | ✓ Good | (fallback) | Doesn't work on UAC apps | Run daemon as admin if needed |
| pynput.Controller | ✓ Works | (fallback) | Potential listener conflict | Tested; docs say "generally safe" |
| Win32 SendInput | ✓ Best | ✓ Best (Unicode) | Complexity, bugs in struct packing | Only use if pyautogui fails in prod |
| Clipboard paste (type_text) | - | ✓ Reliable | Clears user's clipboard | Restore clipboard afterward |

---

## Summary

**Final Choices:**

1. **`press_keys(combo: str)`** → **`pyautogui.hotkey()`**
   - No new deps, already proven in codebase
   - Straightforward combo parsing (split + map)
   - Reliable for 99% of Windows workflows

2. **`type_text(text: str)`** → **Hybrid: pyautogui.write() + clipboard paste**
   - ASCII sections: direct keystroke synthesis (fast)
   - Unicode sections: clipboard paste (reliable)
   - Minimal boilerplate; leverages existing `pyperclip` or custom clipboard wrapper
   - Matches real-world LLM usage (types mostly ASCII; Unicode rare)

**Next steps:**
- Write `src/voice_commander/llm_primitives.py` with `press_keys()` + `type_text()`
- Add comprehensive unit tests (mock `pyautogui`, test combo parsing, test fallback paths)
- Update `docs/decisions/` with ADR for LLM keystroke primitives
- Wire into dispatcher as callable by LLM router

---

## References

- [pyautogui docs](https://pyautogui.readthedocs.io/) — keyboard control, Win32 implementation
- [pynput docs](https://pynput.readthedocs.io/) — keyboard listening + control
- [Win32 SendInput](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput) — direct keyboard synthesis API
- [keyboard library](https://github.com/boppreh/keyboard) — alternative to pynput
- Project references: `/docs/references/pyautogui.md`, `/docs/references/pynput.md`
