# R7: Window Focus Primitives on Windows

## Overview

Window focus is a foundational primitive for voice-driven command routing: "focus browser", "focus terminal", or general "focus <app>". Given a substring of a window title (case-insensitive partial match), we must locate the window, restore it if minimized, bring it to foreground, and assign keyboard focus.

## Existing Implementation in Voice Commander

The project already has `focus_window_by_exe()` in `src/voice_commander/tools/_win32.py`:

- Uses `pywin32` (`win32gui`, `win32con`, `win32process`).
- Iterates top-level windows via `EnumWindows()`.
- Filters by process PID (does not do title-based matching).
- Calls `ShowWindow(hwnd, SW_RESTORE)` then `SetForegroundWindow(hwnd)`.
- Falls back to launching the app via `subprocess.Popen` if not found or if `pywin32`/`psutil` are unavailable.

The goal now is to **extend this** to support title-based substring matching while maintaining reliability on Windows 11.

## Research: Window Focus Approaches

### 1. PyGetWindow (`pygetwindow.getWindowsWithTitle()` + `.activate()`)

**Pros:**
- Minimal API surface: `getWindowsWithTitle('substring')` returns matching window objects.
- Cross-platform intent (though Windows-only in practice).

**Cons:**
- **Maintenance risk:** Inactive project; no updates in 12+ months.
- Internally uses `ctypes` wrapping of Win32 APIs without thread-attachment logic.
- Unicode title support depends on underlying Win32 call; no special handling documented.
- `.activate()` may fail silently if focus-stealing rules block the call (no exception).
- Frequently requires `pywinauto` to be imported first as a workaround for activation to succeed.

**Verdict:** Not recommended for production. Maintenance and reliability gaps make it unsuitable.

### 2. PyWinCtl (Modern PyGetWindow Successor)

**Overview:** Newer fork adding Linux/X11, macOS support, and multi-monitor features.

**Status:** Better maintained than PyGetWindow, but still not a primary Win32 library.

**Verdict:** Better than PyGetWindow if a high-level API is desired, but introduces a new dependency without clear wins over direct Win32 or pywinauto.

### 3. PyWin32 Direct (`win32gui` + `win32con`)

**Approach:**
```python
import win32gui
import win32con

def find_windows_by_title_substring(substring: str) -> list[int]:
    hwnds = []
    def enum_handler(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if substring.lower() in title.lower():
                hwnds.append(hwnd)
        return True
    win32gui.EnumWindows(enum_handler, None)
    return hwnds

def focus_window(hwnd: int) -> bool:
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False
```

**Pros:**
- Direct access to Win32 APIs; no abstraction overhead.
- `EnumWindows()` iterates all top-level windows; callback can apply custom matching logic.
- Case-insensitive substring matching is trivial.
- `pywin32` already in `pyproject.toml` (line 19: `pywin32>=306`).
- `ShowWindow(SW_RESTORE)` handles minimized windows.

**Cons:**
- `SetForegroundWindow()` is **unreliable on modern Windows** due to focus-stealing protection:
  - Requires the calling process to be foreground OR to have received the most recent input.
  - Will silently fail if system denies the call.
  - Workaround: `AttachThreadInput()` to attach to the foreground window's thread, call `SetForegroundWindow()`, then detach.
  - Even the workaround may fail on some Windows configurations.

**The AttachThreadInput Idiom:**
```python
import win32api
import win32con

foreground_hwnd = win32gui.GetForegroundWindow()
foreground_tid, _ = win32process.GetWindowThreadProcessId(foreground_hwnd)
target_tid, _ = win32process.GetWindowThreadProcessId(hwnd)

if foreground_tid != target_tid:
    win32api.AttachThreadInput(foreground_tid, target_tid, True)
    try:
        win32gui.SetForegroundWindow(hwnd)
    finally:
        win32api.AttachThreadInput(foreground_tid, target_tid, False)
else:
    win32gui.SetForegroundWindow(hwnd)
```

**Verdict:** Solid foundation; already used in the codebase. `SetForegroundWindow()` alone may fail; thread-attachment idiom mitigates but adds complexity.

### 4. PyWinAuto (`Application().window()` + `.set_focus()`)

**Approach:**
```python
from pywinauto import Application
app = Application().connect(title_re='.*Chrome.*')
app.top_window().set_focus()
```

**Pros:**
- High-level API designed for UI automation.
- `set_focus()` method **internally handles `AttachThreadInput()` logic automatically**.
- Regex-based title matching.
- More robust than raw `SetForegroundWindow()` because it manages thread attachment.
- Brings window to foreground, restores if minimized, and assigns keyboard focus in one call.

**Cons:**
- Heavy dependency (100+ MB unpacked).
- Slower than direct Win32 calls (initialization overhead ~1-2 seconds on first import).
- Overkill for a simple substring-match + focus task.
- Adds cognitive load if most of the codebase uses direct Win32.

**Verdict:** More reliable than bare `SetForegroundWindow()` but introduces size/performance cost for a single operation.

### 5. UIAutomation Pattern

**Overview:** Microsoft's UIAutomation framework (lower-level than pywinauto) provides `IUIAutomationElement.SetFocus()`.

**Pros:**
- Can focus on specific UI elements within a window (not just the window itself).
- More robust for modern applications (WPF, UWP) that ignore `SetForegroundWindow()`.

**Cons:**
- Requires COM interop or a specialized wrapper.
- Only needed if we want fine-grained element focus (e.g., focus a specific text field).
- Overkill for "focus the whole window" semantics.

**Verdict:** Not needed for MVP; reserve for future element-level control if required.

## Windows 11 Specifics

**Focus-Stealing Protection:**
- Tighter than Windows 10; `SetForegroundWindow()` more aggressively blocked unless process is foreground.
- `AttachThreadInput()` workaround still effective but requires correct thread IDs.

**Minimized-to-Tray Apps:**
- Some apps (Discord, Slack) can minimize to system tray and hide their window from `EnumWindows()`.
- Recovery: Use taskbar search or explicit app re-launch; beyond scope of focus primitive.

**Elevated Windows:**
- Non-elevated process cannot focus an elevated (admin) window and vice versa.
- Exception: If a process has `SE_DEBUG_NAME` privilege (debugger, system service).
- Voice commander daemon is user-level; cannot focus admin windows.

**Unicode Title Support:**
- `GetWindowText()` in Win32 returns wide-character strings; Python 3 `str` handles Unicode natively.
- No special handling needed; substring matching works transparently.

## Performance: EnumWindows vs FindWindowEx

**EnumWindows:**
- Iterates all top-level windows.
- Flexible: arbitrary matching logic in callback.
- Cost: O(n) where n = number of windows (typically 5–30 on a normal desktop).
- Preferred for partial/substring matching.

**FindWindowEx:**
- Searches direct children of a specified parent (usually desktop).
- Requires exact class name and/or window text.
- Cost: Single system call; fastest for exact matches.
- Not suitable for substring matching without further iteration.

**Verdict:** `EnumWindows()` is the right choice for substring matching; performance penalty is negligible.

## Recommended Approach for Voice Commander

**Use pywin32 direct + optional AttachThreadInput:**

1. Extend `focus_window_by_exe()` to accept `title_substring: str` parameter.
2. Use `EnumWindows()` + `GetWindowText()` for substring matching (case-insensitive).
3. Filter visible windows; skip hidden/minimized-to-tray.
4. Call `ShowWindow(hwnd, SW_RESTORE)` to restore if minimized.
5. **Attempt 1:** Call `SetForegroundWindow(hwnd)` directly.
   - If this fails (returns `False`), log the failure and return `False` to caller.
   - Caller can retry or escalate (e.g., user says command again).
6. **Attempt 2 (optional, advanced):** If direct call fails, use `AttachThreadInput()` workaround.
   - Risk: Adds complexity and potential for thread-state corruption if not carefully unwound.
   - Benefit: Handles more edge cases on Windows 11.
   - Decision: Implement in Phase 6+ if MVP shows too many focus failures; collect metrics first.

**Rationale:**
- Leverages existing `pywin32` dependency (no new packages).
- Reuses proven pattern already in codebase (`focus_window_by_exe()`).
- Substring matching is simple and intuitive for voice ("focus browser", "focus code").
- Failure path is clear: return `False`, user repeats command.
- Simple baseline before investing in AttachThreadInput complexity.

## Edge Cases & Fallbacks

| Scenario | Handling |
|----------|----------|
| No match found | Return `False`; dispatch feedback "app not found". |
| Multiple matches (e.g., two Chrome windows) | Focus the first (oldest); document; consider MRU ranking in Phase 6+. |
| Window minimized to tray | `ShowWindow(SW_RESTORE)` may fail for tray-hidden apps; fallback to app re-launch. |
| Elevated/admin window | Will silently fail to focus; acceptable (user must manually authorize). |
| Focus-stealing blocked by Windows | `SetForegroundWindow()` returns `False`; retry logic in caller. |

## Testing Strategy

**Unit Tests:**
- Mock `EnumWindows()`, `GetWindowText()`, `SetForegroundWindow()`.
- Test substring matching (case-insensitive, partial).
- Test `ShowWindow()` call with minimized window.
- Test failure paths (no match, API exception).

**Integration Tests (marked `@pytest.mark.hardware`):**
- Open multiple real windows (Chrome, Notepad, VS Code, Terminal).
- Call `focus_window(substring)` for each; verify foreground focus changes.
- Measure latency (must be <500ms for voice UX).

**Manual Validation:**
- Speak "focus browser", "focus code", "focus terminal" during daemon run.
- Confirm each window comes to foreground and receives keyboard input.

## References

- [SetForegroundWindow (Win32 docs)](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setforegroundwindow)
- [AllowSetForegroundWindow (Win32 docs)](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-allowsetforegroundwindow)
- [pywinauto SetFocus implementation](https://pywinauto.readthedocs.io/en/latest/code/pywinauto.controls.hwndwrapper.html)
- [AttachThreadInput workaround (GitHub gist)](https://gist.github.com/Aetopia/1581b40f00cc0cadc93a0e8ccb65dc8c)
- [PyGetWindow maintenance status (PyPI)](https://pypi.org/project/PyGetWindow/)
- [EnumWindows vs FindWindowEx analysis](https://copyprogramming.com/howto/win32gui-findwindow-not-finding-window)

## Decision Summary

**Adopt:** PyWin32 direct (`win32gui.EnumWindows()` + `GetWindowText()` + `SetForegroundWindow()`).

**Timeline:** Implement in Phase 4 (MVP toolset expansion) or Phase 6 (local LLM router that needs general window focus).

**Future Enhancement:** Collect failure metrics; if >5% of focus calls fail due to focus-stealing, implement `AttachThreadInput()` workaround in Phase 6+.
