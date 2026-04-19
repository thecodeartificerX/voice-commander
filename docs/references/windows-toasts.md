# windows-toasts Reference

> Cited from: https://github.com/DatGuy1/Windows-Toasts (2026-04-19)
> Cited from: https://github.com/DatGuy1/Windows-Toasts/blob/main/tests/test_toasts.py (2026-04-19)
> Cited from: https://github.com/DatGuy1/Windows-Toasts/blob/main/docs/custom_aumid.rst (2026-04-19)
> Cited from: https://pypi.org/project/Windows-Toasts/ (2026-04-19)
> Cited from: Web search results for windows-toasts InteractableWindowsToaster AUMID (2026-04-19)

---

## Overview

`windows-toasts` is a Python library for sending Windows toast notifications using
WinRT (Windows Runtime) bindings instead of pywin32. It supports Windows 10 and 11,
including interactive toasts with buttons, text inputs, images, audio, progress bars,
and duration/scenario control.

Voice Commander uses `InteractableWindowsToaster` to send visual feedback (recording
started, command matched, command missed) with optional action callbacks.

## Installation

```bash
pip install windows-toasts
```

Requires Windows 10+ and Python 3.8+. Uses `winrt-runtime` as backend.

## Minimal Working Example (Voice Commander)

```python
from windows_toasts import Toast, WindowsToaster, InteractableWindowsToaster

# Simple notification (no interaction callbacks)
toaster = WindowsToaster("Voice Commander")
toast = Toast(["Recording started"])
toaster.show_toast(toast)

# Interactive notification with click callback
def on_activated(event_args):
    print("Toast clicked", event_args.arguments)

interactable = InteractableWindowsToaster(
    "Voice Commander",
    notifierAUMID="{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\cmd.exe",  # required for actions
)
toast = Toast(
    ["Command matched: open browser"],
    on_activated=on_activated,
)
interactable.show_toast(toast)
```

---

## API Reference

### `Toast`

```python
Toast(
    text_fields=None,
    audio=None,
    duration=None,
    expiration_time=None,
    group=None,
    launch_action=None,
    progress_bar=None,
    attribution_text=None,
    scenario=None,
    suppress_popup=False,
    timestamp=None,
    actions=(),
    images=(),
    inputs=(),
    on_activated=None,
    on_dismissed=None,
    on_failed=None,
)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `text_fields` | list[str\|None] | Notification text lines. First element = title, second = body, third = extra body. Pass `None` for a line to skip it |
| `audio` | ToastAudio | Audio to play with the notification |
| `duration` | ToastDuration | Display duration: `ToastDuration.Short` or `ToastDuration.Long` |
| `expiration_time` | datetime | When the toast expires from action center |
| `group` | str | Group identifier for notification grouping |
| `launch_action` | str | URI or argument string passed when toast is clicked |
| `progress_bar` | ToastProgressBar | Progress bar element ⚠️ verify |
| `attribution_text` | str | Small attribution text at bottom of notification |
| `scenario` | ToastScenario | Notification scenario: affects rendering |
| `suppress_popup` | bool | If True, notification goes directly to action center |
| `timestamp` | datetime | Custom timestamp displayed on toast |
| `actions` | tuple | ToastButton instances for interactive buttons |
| `images` | tuple | ToastImage instances |
| `inputs` | list | ToastTextBoxInput or ToastSelectBoxInput instances |
| `on_activated` | Callable | Called when user clicks toast or activates a button |
| `on_dismissed` | Callable | Called when toast is dismissed |
| `on_failed` | Callable | Called if notification fails to send |

**Convenience constructor shorthand:**
```python
# text_fields as positional arg
toast = Toast(["Title", "Body text"])
# Equivalent to:
toast = Toast(text_fields=["Title", "Body text"])
```

---

### `ToastDuration`

```python
from windows_toasts import ToastDuration

ToastDuration.Short   # ~7 seconds (default if not set)
ToastDuration.Long    # ~25 seconds
```

> **Note:** If `audio` with `loop=True` is set, duration must be `ToastDuration.Long`.

---

### `ToastScenario`

```python
from windows_toasts import ToastScenario

ToastScenario.Default     # Standard notification
ToastScenario.Alarm       # Alarm-style, stays until dismissed ⚠️ verify exact values
ToastScenario.Reminder    # Reminder style
ToastScenario.IncomingCall # Incoming call style
ToastScenario.Important   # High-importance, bypasses focus assist
```

⚠️ verify: exact enum member names against `windows_toasts/toast.py` source

---

### `WindowsToaster`

```python
WindowsToaster(applicationText: str)
```

Basic toaster for fire-and-forget notifications. Does NOT support `on_activated` callbacks reliably (actions require AUMID).

| Parameter | Type | Description |
|-----------|------|-------------|
| `applicationText` | str | Application name shown in notification |

**Methods:**
```python
toaster.show_toast(toast: Toast) -> None   # send the notification
toaster.clear_toasts(group: str = None)    # remove notifications ⚠️ verify
```

---

### `InteractableWindowsToaster`

```python
InteractableWindowsToaster(
    applicationText: str,
    notifierAUMID: str = None,
)
```

Extended toaster with full interaction support. Required for `on_activated` callbacks
to fire after the notification moves to the action center.

| Parameter | Type | Description |
|-----------|------|-------------|
| `applicationText` | str | Application name shown in notification |
| `notifierAUMID` | str | Application User Model ID. Required for action callbacks |

**Methods:** Same as `WindowsToaster`.

**When to use:**
- You need `on_activated` to fire reliably
- You have buttons (`actions`) in your toast
- You need to handle text input from the notification

---

### AUMID Registration

An AUMID (Application User Model ID) identifies your app to Windows. Interactive toasts
require a recognised AUMID to activate callbacks from the action center.

**Using an existing system AUMID (quick approach):**
```python
# Use cmd.exe AUMID (always available on Windows)
AUMID = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\cmd.exe"

toaster = InteractableWindowsToaster(
    "Voice Commander",
    notifierAUMID=AUMID,
)
```

**Finding existing AUMIDs via PowerShell:**
```powershell
Get-StartApps
# Returns table with Name and AppID columns
```

**Registering a custom AUMID:**

The library ships with a helper script:
```bash
register_hkey_aumid.py --help
# Creates registry entries under HKCU\Software\Classes\AppUserModelId\<your-aumid>
```

Manual registry approach (Python):
```python
import winreg

AUMID = "VoiceCommander.App"
key_path = f"Software\\Classes\\AppUserModelId\\{AUMID}"

with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
    winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "Voice Commander")
    winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, r"C:\path\to\icon.ico")
```

---

### `ToastAudio`

```python
from windows_toasts import ToastAudio
from windows_toasts.wrappers import AudioSource

audio = ToastAudio(AudioSource.IM)          # IM sound
audio = ToastAudio(AudioSource.Default)     # Default notification sound
audio = ToastAudio(AudioSource.Mail)        # Mail sound
audio = ToastAudio(AudioSource.Reminder)    # Reminder sound
audio = ToastAudio(source, loop=False, silent=False)
```

⚠️ verify: full `AudioSource` enum members against `toast_audio.py`

---

### Callback Details

**`on_activated` callback:**
```python
def on_activated(event_args):
    # event_args.arguments  — str: the launch_action or button argument
    # event_args.inputs     — dict: {input_id: value} from text/select boxes
    pass
```

**`on_dismissed` callback:**
```python
def on_dismissed(event_args):
    # event_args.reason  — ToastDismissalReason enum ⚠️ verify
    pass
```

**`on_failed` callback:**
```python
def on_failed(event_args):
    # event_args.error_code  — int
    pass
```

---

## Voice Commander Notification Patterns

```python
from windows_toasts import Toast, InteractableWindowsToaster, ToastDuration

AUMID = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\cmd.exe"
toaster = InteractableWindowsToaster("Voice Commander", notifierAUMID=AUMID)

def notify_recording_started():
    toaster.show_toast(Toast(
        ["Voice Commander", "Recording..."],
        duration=ToastDuration.Short,
        suppress_popup=False,
    ))

def notify_command_matched(transcript: str, command: str, score: float):
    toaster.show_toast(Toast(
        ["Command matched", f"{command!r} ({score:.0f}%)", f'Heard: "{transcript}"'],
        duration=ToastDuration.Short,
    ))

def notify_no_match(transcript: str):
    toaster.show_toast(Toast(
        ["No command matched", f'Heard: "{transcript}"'],
        duration=ToastDuration.Short,
    ))
```

---

## Known Gotchas

1. **`on_activated` requires AUMID** — callbacks from the action center (after toast
   slides away) only fire with a registered AUMID. `WindowsToaster` (without AUMID)
   may not trigger callbacks reliably.

2. **Duration only Short/Long** — no arbitrary seconds. Can't specify "show for 3s".

3. **Thread safety** — `show_toast()` is safe to call from any thread.

4. **`winrt-runtime` version** — ensure compatibility between `windows-toasts` version
   and `winrt-runtime` version. Mixing versions causes import errors. ⚠️ verify

5. **Windows 10 version requirement** — Some features (scenario=Important) require
   Windows 10 1903 or later. ⚠️ verify minimum build numbers

6. **Toast not shown in some Focus Assist modes** — `ToastScenario.Important` bypasses
   Focus Assist on Windows 11.

7. **No toast shown if app not in foreground focus** — toasts appear regardless of
   focus when sent programmatically.

---

## Cited from

- https://github.com/DatGuy1/Windows-Toasts (README) — fetched 2026-04-19
- https://github.com/DatGuy1/Windows-Toasts/blob/main/tests/test_toasts.py — fetched 2026-04-19
- https://github.com/DatGuy1/Windows-Toasts/blob/main/docs/custom_aumid.rst — fetched 2026-04-19
- https://pypi.org/project/Windows-Toasts/ — fetched 2026-04-19
- Web search results for "InteractableWindowsToaster Toast ToastDuration ToastScenario AUMID" — 2026-04-19
