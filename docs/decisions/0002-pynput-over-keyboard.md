# ADR 0002: pynput over keyboard for Global Hotkey Listening

**Status:** Accepted
**Date:** 2026-04-19

## Context

Voice Commander needs to intercept a global keyboard event (Scroll Lock toggle — see ADR 0001) regardless of which window currently has focus. The listener must run on Windows 11, integrate cleanly with a Python daemon process, not require elevated (administrator) privileges for normal use, and be installable as a pure-Python dependency via `uv`/PyPI without C-extension build steps. Several Python libraries provide global keyboard hooks; the choice between them involves trade-offs in privilege requirements, cross-platform support, API ergonomics, and maintenance status.

## Decision

Use **`pynput`** as the primary global keyboard listener. `pynput` is installed as a standard PyPI package, requires no administrator privileges on Windows for key-monitoring (it uses the Win32 `SetWindowsHookEx` low-level keyboard hook via `ctypes`), and exposes a clean thread-based callback API (`pynput.keyboard.Listener`). The `keyboard` library is documented in `docs/gotchas.md` as a known fallback, but is not used by default. `pynput` callbacks run on the listener thread; all blocking work (transcription, tool dispatch) is handed off via a `queue.Queue` to the worker thread to avoid freezing global key dispatch.

## Consequences

### Positive
- No administrator privileges required for key monitoring on Windows 11 — standard user accounts work out of the box.
- Cross-platform API: the same `pynput.keyboard.Listener` code runs on Windows, macOS, and Linux without changes (useful if the project ever targets other platforms).
- Well-maintained library with active releases on PyPI; installable without compiling C extensions.
- Clean object-oriented API with `on_press`/`on_release` callbacks and a graceful `stop()` method, making the listener easy to start and stop from the daemon lifecycle.

### Negative
- `pynput` callbacks execute on the listener thread; developers must remember to dispatch all non-trivial work to the worker queue — blocking the callback freezes all global key dispatch (documented in `docs/gotchas.md`).
- `pynput` cannot suppress the OS-level Scroll Lock LED toggle on the key-down event; the LED blinks before the application processes the event. This is cosmetic and harmless but worth documenting.
- The library introduces a dependency on OS-level hooks; on heavily locked-down corporate Windows environments, security software may block `SetWindowsHookEx` calls.

### Neutral
- `pynput` is cross-platform but Voice Commander targets Windows only in Phase 0–5; the cross-platform benefit is a future option, not a current requirement.
- The `keyboard` library (see Alternatives) remains documented as a fallback for users who encounter issues; switching requires only a small shim in `voice_commander/hotkey.py`.

## Alternatives considered

### `keyboard` library
The `keyboard` library provides a simpler, more imperative API (`keyboard.add_hotkey(...)`) and can optionally suppress key events (preventing the Scroll Lock LED toggle). However, on Windows it sometimes requires running the process as administrator to register the low-level hook, which is unacceptable for a user-facing daemon. It is also Windows-only in its most capable mode. `keyboard` is documented as a fallback but is not the default.

### Raw Win32 `RegisterHotKey` via `pywin32`
`RegisterHotKey` is the Windows-native API for registering a global hotkey tied to a message loop. While it works reliably, it requires a Win32 message loop (`GetMessage`/`DispatchMessage`) running on a dedicated thread, adds a dependency on the heavy `pywin32` package, and provides no benefit over `pynput`'s `SetWindowsHookEx` approach for this use case. The extra complexity is unjustified.

### `ctypes` + `SetWindowsHookEx` directly
Calling `SetWindowsHookEx` directly via `ctypes` would eliminate the `pynput` dependency but would require re-implementing thread management, hook installation/removal, and error handling that `pynput` already provides. This approach offers no practical advantage and increases maintenance burden.

## References

- Spec: [../superpowers/specs/2026-04-19-voice-commander-design.md](../superpowers/specs/2026-04-19-voice-commander-design.md)
- pynput documentation: https://pynput.readthedocs.io/
- pynput PyPI: https://pypi.org/project/pynput/
