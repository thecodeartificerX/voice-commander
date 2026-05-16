# UIAutomation Library Reference

Complete API reference for Python `uiautomation` library (by yinkaisheng) for Windows UI Automation control tree navigation and interactive control filtering.

**Latest Version:** 2.0.29 (released August 5, 2025)  
**Python Support:** 3.4–3.12, x86 and x64  
**License:** Apache 2.0  
**Repository:** [yinkaisheng/Python-UIAutomation-for-Windows](https://github.com/yinkaisheng/Python-UIAutomation-for-Windows)

---

## 1. Installation & Import

```bash
pip install uiautomation
```

**Import convention:**
```python
import uiautomation as auto
```

The library depends on `comtypes` (for COM access to UIAutomationCore.dll) and `typing`.

---

## 2. COM Initialization & Threading

### Auto-Initialization

The `uiautomation` library does **not** automatically initialize COM. However, it provides a threading-aware context manager for worker threads.

### For Worker Threads: `UIAutomationInitializerInThread`

When using UI Automation in a separate worker thread, you must instantiate `UIAutomationInitializerInThread` **before** creating or accessing any Control objects:

```python
import threading
import uiautomation as auto

def worker_thread_func():
    # Initialize COM for this thread (required)
    with auto.UIAutomationInitializerInThread(debug=True):
        # Now you can use UI controls in this thread
        root = auto.GetRootControl()
        # ... enumerate and interact with controls ...
```

**Key constraints:**
- You **cannot** reuse a Control or Pattern created in a different thread.
- Each thread must create its own Control instances.
- The context manager handles COM threading requirements (MTA initialization).

### Main Thread

On the main thread, COM is initialized implicitly. For predictability, you can still use the context manager:

```python
with auto.UIAutomationInitializerInThread():
    control = auto.ControlFromHandle(hwnd)
```

**Threading Model:** The library uses COM MTA (Multithreaded Apartment) for thread safety.

---

## 3. Get a Control from an HWND

### `auto.ControlFromHandle(hwnd: int) -> Control | None`

Retrieves a UI Automation element from a native window handle.

**Signature:**
```python
control = auto.ControlFromHandle(hwnd)
```

**Parameters:**
- `hwnd` (int): Native window handle (HWND)

**Returns:**
- A Control subclass instance, or `None` if the handle is invalid or not accessible

**Example:**
```python
import uiautomation as auto

# Get foreground window
hwnd = auto.GetForegroundWindow()  # Requires Win32 import

# Get root control from HWND
root_control = auto.ControlFromHandle(hwnd)
if root_control:
    print(f"Control: {root_control.Name}, Type: {root_control.ControlTypeName}")
```

**Notes:**
- This wraps the Windows COM API `IUIAutomation::ElementFromHandle()`.
- No `ControlFromHwnd()` alias exists; use `ControlFromHandle()`.
- Returns the automation element for the specified window, which can then be queried for descendants.

---

## 4. Walk & Enumerate Descendants

### `auto.WalkControl(control: Control, maxDepth: int = 0xFFFFFFFF, includeTop: bool = True) -> Iterator[tuple[Control, int]]`

Recursively walks the UI Automation tree, yielding (control, depth) tuples.

**Signature:**
```python
for ctrl, depth in auto.WalkControl(root_control, maxDepth=3):
    # Process ctrl at given depth
    pass
```

**Parameters:**
- `control` (Control): Root control to start walking from
- `maxDepth` (int): Maximum traversal depth (default: 0xFFFFFFFF = unlimited)
- `includeTop` (bool): Whether to include the root control itself (default: True)

**Returns:**
- Iterator yielding tuples of (Control, depth_int)

**Example:**
```python
import uiautomation as auto

root = auto.ControlFromHandle(hwnd)

# Walk up to depth 2 (3 levels: 0, 1, 2)
for ctrl, depth in auto.WalkControl(root, maxDepth=2):
    print(f"{'  ' * depth}{ctrl.Name or '(unnamed)'}")
```

**Performance Considerations:**
- Chromium-based browsers (Chrome, Edge, Brave) are **slow** due to large accessibility trees.
- Limit `maxDepth` to reduce traversal cost.
- Default depth 0xFFFFFFFF can traverse thousands of controls in large apps.

### `control.GetChildren() -> list[Control]`

Returns a list of immediate child controls.

**Signature:**
```python
children = control.GetChildren()
for child in children:
    print(child.Name)
```

**Returns:**
- List of Control instances (child controls only, not descendants)

**Use case:**
- When you need only one level of children (vs. recursive WalkControl)

---

## 5. Control Type & Identification

### `control.ControlType -> int`

Returns the control type as an integer ID (enum value).

**Type:** `int`  
**Example values:**
- 50000 = ButtonControl
- 50004 = EditControl
- 50005 = HyperlinkControl
- 50007 = ListItemControl
- 50009 = MenuControl
- 50011 = MenuItemControl
- 50002 = CheckBoxControl
- 50013 = RadioButtonControl
- 50003 = ComboBoxControl
- 50010 = MenuBarControl
- 50012 = ProgressBarControl

### `control.ControlTypeName -> str`

Returns the control type as a human-readable string name.

**Type:** `str`  
**Example values:**
- "ButtonControl"
- "EditControl"
- "HyperlinkControl"
- "ListItemControl"
- "MenuControl"
- "MenuItemControl"
- "CheckBoxControl"
- "RadioButtonControl"
- "ComboBoxControl"
- "WindowControl"

**Example:**
```python
import uiautomation as auto

for ctrl, depth in auto.WalkControl(root):
    if ctrl.ControlTypeName in ('ButtonControl', 'EditControl'):
        print(f"{ctrl.ControlTypeName}: {ctrl.Name}")
```

### Control Type Class Names (as type checks)

The library also provides type classes for isinstance-style checks:

```python
import uiautomation as auto

# These are Control subclasses
auto.ButtonControl
auto.EditControl
auto.HyperlinkControl
auto.CheckBoxControl
auto.RadioButtonControl
auto.ComboBoxControl
auto.MenuItemControl
auto.ListItemControl
auto.TabItemControl
auto.TreeItemControl
auto.SplitButtonControl
```

**Use for filtering:**
```python
# Collect all buttons and edits
interactive_controls = []
for ctrl, depth in auto.WalkControl(root, maxDepth=3):
    if isinstance(ctrl, (auto.ButtonControl, auto.EditControl, auto.HyperlinkControl)):
        interactive_controls.append(ctrl)
```

---

## 6. Bounding Rectangle & Visibility

### `control.BoundingRectangle -> tuple[int, int, int, int]` or `Rect`

Returns the on-screen bounding rectangle of the control.

**Type:** Typically a named tuple or custom Rect class with `.left`, `.top`, `.right`, `.bottom` attributes (can also unpack to 4 integers).

**Unpacking:**
```python
left, top, right, bottom = control.BoundingRectangle
width = right - left
height = bottom - top
```

**Or accessing attributes (if Rect object):**
```python
rect = control.BoundingRectangle
print(f"Position: ({rect.left}, {rect.top})")
print(f"Size: {rect.width()} x {rect.height()}")  # Methods may vary
```

**Coordinates:** **Screen coordinates** (absolute, not relative).

**Example:**
```python
for ctrl, depth in auto.WalkControl(root, maxDepth=2):
    if ctrl.ControlTypeName == 'ButtonControl':
        x1, y1, x2, y2 = ctrl.BoundingRectangle
        center_x = (x1 + x2) // 2
        center_y = (y1 + y2) // 2
        print(f"Button center: ({center_x}, {center_y})")
```

### `control.IsOffscreen -> bool`

Boolean property indicating whether the control is not visible to the user.

**Type:** `bool`  
**True:** Control is off-screen or clipped  
**False:** Control is visible on-screen

**Example:**
```python
for ctrl, depth in auto.WalkControl(root):
    if not ctrl.IsOffscreen and ctrl.ControlTypeName == 'ButtonControl':
        print(f"Visible button: {ctrl.Name}")
```

### `control.IsEnabled -> bool`

Boolean property indicating whether the control is enabled (can receive input).

**Type:** `bool`  
**True:** Control is enabled and interactive  
**False:** Control is disabled (grayed out, non-clickable)

**Combined filtering example:**
```python
interactive_types = {
    'ButtonControl', 'HyperlinkControl', 'MenuItemControl',
    'ListItemControl', 'TabItemControl', 'CheckBoxControl',
    'RadioButtonControl', 'ComboBoxControl', 'EditControl',
    'SplitButtonControl', 'TreeItemControl'
}

clickable_controls = []
for ctrl, depth in auto.WalkControl(root, maxDepth=3):
    if (ctrl.ControlTypeName in interactive_types and
        not ctrl.IsOffscreen and ctrl.IsEnabled):
        clickable_controls.append(ctrl)
```

---

## 7. Computing a Click Point

Once you have a control with a valid bounding rectangle, compute a center click point:

```python
import uiautomation as auto

def get_click_point(control):
    """Return screen (x, y) for clicking control center."""
    if control.IsOffscreen:
        return None
    
    try:
        left, top, right, bottom = control.BoundingRectangle
    except (ValueError, TypeError):
        return None
    
    if left >= right or top >= bottom:
        return None  # Invalid rectangle
    
    center_x = (left + right) // 2
    center_y = (top + bottom) // 2
    return (center_x, center_y)

# Example usage
root = auto.ControlFromHandle(hwnd)
for ctrl, depth in auto.WalkControl(root, maxDepth=2):
    if ctrl.ControlTypeName == 'ButtonControl':
        point = get_click_point(ctrl)
        if point:
            print(f"Click {ctrl.Name} at {point}")
            # auto.Click(*point)  # If performing actual clicks
```

---

## 8. Performance & Optimization

### Depth Limiting

Always limit `maxDepth` when possible to avoid traversing deep control hierarchies:

```python
# Fast: only 2 levels
for ctrl, depth in auto.WalkControl(root, maxDepth=1):
    pass

# Slow: all levels (can be thousands in large apps)
for ctrl, depth in auto.WalkControl(root):
    pass
```

### Chromium Browser Slowness

**Known issue:** Chrome, Edge, Brave expose extremely large accessibility trees (10k+ elements).

**Workaround:**
- Use smaller `maxDepth` values
- Filter by control type early to avoid processing all descendants
- Cache the control tree if enumerating multiple times

### Recommended Practice

```python
# Enumerate once, cache results, filter
all_controls = list(auto.WalkControl(root, maxDepth=3))
buttons = [c for c, _ in all_controls if c.ControlTypeName == 'ButtonControl']
```

---

## 9. Browser Accessibility & Chromium

### Chrome/Edge Accessibility Mode

**Question:** Does attaching a `uiautomation` client auto-enable browser accessibility?

**Answer:** **No.** You must explicitly enable accessibility in Chromium browsers.

### Enable via Command-Line Flag

Pass the `--force-renderer-accessibility` flag when launching Chrome or Edge:

```python
import subprocess

# Windows example
subprocess.Popen([
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    '--force-renderer-accessibility',
    'https://example.com'
])
```

**Note:** As of recent versions, the flag syntax changed to `--force-renderer-accessibility=complete` in some builds.

### Enable via Browser Settings

Alternatively, open the browser's **Accessibility settings** and enable accessibility features.

### Verification

Once enabled, you can walk the browser's control tree:

```python
root = auto.ControlFromHandle(chrome_hwnd)
for ctrl, depth in auto.WalkControl(root, maxDepth=2):
    print(ctrl.ControlTypeName, ctrl.Name)
```

### Known Issues

- **Chrome 117–119:** Accessibility crashes browser tabs ([reported issue](https://community.blueprism.com/t5/Product-Forum/force-renderer-accessibility-not-working-in-chrome/td-p/109640))
- **Edge 117.0.2045.9:** Flag stopped working entirely
- **Workaround:** Use older Chrome/Edge versions or test with Firefox (which has more stable accessibility support)

---

## 10. Alternatives & Comparison

### Direct COM via `comtypes`

For fine-grained control, you can use `comtypes` directly:

```python
from comtypes.client import CreateObject

automation = CreateObject("UIAutomationCore.IUIAutomation")
element = automation.ElementFromHandle(hwnd)
```

**Pros:** Direct API access, no wrapper overhead  
**Cons:** Verbose, COM error handling required, must manage interfaces yourself

### `pywinauto` (Text-Based)

[pywinauto](https://github.com/pywinauto/pywinauto) is a higher-level wrapper supporting multiple backends (Win32 API, UI Automation):

```python
from pywinauto import Application
app = Application(backend='uia').connect(title='Notepad')
```

**Pros:** Higher-level API, more cross-framework support  
**Cons:** Slower, adds abstraction layer

### `pywinauto` with UIA backend

`pywinauto` itself uses `comtypes` under the hood for UIA. Choose:
- **Low-level:** Use `uiautomation` directly (this library)
- **Mid-level:** Use `pywinauto` with `backend='uia'`
- **Raw COM:** Use `comtypes` directly

### Maintenance Status

| Library | Status | Last Release | Notes |
|---------|--------|--------------|-------|
| `uiautomation` | Active | Aug 2025 (v2.0.29) | Actively maintained by yinkaisheng |
| `pywinauto` | Active | Regularly updated | Broader scope, slower |
| `comtypes` | Stable | Mature, minimal changes | Low-level, Win32/COM only |

---

## 11. Complete Working Example

```python
#!/usr/bin/env python3
"""
Enumerate and identify interactive controls in the foreground window.
"""
import sys
import uiautomation as auto
from ctypes import windll

def get_foreground_hwnd():
    """Get the HWND of the currently active window."""
    return windll.user32.GetForegroundWindow()

def filter_interactive_controls(root, max_depth=3):
    """
    Walk control tree and yield interactive controls with click points.
    
    Yields:
        (control, center_x, center_y) tuples
    """
    interactive_types = {
        'ButtonControl',
        'HyperlinkControl',
        'MenuItemControl',
        'ListItemControl',
        'TabItemControl',
        'CheckBoxControl',
        'RadioButtonControl',
        'ComboBoxControl',
        'EditControl',
        'SplitButtonControl',
        'TreeItemControl',
    }
    
    for ctrl, depth in auto.WalkControl(root, maxDepth=max_depth):
        # Filter by control type and visibility
        if ctrl.ControlTypeName not in interactive_types:
            continue
        if ctrl.IsOffscreen:
            continue
        if not ctrl.IsEnabled:
            continue
        
        # Compute click point
        try:
            left, top, right, bottom = ctrl.BoundingRectangle
            if left >= right or top >= bottom:
                continue  # Invalid rectangle
        except (ValueError, TypeError):
            continue
        
        center_x = (left + right) // 2
        center_y = (top + bottom) // 2
        
        yield (ctrl, center_x, center_y)

def main():
    """Main entry point."""
    hwnd = get_foreground_hwnd()
    if not hwnd:
        print("Could not get foreground window")
        return 1
    
    root = auto.ControlFromHandle(hwnd)
    if not root:
        print(f"Could not create control from HWND {hwnd}")
        return 1
    
    print(f"Enumerating controls in window: {root.Name or '(untitled)'}")
    print(f"Root control type: {root.ControlTypeName}\n")
    
    controls = list(filter_interactive_controls(root, max_depth=3))
    print(f"Found {len(controls)} interactive control(s):\n")
    
    for i, (ctrl, cx, cy) in enumerate(controls, 1):
        print(f"{i}. {ctrl.ControlTypeName}")
        print(f"   Name: {ctrl.Name or '(unnamed)'}")
        print(f"   Click point: ({cx}, {cy})")
        if ctrl.AutomationId:
            print(f"   AutomationId: {ctrl.AutomationId}")
        print()
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
```

**Run the example:**
```bash
python example.py
```

This will enumerate and print all interactive controls in the currently active window, along with their screen coordinates for clicking.

---

## References

- [GitHub: Python-UIAutomation-for-Windows](https://github.com/yinkaisheng/Python-UIAutomation-for-Windows)
- [PyPI: uiautomation](https://pypi.org/project/uiautomation/)
- [Microsoft Learn: UI Automation Overview](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-uiautomationoverview)
- [Microsoft Learn: IUIAutomation::ElementFromHandle](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomation-elementfromhandle)
- [Microsoft Learn: UI Automation Control Types Overview](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-controltypesoverview)
- [Microsoft Learn: Understanding Threading Issues](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-threading)
