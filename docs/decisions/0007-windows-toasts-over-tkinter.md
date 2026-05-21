# ADR 0007: Windows Toasts via windows_toasts over Tkinter Overlays

**Status:** Superseded by [ADR 0013](0013-drop-winrt-toasts-audio-only-feedback.md) (2026-04-20). The comparative reasoning below (why toasts over Tkinter) remains correct; the project has since concluded that no visual notification at all is preferable for this use case.
**Date:** 2026-04-19

## Context

Voice Commander needs a non-blocking mechanism to deliver brief user-visible feedback — e.g., confirming a command was recognised ("Opening browser"), indicating a miss, or signalling recording state changes. The notification must be visible over all other windows without requiring focus, and it must never stall the daemon's worker thread.

The project targets Windows 11 exclusively. An early prototype considered a floating label widget to show status text, but the user has direct experience of Python floating UIs — both `tkinter` and `PyQt` — causing **thread-blocking freezes**: `tkinter` requires its own `mainloop()` on the main thread, and any cross-thread widget update that falls outside that loop either silently drops the update or deadlocks the calling thread. `PyQt`/`PySide` has the same constraint; emitting signals across threads is safe only with explicit `QObject` signal/slot wiring, which adds boilerplate and still ties a GUI event loop to the process.

The daemon runs three concurrent threads (listener, capture callback, worker). Injecting a GUI event loop as a fourth concern creates a lifecycle problem: the loop must start before the first notification and shut down cleanly on `Ctrl+C`. Getting this right is non-trivial and fragile.

Windows 10 1903+ ships WinRT Toast notifications natively. They are rendered by the Windows Shell, not by the calling process, so the Python process bears zero rendering load and the call returns immediately.

## Decision

Use the `windows_toasts` library (wraps WinRT `Windows.UI.Notifications`) to fire native toast notifications. Each notification is dispatched as a fire-and-forget call: the `WindowsToaster.show_toast()` method hands the toast to the Shell and returns; it **cannot** block the caller thread. No GUI event loop is required in the Python process. The library supports plain-text toasts with a title and body, which is sufficient for all three notification use cases (recognised, missed, recording state).

The daemon calls `windows_toasts` from the worker thread directly. No queue, no dedicated notification thread, no `asyncio` bridge needed.

## Consequences

### Positive
- Thread-safe by design: the WinRT call marshals to the Shell process; Python thread blocking is impossible.
- Zero extra threads or event loops required in the daemon.
- Native Windows 11 appearance, respects Focus Assist / Do Not Disturb settings automatically.
- No heavyweight GUI dependency (`tkinter`, `PyQt`, `wx`) added to the project.
- Toasts are non-modal and auto-dismiss; they never steal focus from the active window.

### Negative
- Windows-only: not portable to macOS or Linux (acceptable — the spec explicitly targets Windows 11).
- Requires an Application User Model ID (AUMID) to be registered for interactive toasts; plain informational toasts work without registration, but actionable toasts (buttons) need a registered AUMID or a desktop shortcut. Current scope uses informational-only toasts, so this is not a blocker.
- `windows_toasts` is a third-party library (not stdlib); adds one more dependency. The library is actively maintained as of 2026 and has no native extension build step.

### Neutral
- Toast duration is controlled by Windows accessibility settings, not by the application; typically 5–7 seconds.
- Toast history appears in the Windows Action Center; this is expected OS behaviour and does not affect the daemon.

## Alternatives considered

### tkinter overlay window
A transparent `Toplevel` window positioned in the screen corner to show status text. Rejected because `tkinter` requires `mainloop()` on the main thread. Attempting to call `widget.configure()` from the worker thread without a running loop either silently fails or raises `RuntimeError`. The workaround (`root.after()` + a `queue.Queue`) adds ~50 lines of fragile plumbing and still ties the daemon to a GUI event loop lifecycle. The user has experienced this exact failure mode in prior projects and explicitly ruled out floating Python UIs for this reason.

### PyQt / PySide overlay
Same structural problem as tkinter: a `QApplication` event loop must run on the main thread, and cross-thread widget mutations require explicit signal/slot wiring. More boilerplate than `tkinter`, heavier install (~60 MB), and the thread-blocking risk is identical. Rejected.

### win11toast
A thin wrapper around `winrt` that predates `windows_toasts`. Viable as a fallback — same WinRT backend, same fire-and-forget guarantee. Rejected as primary choice because `windows_toasts` has a cleaner API, more active maintenance, and explicit support for the `InteractableWindowsToaster` pattern we may use in Phase 3 (actionable toasts for undo).

### plyer notifications
Cross-platform notification library. On Windows it falls back to `win10toast`, which spawns a `tkinter` window internally — reintroducing the threading concern. Rejected.

## References

- `windows_toasts` library: https://github.com/DatGuy1/Windows-Toasts
- WinRT Toast Notifications (Microsoft Docs): https://learn.microsoft.com/en-us/windows/apps/design/shell/tiles-and-notifications/toast-notifications-overview
- Python `tkinter` thread safety caveat: https://docs.python.org/3/library/tkinter.html#threading-model
