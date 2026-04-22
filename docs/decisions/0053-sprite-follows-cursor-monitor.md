# 0053. Sprite Follows Cursor Across Monitors, Docks Above Taskbar

**Status:** Accepted
**Date:** 2026-04-22
**Spec:** [superpowers/specs/2026-04-22-command-hud-design.md](../superpowers/specs/2026-04-22-command-hud-design.md)

## Context

The sprite was pinned to one configured corner of the primary monitor.
On multi-monitor setups this means the sprite was effectively invisible
whenever the user worked on a secondary display. Users wanted it to
follow — specifically, to dock to the bottom-right of whichever monitor
holds the mouse cursor, sitting *above* (next to) the taskbar rather than
overlapping it, regardless of which edge the taskbar is docked to.

## Decision

Poll `GetCursorPos` on the pyglet clock at 30 Hz. Map cursor to monitor
via `MonitorFromPoint(MONITOR_DEFAULTTONEAREST)`. If the returned
`HMONITOR` changed since last tick, read `MONITORINFO.rcWork` via
`GetMonitorInfoW` (Windows has already shrunk this rect to exclude the
taskbar on whatever edge), call `GetDpiForMonitor` for per-monitor scale,
recompute window width/height (`sprite + HUD + bubble`), and call
`window.set_size` + `window.set_location`. The sprite column — not the HUD
column — is what sits flush against `rcWork.right - margin_x`.

Rejected alternatives:
- **Low-level mouse hook (`WH_MOUSE_LL`)** — runs on the installing
  thread's message pump, subject to `LowLevelHooksTimeout` silent unhook,
  needs elevated rights in some environments. Polling is simpler and safer.
- **Fixed monitor via config** — ignores the actual user problem.
- **Follow the foreground window instead of cursor** — surprising UX;
  cursor is what the user is tracking with their eyes.
- **Use pywin32** — already a dep, but ctypes is enough for four calls
  and avoids cross-process import weight.

## Consequences

- `PER_MONITOR_AWARE_V2` MUST be set *before* the first pyglet window is
  created, else the HWND stays pinned to system-DPI awareness and
  `set_location` coords get silently rescaled across monitors. The
  `SetProcessDpiAwarenessContext(-4)` call moves to the top of `main()`.
- Sprite size rescales per-monitor on boundary cross (explicit
  `set_size`) — without this, the sprite visually shrinks/grows.
- `rcWork` handles any taskbar edge (bottom / top / left / right /
  autohide / multi-taskbar on Win11 22H2+) with no extra work.
- On a locked workstation, `GetCursorPos` returns `(0, 0)` with success;
  the tick no-ops and the sprite parks until unlock.
- Virtual-screen coordinates can be negative (monitors left of / above
  primary); do NOT clamp to `>= 0`.
- 30 Hz polling is ~0.02 % CPU; no measurable battery impact.
