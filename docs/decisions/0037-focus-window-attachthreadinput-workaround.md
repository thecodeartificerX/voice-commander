# ADR 0037: Focus-Window Hardening — AttachThreadInput + Verified Raise Pattern

**Status:** Accepted
**Date:** 2026-04-21

## Context

ADR 0026 introduced the LLM router; one of its primary use cases is chained plans such as *"focus browser then open a new tab"*. `Dispatcher.run_plan` executes these steps sequentially. The correctness of every step after the first depends on the window that received focus in the preceding step actually being in the foreground when the next keystroke fires.

The existing `focus_window_by_exe()` implementation in `src/voice_commander/tools/_win32.py` calls `ShowWindow(SW_RESTORE)` followed by `SetForegroundWindow()`. On Windows 10 this usually succeeds. On Windows 11 it fails silently:

```
pywintypes.error: (0, 'SetForegroundWindow', 'No error message is available')
```

Windows 11 tightened foreground-lockout rules compared to Windows 10. `SetForegroundWindow` is blocked unless the calling process is already the foreground process, the calling thread has received the most recent input event, or the calling process explicitly holds the `ASFW_ANY` foreground permission token. A background daemon process (Voice Commander) holds none of these at the moment it needs to focus a browser window. The call returns `False` (or raises with error code 0) and does nothing — but the function returned without raising, so `Dispatcher.run_plan` continues to the next step, sending keystrokes into the wrong window.

This is a production bug. The failure was reproducible as follows (excerpt from daemon log):

```
2026-04-21 14:22:11 INFO  dispatcher: plan step 1/2 — focus_browser()
2026-04-21 14:22:11 INFO  _win32: SetForegroundWindow returned False, hwnd=0x2A04
2026-04-21 14:22:11 INFO  dispatcher: plan step 2/2 — new_tab()
2026-04-21 14:22:11 INFO  browser: Ctrl+T fired
2026-04-21 14:22:11 DEBUG dispatcher: plan complete (2 steps)
```

The Ctrl+T keystroke landed in the terminal window that was already focused, not in the browser — opening a new terminal tab instead. No error was surfaced; the daemon reported the plan as successful.

R7 §3 (Win32 direct) and §Windows 11 Specifics document two mitigations for this class of failure:

1. **`AttachThreadInput(foreground_tid, target_tid, True)`** — temporarily links the input message queue of the calling thread to the foreground window's thread, which grants `SetForegroundWindow` the input-thread attachment it requires. Detach immediately after the call.
2. **`AllowSetForegroundWindow(ASFW_ANY)`** — grants any process permission to call `SetForegroundWindow` for the process lifetime. Less targeted, but sometimes necessary when `AttachThreadInput` alone is insufficient.

R7 §Recommended Approach originally deferred `AttachThreadInput` to Phase 6+, pending metrics. However, the production log confirmed failure rates high enough to break chains on the first voice command during a real use session. The MVP is not viable if the most common chain (focus + keystroke) silently misfires.

ADR 0033 introduced `settle_ms` to give the OS time to process focus changes before the next step fires. `settle_ms` mitigates race conditions when focus *did* succeed; it cannot help when focus *never succeeded*. These two mechanisms are complementary, not overlapping.

## Decision

`focus_window_by_exe()` (and the new general `focus_window_by_title()` primitive) are hardened using the following pattern:

1. **Restore minimized windows** — call `ShowWindow(hwnd, SW_RESTORE)` before any focus attempt. This is unchanged from the existing implementation.
2. **Permit foreground switching** — call `AllowSetForegroundWindow(ASFW_ANY)` before `SetForegroundWindow`. This acquires the foreground permission token that Windows 11 requires.
3. **AttachThreadInput bracket** — get the current foreground window's thread ID (`foreground_tid`) and the target window's thread ID (`target_tid`). If they differ, call `AttachThreadInput(foreground_tid, target_tid, True)`, then call `SetForegroundWindow(hwnd)`, then unconditionally call `AttachThreadInput(foreground_tid, target_tid, False)` (detach) in a `finally` block. If the thread IDs are the same, call `SetForegroundWindow(hwnd)` directly.
4. **Post-call verification** — after the `SetForegroundWindow` call, read `GetForegroundWindow()`. If it does not equal `hwnd`, the focus change failed. The function **raises `FocusWindowError`** (a new exception in `_win32.py`) with a message including the hwnd and the window title. It does not return `False` silently.
5. **Caller contract** — `focus_window_by_exe()` and `focus_window_by_title()` now declare return type `bool` (for the `True` success path) and raise `FocusWindowError` on verified failure. `Dispatcher.run_plan` catches `FocusWindowError`, logs it at ERROR level, plays the miss chime, and halts the plan at the failing step rather than continuing into subsequent steps.

`settle_ms = 200` is baked into the TOML entries for `focus_browser`, `focus_terminal`, and the `focus_window` primitive. This is an increase from the previous values (150 ms) and reflects the extra time Windows 11 needs after a `SetForegroundWindow` call via `AttachThreadInput` for the target window to complete its paint and start receiving keyboard input reliably. The `settle_ms` timer starts only if focus succeeded — if `FocusWindowError` is raised, the plan halts immediately and the timer is never applied.

## Consequences

### Positive

- Chained plans that begin with a focus step either succeed reliably or halt cleanly. The silent-misfire class of bug (keystrokes landing in the wrong window) is eliminated.
- `FocusWindowError` propagates to structured logs with the window handle and title. Operators can see exactly which window failed to receive focus.
- The `AttachThreadInput` detach is unconditional (in `finally`), so the calling thread's input queue is never left in an attached state even if `SetForegroundWindow` raises internally.
- The verification step (`GetForegroundWindow() == hwnd`) gives a ground-truth result independent of the Win32 return code, which is unreliable (documented as returning `False` with error code 0 under foreground lockout rather than raising).
- `AllowSetForegroundWindow(ASFW_ANY)` covers the subset of Windows 11 configurations where foreground lockout cannot be bypassed by `AttachThreadInput` alone (e.g., certain accessibility policy settings).

### Negative

- The `AttachThreadInput` pattern adds ~5–10 ms overhead per focus call (two extra `AttachThreadInput` system calls + `GetForegroundWindow` verification). This is well below the 200 ms `settle_ms` floor and imperceptible to users.
- `AttachThreadInput` is a low-level Win32 operation. If the target thread ID is stale (window closed between enumeration and focus attempt), the attach call may raise. The implementation wraps the entire pattern in a try/except and converts any Win32 exception to `FocusWindowError`.
- Elevated (admin) windows cannot be focused by a non-elevated daemon regardless of `AttachThreadInput`. The behaviour is unchanged from before — focus fails, `FocusWindowError` is raised, plan halts. This is documented in the TOML description and in `gotchas.md`.
- The `settle_ms = 200` addition means every chained focus step incurs 200 ms of mandatory sleep on success. A focus-only voice command (no chain) still pays this cost. It is accepted: 200 ms is imperceptible to the user after a voice-driven action.

### Neutral

- The `FocusWindowError` raise contract does not change the rapidfuzz-only path (Phase 4 tools). Those tools call `focus_window_by_exe` directly and will now receive a raised exception instead of a silent `False`; tests for those tools must be updated to reflect the new exception semantics.
- Windows 10 behaviour is unchanged in practice: `AllowSetForegroundWindow` is a no-op when the foreground lockout is not active, and `AttachThreadInput` succeeds trivially when both threads are in the same input queue.

## Alternatives considered

### Keep returning `False`, let the plan continue
This is the pre-fix behaviour and the direct cause of the production bug. Rejected. A plan step that reports success without having succeeded is worse than a plan step that fails loudly.

### PyWinAuto `set_focus()`
R7 §4 notes that PyWinAuto internally handles `AttachThreadInput` and is more reliable than bare `SetForegroundWindow`. Rejected because PyWinAuto is a 100+ MB dependency with 1–2 s first-import overhead, and the project already has `pywin32` in `pyproject.toml`. The `AttachThreadInput` pattern from R7 §3 gives equivalent reliability without the weight.

### UIAutomation `IUIAutomationElement.SetFocus()`
R7 §5 notes UIAutomation can focus specific UI elements inside a window. Rejected for this use case: we need window-level focus (bring window to foreground), not element-level focus. UIAutomation adds COM interop overhead and is reserved for future element-level control if required.

### Retry loop (retry `SetForegroundWindow` up to N times on failure)
Considered. Retrying without thread attachment does not help — Windows 11 foreground lockout is not transient; it is a policy decision made at the time of the call. Without `AttachThreadInput`, all retries fail for the same reason. Rejected.

## Also see

- ADR 0033 — `settle_ms` and `wait` primitive; `settle_ms = 200` on focus tools traces to this ADR.
- ADR 0026 — hybrid routing; chained plans that depend on correct focus behaviour.

## References

- R7 — Focus window primitives on Windows; §3 AttachThreadInput idiom; §Windows 11 Specifics
- R7 §4 — PyWinAuto; rejected as too heavy
- R7 §5 — UIAutomation; rejected as overkill
- ADR 0033 — `settle_ms` per-tool metadata; the 200 ms value baked into focus tools
- ADR 0026 — hybrid routing; chained plan execution depends on this fix
- [SetForegroundWindow — Win32 docs](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setforegroundwindow)
- [AllowSetForegroundWindow — Win32 docs](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-allowsetforegroundwindow)
- [AttachThreadInput — Win32 docs](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-attachthreadinput)
