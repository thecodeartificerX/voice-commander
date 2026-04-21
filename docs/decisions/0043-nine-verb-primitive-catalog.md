# ADR 0043: Nine-Verb Primitive Catalog

**Status:** Accepted
**Date:** 2026-04-21

## Context

The original primitive catalog shipped six verbs:

| Verb | Purpose |
|---|---|
| `wait(ms)` | Pause between plan steps |
| `press_keys(*keys)` | Send arbitrary keystroke combinations |
| `type_text(text)` | Type a string via `pyautogui.typewrite()` |
| `focus_window(title_substring)` | Bring a window to foreground |
| `launch(app)` | Open an application |
| `no_match(reason)` | Escape hatch — LLM signals no matching tool |

These covered basic command composition but left three common voice-command intents unaddressed:

1. **Scrolling.** "Scroll down" and "page up" are high-frequency commands with no natural single-tool mapping. They require sending specific keys or calling OS scroll APIs, and the LLM needs a named primitive to compose with.
2. **URL opening.** "Open the Voice Commander GitHub page" requires launching a URL in the default browser. `launch()` cannot express this cleanly — it is designed for app names, not URLs.
3. **Window closing.** "Close this window" and "close the terminal" are common. `press_keys('alt', 'f4')` is a workaround, but it is fragile (some apps ignore Alt+F4) and non-declarative. A named `close_window()` primitive expresses intent clearly.

The LLM's ability to compose plans is only as rich as the primitive catalog. Gaps in the catalog force the LLM to either fail (`no_match`) or compose awkward workarounds via `press_keys`. Both outcomes degrade UX.

## Decision

Add three primitives, all marked `llm_only = true`:

### `scroll(direction, amount)`

- `direction`: `"up"` | `"down"` | `"left"` | `"right"`
- `amount`: integer number of scroll clicks (default 3)
- Implementation: `pyautogui.scroll(clicks)` (positive = up, negative = down) or `pyautogui.hscroll()` for left/right.
- `settle_ms`: 0 (scroll is near-instantaneous)

### `open_url(url)`

- `url`: fully-qualified URL string (e.g. `"https://github.com/..."`)
- Implementation: `webbrowser.open(url)` — uses the OS default browser, no subprocess required.
- `settle_ms`: 500 (browser tab open takes a moment to become interactive)

### `close_window(title_substring)`

- `title_substring`: case-insensitive substring to match against the foreground window title (or `""` to close the current foreground window).
- Implementation: `win32gui.FindWindow` / `EnumWindows` to find matching hwnd, then `win32gui.PostMessage(hwnd, WM_CLOSE, 0, 0)`. Uses `WM_CLOSE` (graceful close request), not `TerminateProcess` or `WM_DESTROY`.
- `settle_ms`: 200 (app needs a moment to process the close message)

Total primitive count after this ADR: **9**.

## Consequences

### Positive

- Richer command composition. The LLM can express scrolling, URL navigation, and window closing declaratively in multi-step plans.
- `close_window` uses `WM_CLOSE` — apps receive a normal close request and can save state. No data loss.
- `open_url` via `webbrowser.open()` is stdlib — no new dependency.
- `scroll` via `pyautogui` — already a dependency.
- Total tool count remains well under 25 (the safe upper bound for small MoE model accuracy with tool-calling).

### Negative

- `close_window` requires `pywin32` (`win32gui`) for `EnumWindows` / `PostMessage`. `pywin32` is not currently a runtime dependency. If `pywin32` is not acceptable, the fallback implementation is `pyautogui.hotkey('alt', 'f4')` — less reliable but dependency-free.
- `open_url` with a `webbrowser` module call may open a new tab in an existing browser window or a new window, depending on OS default browser settings. This is acceptable but not fully deterministic.

### Neutral

- All three primitives are `llm_only = true`. They do not appear in any phrase corpus (which is removed in ADR 0040 anyway).
- `scroll` left/right is included for completeness but unlikely to appear in common plans — horizontal scroll is rare in voice command flows.

## Alternatives considered

### Add `close_window` via `pyautogui.hotkey('alt', 'f4')` only
Rejected. Alt+F4 is not universal (browsers intercept it differently across platforms; some apps have custom close handlers). `WM_CLOSE` is the correct Win32 close signal.

### Add URL opening via a `launch` overload
Rejected. `launch()` is designed for application names resolved via the OS app launcher. URLs are a different semantic. A separate `open_url()` primitive is clearer for LLM tool calling — the LLM knows when to use which.

### Raise primitive count higher (add clipboard read, screenshot, etc.)
Deferred. Tool count growth increases the chance of LLM tool-call confusion. Each new primitive should solve a specific, high-frequency gap. Clipboard read and screenshot are lower frequency and have privacy implications. They can be added in a future ADR when the use case is validated.
