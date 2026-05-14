# Visual end-to-end validation protocol

Mandatory for any feature that touches pixels, focus state, audio, hotkeys, or IPC across daemon ↔ sprite ↔ web UI. Unit tests prove code branches. They do **not** prove a user can see, click, hear, or use the feature. Every shipping feature gets an automated harness that exercises it the way a human would, captures evidence, and asserts on that evidence.

## Why

The picker landed with five "fix" commits in a row because each iteration was validated by reading log lines, not by looking at the screen. A `SetWindowPos=False` that returned a falsy bool stayed invisible until someone asked "did the modal actually appear?" — at which point it had been broken for hours. Visual E2E catches this in the first run.

## When the protocol applies

Trigger conditions:

- Anything that draws on screen — modal, overlay, sprite, HUD, web page.
- Anything that changes window state — focus, foreground, minimize, restore, topmost.
- Anything that emits sound — chimes, TTS, audio cues.
- Anything that routes hotkeys.
- Anything that crosses the daemon/sprite/web-UI process boundary (SSE, HTTP, IPC).

If you cannot take a screenshot or query a system property to prove the feature works, raise that as a blocker before shipping.

## The eight rules

### 1. Read first, write second

Grep the modules you are about to drive — config loader, public functions, win32 wrappers — **before** writing harness code. Half the debug time on the picker harness was guessed API surfaces. `win32gui.FindWindowW` does not exist; sprite reads `[web].host`/`port` not `[sprite].daemon_url`. Both findable in seconds with `Grep` / `Read`.

### 2. Stage the harness

Ship one phase at a time. Render-only smoke first (in-process, no subprocess). Verify it. Then add the SSE/IPC layer. Then add real-world dispatch. Each phase gets its own runnable script that exits 0/1. A 400-line harness that fails on line 200 hides every bug after line 200.

### 3. Real signals, not weak signals

"Any gold pixel exists" passes when only the header drew. Real signals:

- **OCR the screenshot**, count expected strings (`1.`, `2.`, … expected app names).
- **Sample pixels at known coords** (badge centers) where colors are deterministic.
- **Query system state directly** — `GetForegroundWindow`, `GetActiveWindow`, `GetWindowText`, `IsWindowVisible`.
- **Assert on the exit code** of the thing under test, not just "didn't crash."

### 4. Capture artifacts

Every harness run writes:

- PNG of the screen / window under test → `outputs/<feature>_e2e.png`
- Full log → `outputs/<feature>_e2e.log`
- JSON summary of assertions → `outputs/<feature>_e2e.json` (optional, useful for diffing across runs)

These survive the run for human inspection and for diff-against-last-good.

### 5. Cleanup on crash

Any subprocess (sprite, tk window, helper app) is wrapped in `try`/`finally` that terminates it. The harness MUST NOT leak processes. Test the crash path: kill mid-run, verify nothing is left via `tasklist | findstr <name>`.

### 6. Run the full test suite at the end

Feature-only pytest does not catch ripple from a shared-type change (e.g. adding a field to `PickerItem`). Run `pytest -q` (or the project's quick-suite) before declaring the work done.

### 7. Test how the user uses it

Do not test the "easy" path — test the user path. Picker example:

- User says "focus." → modal opens
- User reads the modal → harness OCRs the modal
- User says "two." → harness sends "two" through the router
- User sees window 2 come forward → harness asserts `GetForegroundWindow == hwnd2`

If the unit test exercises a function that the user never calls directly, the unit test is not the validation gate.

### 8. Iterate, don't accept first green

First `PASS` is a starting point, not a finish line. Ask:

- Does it look right? (Open the PNG, eyeball it.)
- Edge cases — what if there's only 1 item? 0? 9? Multi-monitor? High-DPI?
- Failure modes — what if SSE drops mid-render? What if dispatch errors?
- Add at least one negative test (fewer items, timeout, cancel word) before calling it done.

## Reference implementation: picker E2E

Use these as templates when bootstrapping a new feature harness.

| Layer | Script | What it proves |
|---|---|---|
| **Render-only smoke** | [`scripts/picker_modal_smoke.py`](../../scripts/picker_modal_smoke.py) | In-process `pyglet` window receives a fake `picker.open` payload, draws one frame, captures the framebuffer to PNG. Fastest visual feedback loop. |
| **Full E2E** | [`scripts/picker_visual_e2e.py`](../../scripts/picker_visual_e2e.py) | Phase A: real `voice_sprite` subprocess + hand-rolled SSE server + `FindWindow("vc-picker")` + `PrintWindow(PW_RENDERFULLCONTENT)` → PNG → pixel assertion. Phase B: 3 Tk windows + `focus(_hwnd=N)` + `GetForegroundWindow` assertion. |

Both write to `outputs/picker_*` and exit non-zero on any failure.

## Boilerplate snippets

### Capturing a specific window via `PrintWindow`

```python
# PW_RENDERFULLCONTENT (0x2) is required for layered/composited windows
# (WS_EX_LAYERED + DWM). Plain PrintWindow without it captures black.
import ctypes
from ctypes import wintypes
user32 = ctypes.windll.user32
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.PrintWindow.restype = wintypes.BOOL
ok = user32.PrintWindow(hwnd, mem.GetSafeHdc(), 0x2)
```

### Tiny SSE server for sprite-side smoke

See `_SSEHandler` / `_start_sse_server` in `scripts/picker_visual_e2e.py`. Stdlib only, no FastAPI required for harness work.

### Spawn-N-isolated-windows fixture

See `_spawn_tk_window` in `scripts/picker_visual_e2e.py`. Tk windows are reliable, lightweight, deterministic titles, no Notepad-tabbed-window surprises.

### Finding a free port

```python
import socket
def free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]; s.close(); return p
```

## Anti-patterns (do not ship these)

- Validating only via log lines ("INFO: state → success") with no screen evidence.
- Asserting on `subprocess.returncode == 0` and calling that "end-to-end."
- Using a single broad pixel sample (`>2 gold pixels exist`) as the only screen-side assertion.
- Hardcoding production ports (`8765`) in test harnesses — collides with a running daemon.
- Leaking sprite / Tk / Notepad subprocesses across harness runs.
- Skipping the full pytest after a "small change to one file."
