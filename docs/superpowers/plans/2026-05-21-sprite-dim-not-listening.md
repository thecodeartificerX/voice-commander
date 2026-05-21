# Sprite Dim-When-Not-Listening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `voice_sprite` cat bright when listening and uniformly dimmed (~40%) when not, replacing the inconsistent grey/idle-lock overlay system.

**Architecture:** A single binary visual treatment layered on the existing per-state animations. A pure `is_dim(state, processing)` predicate decides bright vs dim; the window applies it as a `sprite.color` multiply. The dead `muted` consumption in the sprite is removed (the daemon still emits `muted`/`unmuted` — unchanged). No new assets, shaders, or sprite-sheet rows.

**Tech Stack:** Python 3.11+, pyglet 2.1.x (sprite tint via `Sprite.color`), pytest, Windows PrintWindow + Pillow for the visual-E2E harness.

**Spec:** `docs/superpowers/specs/2026-05-21-sprite-dim-not-listening-design.md`

**Conventions for this plan:**
- Run tests from repo root `F:\Tools\Projects\voice-commander`.
- The new ADR for this work is **ADR 0097** (`docs/decisions/0097-sprite-dim-not-listening.md`). Wherever a code comment/docstring references the ADR, write `ADR 0097`.

---

### Task 1: Add the `is_dim` predicate (additive, no behaviour change yet)

**Files:**
- Modify: `src/voice_sprite/state_machine.py` (insert a module-level function after the `SpriteState` enum, which ends at line 19)
- Test: `tests/unit/test_sprite_is_dim.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_sprite_is_dim.py`:

```python
from voice_sprite.state_machine import SpriteState, is_dim

# Bright = listening: any in-session pose with the mic logically engaged.
BRIGHT_STATES = {
    SpriteState.LISTENING,
    SpriteState.HEARING_SPEECH,
    SpriteState.THINKING,
    SpriteState.LLM_THINKING,
    SpriteState.SUCCESS,
    SpriteState.MISS,
    SpriteState.TOOL_ERROR,
}
# Dark = not listening: no active session, plus the dictation decode wait.
DARK_STATES = {
    SpriteState.IDLE,
    SpriteState.WARMUP,
    SpriteState.CRASHED,
    SpriteState.PROCESSING,
}


def test_bright_states_not_dim_when_not_processing():
    for state in BRIGHT_STATES:
        assert is_dim(state, processing=False) is False, state


def test_dark_states_dim_when_not_processing():
    for state in DARK_STATES:
        assert is_dim(state, processing=False) is True, state


def test_processing_flag_forces_dim_for_every_state():
    for state in SpriteState:
        assert is_dim(state, processing=True) is True, state


def test_truth_table_covers_every_state():
    assert BRIGHT_STATES | DARK_STATES == set(SpriteState)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_sprite_is_dim.py -v`
Expected: FAIL — `ImportError: cannot import name 'is_dim' from 'voice_sprite.state_machine'`

- [ ] **Step 3: Implement the predicate**

In `src/voice_sprite/state_machine.py`, insert this function immediately after the `SpriteState` enum (after line 19, before the `EVENT_STATE_MAP` comment block):

```python
def is_dim(state: SpriteState, processing: bool) -> bool:
    """Return True when the cat is NOT listening and should render dimmed.

    Dim = no active voice session (IDLE / WARMUP / CRASHED) or the dictation
    decode wait (``processing``). Every in-session pose — including dictation
    *capture*, which carries a session state with ``processing=False`` — renders
    bright. ``PROCESSING`` is listed defensively; at runtime it always coincides
    with ``processing=True``. See ADR 0097.
    """
    return (
        state
        in (
            SpriteState.IDLE,
            SpriteState.WARMUP,
            SpriteState.CRASHED,
            SpriteState.PROCESSING,
        )
        or processing
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_sprite_is_dim.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/voice_sprite/state_machine.py tests/unit/test_sprite_is_dim.py
git commit -m "feat(sprite): add is_dim listening predicate"
```

---

### Task 2: Add `dim_brightness` config knob (additive)

**Files:**
- Modify: `src/voice_sprite/config.py` (`_require_float`, `_opt_float`, `SpriteAppConfig`, `load_sprite_config`)
- Modify: `config.toml.example` (after line 62, `render_scale = 0.75`)
- Test: `tests/unit/test_sprite_config.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_sprite_config.py`:

```python
def test_dim_brightness_default(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("[sprite]\nbase_size_px = 128\n", encoding="utf-8")
    cfg = load_sprite_config(cfg_path)
    assert cfg.dim_brightness == 0.4


def test_dim_brightness_override(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("[sprite]\ndim_brightness = 0.6\n", encoding="utf-8")
    cfg = load_sprite_config(cfg_path)
    assert cfg.dim_brightness == 0.6


def test_dim_brightness_above_one_raises(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("[sprite]\ndim_brightness = 1.5\n", encoding="utf-8")
    with pytest.raises(SpriteConfigError):
        load_sprite_config(cfg_path)


def test_dim_brightness_negative_raises(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("[sprite]\ndim_brightness = -0.1\n", encoding="utf-8")
    with pytest.raises(SpriteConfigError):
        load_sprite_config(cfg_path)


def test_dim_brightness_bool_rejected(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("[sprite]\ndim_brightness = true\n", encoding="utf-8")
    with pytest.raises(SpriteConfigError):
        load_sprite_config(cfg_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_sprite_config.py -k dim_brightness -v`
Expected: FAIL — `AttributeError: 'SpriteAppConfig' object has no attribute 'dim_brightness'` (the default/override tests) and the range tests fail because the key is silently ignored.

- [ ] **Step 3: Extend `_require_float` with inclusive bounds**

In `src/voice_sprite/config.py`, replace the entire `_require_float` function (lines 97–115) with:

```python
def _require_float(
    table: str,
    key: str,
    raw: Any,
    *,
    min_exclusive: float | None = None,
    min_inclusive: float | None = None,
    max_inclusive: float | None = None,
) -> float:
    # bool subclasses int (and int → float); reject it so TOML `true` doesn't coerce to 1.0.
    if isinstance(raw, bool):
        raise SpriteConfigError(f"[{table}] {key}: expected float, got bool {raw!r}")
    try:
        val = float(raw)
    except (TypeError, ValueError) as exc:
        raise SpriteConfigError(
            f"[{table}] {key}: expected float, got {type(raw).__name__} {raw!r}"
        ) from exc
    if min_exclusive is not None and val <= min_exclusive:
        raise SpriteConfigError(f"[{table}] {key}: must be > {min_exclusive}, got {val}")
    if min_inclusive is not None and val < min_inclusive:
        raise SpriteConfigError(f"[{table}] {key}: must be >= {min_inclusive}, got {val}")
    if max_inclusive is not None and val > max_inclusive:
        raise SpriteConfigError(f"[{table}] {key}: must be <= {max_inclusive}, got {val}")
    return val
```

- [ ] **Step 4: Pass the new bounds through `_opt_float`**

Replace the entire `_opt_float` function (lines 138–146) with:

```python
def _opt_float(
    table: str,
    d: dict[str, Any],
    key: str,
    default: float,
    *,
    min_exclusive: float | None = None,
    min_inclusive: float | None = None,
    max_inclusive: float | None = None,
) -> float:
    if key not in d:
        return default
    return _require_float(
        table,
        key,
        d[key],
        min_exclusive=min_exclusive,
        min_inclusive=min_inclusive,
        max_inclusive=max_inclusive,
    )
```

- [ ] **Step 5: Add the `dim_brightness` field**

In `SpriteAppConfig`, add the field immediately after `render_scale: float = 0.75` (line 64):

```python
    render_scale: float = 0.75
    dim_brightness: float = 0.4  # brightness multiplier (0.0–1.0) for not-listening states (ADR 0097)
```

- [ ] **Step 6: Read the key in `load_sprite_config`**

In the `return SpriteAppConfig(...)` call, add this line immediately after the `render_scale=...` line (line 210):

```python
        render_scale=_opt_float("sprite", sprite_raw, "render_scale", 0.75, min_exclusive=0.0),
        dim_brightness=_opt_float(
            "sprite", sprite_raw, "dim_brightness", 0.4, min_inclusive=0.0, max_inclusive=1.0
        ),
```

- [ ] **Step 7: Document the key in `config.toml.example`**

Insert after line 62 (`render_scale = 0.75`):

```toml
render_scale = 0.75
dim_brightness = 0.4   # 0.0–1.0 brightness multiplier for not-listening (dimmed) states; 1.0 = never dim
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/unit/test_sprite_config.py -v`
Expected: PASS (all, including the 5 new `dim_brightness` tests)

- [ ] **Step 9: Commit**

```bash
git add src/voice_sprite/config.py config.toml.example tests/unit/test_sprite_config.py
git commit -m "feat(sprite): add [sprite] dim_brightness config knob"
```

---

### Task 3: Atomic core swap — dim tint replaces grey/idle-lock; remove sprite `muted`

This task is one atomic commit because the `set_muted`→`set_dim` rename spans the window, renderer, state machine, and `__main__` together — any partial state leaves the app broken. Tests are updated in the same task.

**Files:**
- Modify: `src/voice_sprite/window.py` (constructor + `set_muted`→`set_dim` + `on_draw` tint)
- Modify: `src/voice_sprite/sprite_renderer.py` (delete `_muted` / `set_muted` / frame_region override)
- Modify: `src/voice_sprite/state_machine.py` (delete `muted` field + `muted`/`unmuted` handlers)
- Modify: `src/voice_sprite/__main__.py` (import `is_dim`, replace grey block, pass `dim_brightness`)
- Modify: `tests/unit/test_window.py`
- Modify: `tests/unit/test_state_machine.py`
- Modify: `tests/unit/test_sprite_main.py`

- [ ] **Step 1: Update `test_window.py` (failing first)**

In `tests/unit/test_window.py`, in `_make_window`, replace lines 39–40:

```python
    win._muted = False
    win._mute_color = (128, 128, 128)
```

with:

```python
    win._dim = False
    win._dim_color = (102, 102, 102)
```

And replace the whole `test_set_muted_toggles_flag` test (lines 110–119) with:

```python
def test_set_dim_toggles_flag():
    renderer = MagicMock()
    bubble = MagicMock()
    win = _make_window(renderer, bubble)

    assert win._dim is False
    win.set_dim(True)
    assert win._dim is True
    win.set_dim(False)
    assert win._dim is False
```

- [ ] **Step 2: Update `test_state_machine.py`**

In `tests/unit/test_state_machine.py`, delete both `test_muted_sets_overlay` (lines 62–67) and `test_muted_does_not_change_state` (lines 70–74) entirely. Then in `test_all_states_in_event_map`, change the docstring on line 132 from:

```python
    """Every non-crashed, non-muted state should be reachable via at least one event."""
```

to:

```python
    """Every non-crashed, non-processing state should be reachable via at least one event."""
```

- [ ] **Step 3: Update `test_sprite_main.py`**

In `tests/unit/test_sprite_main.py`, in `test_cancel_badge_distinct_from_dictating_badge` (lines 124–139): change the docstring `"must NOT affect sm.dictating or sm.muted."` to `"must NOT affect sm.dictating."`, delete the line `sm.muted = False` (line 131), and change `window.set_muted.assert_not_called()` (line 139) to `window.set_dim.assert_not_called()`.

- [ ] **Step 4: Run the updated tests to verify they fail**

Run: `pytest tests/unit/test_window.py tests/unit/test_state_machine.py tests/unit/test_sprite_main.py -v`
Expected: FAIL — `AttributeError: 'SpriteWindow' object has no attribute 'set_dim'` and related (`_dim`, removed `muted` handlers still present so the deleted-test removal is fine, but `set_dim` is undefined).

- [ ] **Step 5: Implement `window.py` — constructor**

In `src/voice_sprite/window.py`, add a keyword-only `dim_brightness` parameter. Change the constructor signature block (lines 93–97) from:

```python
        *,
        hud_renderer: ChatLogRenderer | None = None,
        sprite_region_x: int = 0,
        sprite_region_w: int | None = None,
    ) -> None:
```

to:

```python
        *,
        dim_brightness: float = 0.4,
        hud_renderer: ChatLogRenderer | None = None,
        sprite_region_x: int = 0,
        sprite_region_w: int | None = None,
    ) -> None:
```

Then replace the two instance-attribute lines (165–166):

```python
        self._muted = False
        self._mute_color = (128, 128, 128)
```

with:

```python
        # Not-listening dim tint: multiply the sprite toward black by
        # dim_brightness. self._dim is toggled by set_dim() each frame. ADR 0097.
        self._dim = False
        _b = round(255 * dim_brightness)
        self._dim_color = (_b, _b, _b)
```

- [ ] **Step 6: Implement `window.py` — `set_dim` method**

Replace the `set_muted` method (lines 193–194):

```python
    def set_muted(self, muted: bool) -> None:
        self._muted = muted
```

with:

```python
    def set_dim(self, dim: bool) -> None:
        """Toggle the not-listening dim tint. True → sprite is multiplied toward
        black by ``dim_brightness``; False → full brightness. See ADR 0097."""
        self._dim = dim
```

- [ ] **Step 7: Implement `window.py` — `on_draw` tint**

Replace line 268:

```python
        self._sprite.color = self._mute_color if self._muted else (255, 255, 255)
```

with:

```python
        self._sprite.color = self._dim_color if self._dim else (255, 255, 255)
```

- [ ] **Step 8: Implement `sprite_renderer.py` — drop the mute pose-lock**

In `src/voice_sprite/sprite_renderer.py`, replace the tail of `__init__` (lines 25–31):

```python
        self._transition_anim: AnimInfo | None = None
        self._transition_done = False
        # When muted, we render the IDLE pose (alt sitting = disengaged)
        # regardless of logical state, so the cat reads as "not listening".
        # Grey tint applied by the window on top is a secondary cue.
        self._muted = False
        self._set_anim(charsheet.get_state_anim(SpriteState.WARMUP))
```

with:

```python
        self._transition_anim: AnimInfo | None = None
        self._transition_done = False
        self._set_anim(charsheet.get_state_anim(SpriteState.WARMUP))
```

Delete the `set_muted` method (lines 33–37):

```python
    def set_muted(self, muted: bool) -> None:
        """Toggle muted rendering. When muted, frame_region always resolves
        to the IDLE animation regardless of the current state — visually
        communicates that the cat is not processing input."""
        self._muted = muted

```

In `frame_region`, replace the body (lines 84–95):

```python
        anim = self._current_anim
        if self._muted:
            # Override to IDLE anim (disengaged pose) regardless of state.
            anim = self._cs.get_state_anim(SpriteState.IDLE)
        if anim is None:
            return (0, 0, self._cs.frame_width, self._cs.frame_height)
        fw = anim.frame_width or self._cs.frame_width
        fh = anim.frame_height or self._cs.frame_height
        # Frame index loops within the mute-override anim's frame count so
        # the IDLE animation cycles cleanly even if the underlying state's
        # frame count is different.
        frame_idx = self._frame_index % anim.frames
```

with:

```python
        anim = self._current_anim
        if anim is None:
            return (0, 0, self._cs.frame_width, self._cs.frame_height)
        fw = anim.frame_width or self._cs.frame_width
        fh = anim.frame_height or self._cs.frame_height
        frame_idx = self._frame_index % anim.frames
```

- [ ] **Step 9: Implement `state_machine.py` — remove `muted`**

In `src/voice_sprite/state_machine.py`, delete the field line in `__init__` (line 67):

```python
        self.muted = False
```

(leave `self.dictating = False` immediately below it). Then delete the two handler blocks (lines 93–99):

```python
        if event_type == "muted":
            self.muted = True
            return None

        if event_type == "unmuted":
            self.muted = False
            return None

```

(the next block, `if event_type == "dictation.start":`, stays).

- [ ] **Step 10: Implement `__main__.py` — import and wiring**

In `src/voice_sprite/__main__.py`, change the import on line 120 from:

```python
    from .state_machine import SpriteState, StateMachine
```

to:

```python
    from .state_machine import SpriteState, StateMachine, is_dim
```

Pass `dim_brightness` into the window: in the `SpriteWindow(...)` call (lines 212–224), add the argument after `render_scale=cfg.render_scale,`:

```python
        render_scale=cfg.render_scale,
        dim_brightness=cfg.dim_brightness,
```

Replace the grey block (lines 336–340):

```python
        # Grey tint + IDLE-animation freeze apply whenever muted OR dictating.
        grey = sm.muted or sm.dictating
        window.set_muted(grey)
        renderer.set_muted(grey)
        window.set_dictating(sm.dictating)  # badge only; renderer uses grey above
```

with:

```python
        # Dim the cat whenever it is NOT listening: no active session
        # (IDLE/WARMUP/CRASHED) or the dictation decode wait (processing).
        # Dictation *capture* carries a session state with processing=False,
        # so the cat stays bright while the mic is hot. ADR 0097.
        window.set_dim(is_dim(sm.current_state, sm.processing))
        window.set_dictating(sm.dictating)  # amber DICTATING badge during capture
```

- [ ] **Step 11: Verify no stray `muted` / `set_muted` remains in the sprite package**

Run: `git grep -n "set_muted\|_mute_color\|\.muted\|_muted" -- src/voice_sprite tests/unit/test_window.py tests/unit/test_state_machine.py tests/unit/test_sprite_main.py`
Expected: no matches. (The daemon emitters `src/voice_commander/daemon.py` and daemon tests are out of scope and intentionally still contain `muted`/`unmuted`.)

- [ ] **Step 12: Run the full sprite test suite + a daemon regression check**

Run: `pytest tests/unit/test_window.py tests/unit/test_state_machine.py tests/unit/test_sprite_main.py tests/unit/test_sprite_renderer.py tests/unit/test_sprite_state_machine.py tests/unit/test_voice_sprite_state_machine.py tests/unit/test_sprite_is_dim.py tests/unit/test_sprite_config.py tests/unit/test_charsheet.py -v`
Expected: PASS (all)

Run: `pytest tests/unit/test_daemon_session_helpers.py tests/unit/test_event_bus.py -v`
Expected: PASS — proves the daemon still emits `muted`/`unmuted` (unchanged by this work).

- [ ] **Step 13: Commit**

```bash
git add src/voice_sprite/window.py src/voice_sprite/sprite_renderer.py src/voice_sprite/state_machine.py src/voice_sprite/__main__.py tests/unit/test_window.py tests/unit/test_state_machine.py tests/unit/test_sprite_main.py
git commit -m "feat(sprite): dim when not listening, bright when listening (ADR 0097)"
```

---

### Task 4: Visual E2E harness

Mandatory per `docs/agents/visual-e2e-testing.md`. Launches the real sprite subprocess, drives SSE state transitions, captures the sprite window with `PrintWindow`, and asserts that listening phases are measurably brighter than not-listening phases.

**Files:**
- Create: `scripts/sprite_dim_e2e.py`

- [ ] **Step 1: Write the harness**

Create `scripts/sprite_dim_e2e.py`:

```python
"""End-to-end visual validation for the sprite dim-when-not-listening feature (ADR 0097).

Launches the real ``voice_sprite`` subprocess against a fake SSE server, drives
it through bright (listening) and dim (not-listening) phases, captures the sprite
window with PrintWindow each phase, and asserts the listening phases are
measurably brighter than the not-listening phases.

Phases captured:
  IDLE        (not listening)     -> dim
  LISTENING   (session open)      -> bright
  dictation capture               -> bright
  dictation processing (decode)   -> dim

Outputs:
  outputs/sprite_dim_*.png  -- per-phase screenshots
  outputs/sprite_dim_e2e.log
Exit code 0 on PASS, 1 on FAIL.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs"
OUT.mkdir(parents=True, exist_ok=True)
LOG_PATH = OUT / "sprite_dim_e2e.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger("sprite_dim_e2e")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class _SSEHandler(BaseHTTPRequestHandler):
    def log_message(self, *_a: Any) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/healthz"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return
        if self.path.startswith("/events"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            ev_id = 0
            self.server.connected.set()  # type: ignore[attr-defined]
            while not self.server.shutdown_flag.is_set():  # type: ignore[attr-defined]
                try:
                    ev = self.server.queue.get(timeout=1.0)  # type: ignore[attr-defined]
                except queue.Empty:
                    try:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                    except OSError:
                        return
                    continue
                if ev is None:
                    return
                ev_id += 1
                payload = (
                    f"id: {ev_id}\nevent: {ev['type']}\ndata: {json.dumps(ev['data'])}\n\n"
                ).encode("utf-8")
                try:
                    self.wfile.write(payload)
                    self.wfile.flush()
                except OSError:
                    return
            return
        self.send_response(404)
        self.end_headers()


def _start_sse_server(port: int) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), _SSEHandler)
    srv.queue = queue.Queue()  # type: ignore[attr-defined]
    srv.connected = threading.Event()  # type: ignore[attr-defined]
    srv.shutdown_flag = threading.Event()  # type: ignore[attr-defined]
    threading.Thread(target=srv.serve_forever, daemon=True, name="fake-sse").start()
    return srv


def _emit(srv: ThreadingHTTPServer, type_: str, data: dict[str, Any]) -> None:
    srv.queue.put({"type": type_, "data": data})  # type: ignore[attr-defined]


def _write_temp_config(port: int) -> Path:
    cfg_path = OUT / "_sprite_dim_e2e_config.toml"
    cfg_path.write_text(
        f"""\
hotkey = "scroll_lock"
mic_index = 0

[web]
host = "127.0.0.1"
port = {port}

[sprite]
corner = "bottom_right"
base_size_px = 160
asset_path = "assets/sprite"
follow_cursor = false
dim_brightness = 0.4

[sprite.hud]
enabled = false
""",
        encoding="utf-8",
    )
    return cfg_path


def _find_window_by_pid(pid: int, timeout_s: float = 8.0) -> int:
    import win32gui  # type: ignore
    import win32process  # type: ignore

    deadline = time.monotonic() + timeout_s
    found: list[int] = []

    def _cb(hwnd: int, _: Any) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        _, wpid = win32process.GetWindowThreadProcessId(hwnd)
        if wpid == pid:
            rect = win32gui.GetWindowRect(hwnd)
            if (rect[2] - rect[0]) > 0 and (rect[3] - rect[1]) > 0:
                found.append(hwnd)
        return True

    while time.monotonic() < deadline:
        found.clear()
        win32gui.EnumWindows(_cb, None)
        if found:
            return int(found[0])
        time.sleep(0.15)
    return 0


def _capture_window(hwnd: int, png_path: Path) -> bool:
    try:
        import ctypes
        from ctypes import wintypes

        import win32gui  # type: ignore
        import win32ui  # type: ignore
        from PIL import Image  # type: ignore

        rect = win32gui.GetWindowRect(hwnd)
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        if w <= 0 or h <= 0:
            return False
        hdc = win32gui.GetWindowDC(hwnd)
        src = win32ui.CreateDCFromHandle(hdc)
        mem = src.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(src, w, h)
        mem.SelectObject(bmp)
        user32 = ctypes.windll.user32
        user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
        user32.PrintWindow.restype = wintypes.BOOL
        user32.PrintWindow(hwnd, mem.GetSafeHdc(), 0x2)  # PW_RENDERFULLCONTENT
        bmpinfo = bmp.GetInfo()
        bmpstr = bmp.GetBitmapBits(True)
        img = Image.frombuffer(
            "RGB", (bmpinfo["bmWidth"], bmpinfo["bmHeight"]), bmpstr, "raw", "BGRX", 0, 1
        )
        img.save(png_path)
        win32gui.DeleteObject(bmp.GetHandle())
        mem.DeleteDC()
        src.DeleteDC()
        win32gui.ReleaseDC(hwnd, hdc)
        return True
    except Exception:
        log.exception("capture failed")
        return False


def _bright_metric(png_path: Path) -> float:
    """Mean luminance of the brightest 1% of pixels (the lit cat body).

    Transparent background captures as black, so we ignore it by sampling only
    the brightest pixels — the cat's lit pixels — and comparing that across
    phases. Returns 0.0 if the image can't be read.
    """
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        return 0.0
    try:
        img = Image.open(png_path).convert("RGB")
    except Exception:
        return 0.0
    lums = sorted(0.299 * r + 0.587 * g + 0.114 * b for r, g, b in img.getdata())
    if not lums:
        return 0.0
    top = lums[int(len(lums) * 0.99):]
    return sum(top) / max(1, len(top))


def _capture_phase(hwnd: int, name: str) -> float:
    png = OUT / f"sprite_dim_{name}.png"
    if not _capture_window(hwnd, png):
        log.error("phase %s: capture failed", name)
        return -1.0
    m = _bright_metric(png)
    log.info("phase %-12s metric=%.1f  -> %s", name, m, png.name)
    return m


def main() -> int:
    port = _free_port()
    srv = _start_sse_server(port)
    cfg = _write_temp_config(port)
    proc = subprocess.Popen(
        [sys.executable, "-m", "voice_sprite", "--config", str(cfg)],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    log.info("sprite pid=%d", proc.pid)
    ok = False
    metrics: dict[str, float] = {}
    try:
        if not srv.connected.wait(timeout=10.0):  # type: ignore[attr-defined]
            log.error("sprite never connected to fake SSE")
            return 1
        hwnd = _find_window_by_pid(proc.pid)
        if not hwnd:
            log.error("sprite window not found")
            return 1
        log.info("sprite hwnd=%d", hwnd)

        # --- Phase: IDLE (not listening -> dim) ---
        _emit(srv, "warmup_done", {})  # WARMUP -> IDLE
        time.sleep(1.0)
        metrics["idle"] = _capture_phase(hwnd, "idle")

        # --- Phase: LISTENING (session open -> bright) ---
        _emit(srv, "session_started", {})
        _emit(srv, "unmuted", {})  # daemon still emits this; must be a no-op
        time.sleep(1.0)
        metrics["listening"] = _capture_phase(hwnd, "listening")

        # --- Phase: dictation capture (mic hot -> bright) ---
        _emit(srv, "dictation.start", {})
        time.sleep(1.0)
        metrics["capture"] = _capture_phase(hwnd, "capture")

        # --- Phase: dictation processing (decode -> dim) ---
        _emit(srv, "dictation.processing", {})
        time.sleep(1.0)
        metrics["processing"] = _capture_phase(hwnd, "processing")

        if any(v < 0 for v in metrics.values()):
            log.error("a capture failed; metrics=%s", metrics)
            return 1

        # Assertions: listening phases must be clearly brighter than dim phases.
        # Use a 0.6 ratio guard (dim is ~0.4x of bright; 0.6 leaves margin).
        bright = min(metrics["listening"], metrics["capture"])
        dim = max(metrics["idle"], metrics["processing"])
        log.info("bright(min listening/capture)=%.1f  dim(max idle/processing)=%.1f", bright, dim)
        if dim <= 0:
            log.error("dim cat not visible at all (metric<=0) — should be dimmed, not gone")
            return 1
        if dim >= bright * 0.6:
            log.error("dim phases not measurably darker than bright phases")
            return 1
        ok = True
        return 0
    finally:
        srv.shutdown_flag.set()  # type: ignore[attr-defined]
        srv.queue.put(None)  # type: ignore[attr-defined]
        srv.shutdown()
        try:
            proc.terminate()
            proc.wait(timeout=5.0)
        except Exception:
            proc.kill()
        log.info("=== sprite_dim_e2e %s === metrics=%s", "PASS" if ok else "FAIL", metrics)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run the harness against the real sprite**

Run: `python scripts/sprite_dim_e2e.py`
Expected: exit 0; log ends `=== sprite_dim_e2e PASS ===`; `outputs/sprite_dim_idle.png` (dark cat), `outputs/sprite_dim_listening.png` (bright cat), `outputs/sprite_dim_capture.png` (bright + amber badge), `outputs/sprite_dim_processing.png` (dark + blue badge) written.

> If `python-win32`/`Pillow` are missing, install them first: `uv pip install pywin32 Pillow` (they are already project deps for the other E2E harnesses).

- [ ] **Step 3: Eyeball the evidence**

Open the four PNGs and confirm: idle + processing are visibly darker than listening + capture; the amber DICTATING badge shows on `capture`, the blue PROCESSING badge on `processing`.

- [ ] **Step 4: Commit**

```bash
git add scripts/sprite_dim_e2e.py
git commit -m "test(sprite): visual E2E harness for dim-when-not-listening"
```

---

### Task 5: Documentation (ADR, technical-decisions, CLAUDE.md)

A change is not done until its docs are. The spec is already updated; this task lands the ADR, the decisions index row, and the Current-state paragraph.

**Files:**
- Create: `docs/decisions/0097-sprite-dim-not-listening.md`
- Modify: `docs/agents/technical-decisions.md` (append a row to the table)
- Modify: `CLAUDE.md` (Current state paragraph — sprite visual behaviour)

- [ ] **Step 1: Write ADR 0097**

Create `docs/decisions/0097-sprite-dim-not-listening.md`:

```markdown
# ADR 0097 — Sprite dims when not listening, bright when listening

**Status:** Accepted
**Date:** 2026-05-21

## Context

The `voice_sprite` cat tracked 10+ states with an inconsistent visual story: the
only runtime tint was grey `(128,128,128)` applied when `muted` **or**
`dictating`, plus a forced IDLE pose during dictation. Mute's mid-session toggle
was removed (ADR 0025), but the daemon still emits `muted`/`unmuted` as
session-boundary cues; the sprite consumed `muted` for the grey tint. Net effect:
the cat greyed out and "slept" exactly when the mic was hottest (dictation
capture) — backwards.

## Decision

One binary visual rule layered on the existing per-state animations:

- **Listening → bright** (`(255,255,255)`), playing the state's own animation.
- **Not listening → dimmed**, brightness multiplied to `dim_brightness` (default
  0.4), full opacity, still playing the state's own animation row.

The dim decision is the pure predicate `is_dim(state, processing)` in
`voice_sprite/state_machine.py`:

`dim = state in {IDLE, WARMUP, CRASHED, PROCESSING} or processing`

Dictation **capture** carries a session state with `processing=False` → bright;
dictation **decode** (`processing=True`) → dim. Badges (DICTATING / PROCESSING /
CANCELLED) draw at full opacity on top, regardless of dim.

Implementation reuses `pyglet.sprite.Sprite.color` (the existing tint point in
`window.py`). The sprite's `muted` field + `muted`/`unmuted` handlers + the
renderer's IDLE pose-lock are removed as redundant; the daemon's emission of
`muted`/`unmuted` is unchanged (out of scope).

`[sprite] dim_brightness` (0.0–1.0, default 0.4) is configurable;
out-of-range raises `SpriteConfigError`. `1.0` disables dimming.

## Consequences

- Legible, consistent "off" signal; the mic-hot moment is now bright, as expected.
- Less code: one tint path, no pose-lock, no `muted` consumption in the sprite.
- Supersedes the mute-grey behaviour from the ADR 0025 era for the sprite.

## References

- Spec: `docs/superpowers/specs/2026-05-21-sprite-dim-not-listening-design.md`
- Plan: `docs/superpowers/plans/2026-05-21-sprite-dim-not-listening.md`
- Mute toggle removal: ADR 0025
```

- [ ] **Step 2: Append the decisions-index row**

Open `docs/agents/technical-decisions.md`, find the table (same 4-column format as the existing rows: Decision | Description | Rationale | ADR link), and append this row at the end of the table body:

```markdown
| Sprite dims when not listening | Sprite cat is bright while listening (any in-session pose incl. dictation capture) and dimmed to `dim_brightness` (default 0.4, configurable via `[sprite] dim_brightness`) when not — `IDLE`/`WARMUP`/`CRASHED` or dictation decode (`processing`). Decided by pure `is_dim(state, processing)`; reuses `Sprite.color` tint. Sprite's `muted` field + IDLE pose-lock removed (daemon still emits `muted`/`unmuted`). | One legible binary cue replaces the inconsistent grey/idle-lock; the mic-hot moment reads bright instead of greyed. | [0097](../decisions/0097-sprite-dim-not-listening.md) |
```

- [ ] **Step 3: Update the Current-state paragraph in `CLAUDE.md`**

In `CLAUDE.md`, in the **Current state** paragraph, locate the sentence describing the sprite/HUD visual behaviour and add (or fold in) this sentence describing the new rule:

> The sprite cat now uses a single binary visual rule (ADR 0097): **bright** (full brightness) whenever it is listening — any in-session pose including dictation capture — and **dimmed** (brightness ×`[sprite] dim_brightness`, default 0.4, full opacity) when not listening (`IDLE`/`WARMUP`/`CRASHED` or the dictation decode wait, `processing`). The decision is the pure `is_dim(state, processing)` predicate; the prior grey-tint/IDLE-pose-lock and the sprite-side `muted` consumption are removed (the daemon still emits `muted`/`unmuted` at session boundaries). Badges (DICTATING/PROCESSING/CANCELLED) still draw at full opacity on top.

- [ ] **Step 4: Verify docs links resolve**

Run: `git grep -n "0097-sprite-dim-not-listening" -- docs CLAUDE.md`
Expected: matches in `docs/agents/technical-decisions.md` and the ADR self-reference; no broken paths.

- [ ] **Step 5: Commit**

```bash
git add docs/decisions/0097-sprite-dim-not-listening.md docs/agents/technical-decisions.md CLAUDE.md docs/superpowers/specs/2026-05-21-sprite-dim-not-listening-design.md
git commit -m "docs(sprite): ADR 0097 + decisions index + CLAUDE.md for dim-when-not-listening"
```

---

## Self-Review (completed by plan author)

**Spec coverage:**
- Binary listening/dim rule → Tasks 1, 3. ✓
- State→visual mapping (bright/dark sets) → Task 1 truth table + Task 3 wiring. ✓
- Dictation capture bright / decode dim → Task 1 predicate (`processing`) + Task 3 + Task 4 phases. ✓
- Darken (not fade), ~40%, configurable → Task 2 (`dim_brightness`) + Task 3 (`_dim_color`). ✓
- Remove dead sprite `muted` / pose-lock; daemon untouched → Task 3 (+ Step 12 daemon regression). ✓
- Badges unchanged on top → preserved in `on_draw`; verified visually in Task 4 Step 3. ✓
- Unit tests + visual E2E → Tasks 1, 2, 3 (unit), Task 4 (E2E). ✓
- Docs (ADR, technical-decisions, CLAUDE.md, config.toml.example, spec) → Task 2 (config example) + Task 5. ✓

**Placeholder scan:** none — every code/test step has full content; commands have expected output.

**Type/name consistency:** `is_dim(state, processing)` signature identical across Tasks 1/3/4. `set_dim`/`_dim`/`_dim_color`/`dim_brightness` names consistent across window, tests, `__main__`, config. ADR number `0097` consistent across code comments and docs.
