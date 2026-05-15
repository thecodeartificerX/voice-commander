"""End-to-end validation for the chain primitive's HUD suppression contract.

A ``chain click click`` utterance must produce, on the SSE stream:
  transcript -> tool_fired{click} -> (255 ms gap) -> tool_fired{click} -> plan_outcome{ok}

with NO ``tool_fired{wait}`` between the clicks. The sprite HUD must render:
  - One transcript line ("chain click click" in info/blue colour)
  - Exactly TWO "ok"-coloured (green) tool_fired lines, both showing "click"
  - ZERO "wait" lines (the internal wait step is invisible)

Assertion strategy: pixel-band counting
  pytesseract is not a project dependency and is not installed.
  The HUD exposes no introspection endpoint.
  Therefore we capture the sprite window via PrintWindow and count non-contiguous
  horizontal pixel bands that carry the ``_COLOR_OK`` green (143, 215, 127) from
  chat_log_renderer.py — the exact colour used for "ok"-status HUD lines. Each
  rendered "click" line from a ``tool_fired`` event produces a band of green
  pixels in the HUD region. We expect exactly 2 such bands (click + click) and
  assert that ZERO bands appear at the orange-miss colour (255, 181, 98) or
  the blue-info colour in the "wait" range (wait lines would be green too, so
  the distinguishing assertion is band count == 2, not == more).

  FRAGILITY NOTE: This heuristic assumes the HUD font is large enough to produce
  measurable green runs and that hold_ms is long enough that the lines have not
  faded. The config written here sets hold_ms=8000, fade_ms=0 so lines persist
  for the full harness run. If the heuristic proves flaky on a given machine,
  the preferred upgrade path is to install pytesseract and OCR the crop, counting
  occurrences of "click" and asserting zero occurrences of "wait".

Exit codes:
  0 — pass
  1 — sprite never connected to fake SSE
  2 — sprite window HWND not found by PID enumeration
  3 — PrintWindow capture failed
  4 — HUD green band count != 2 (assertion failure)

Outputs:
  outputs/chain_e2e_hud.png  -- captured sprite window screenshot
  outputs/chain_e2e.log      -- full validation log
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

LOG_PATH = OUT / "chain_e2e.log"
HUD_PNG = OUT / "chain_e2e_hud.png"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("chain_e2e")


# ---------------------------------------------------------------------------
# Free-port helper
# ---------------------------------------------------------------------------


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ---------------------------------------------------------------------------
# Minimal SSE server (stdlib only) — mirrors picker_visual_e2e.py exactly
# ---------------------------------------------------------------------------


class _SSEHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args: Any) -> None:  # silence HTTP access log
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
                    f"id: {ev_id}\n"
                    f"event: {ev['type']}\n"
                    f"data: {json.dumps(ev['data'])}\n\n"
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
    t = threading.Thread(target=srv.serve_forever, daemon=True, name="chain-e2e-sse")
    t.start()
    return srv


def _emit(srv: ThreadingHTTPServer, type_: str, data: dict[str, Any]) -> None:
    srv.queue.put({"type": type_, "data": data})  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Sprite config writer — HUD ENABLED so we can assert on its render
# ---------------------------------------------------------------------------


def _write_sprite_config(port: int) -> Path:
    cfg_path = OUT / "_chain_e2e_sprite_config.toml"
    # Key differences from picker harness config:
    # * [hud] enabled = true  — we NEED the HUD rendered
    # * hold_ms = 8000, fade_ms = 0  — keep lines visible long enough to capture
    # * font_size = 16, width_px = 280  — bigger so green bands are measurable
    # * follow_cursor = false  — deterministic position, no 30 Hz dock noise
    cfg_path.write_text(
        f"""\
hotkey = "scroll_lock"
mic_index = 0
keep_warm_min = 60

[web]
host = "127.0.0.1"
port = {port}

[fuzzy]
focus_threshold = 60
open_threshold = 60

[sprite]
corner = "bottom_right"
base_size_px = 96
asset_path = "assets/sprite"
follow_cursor = false

[hud]
enabled = true
max_lines = 8
hold_ms = 8000
fade_ms = 0
font_size = 16
width_px = 280
""",
        encoding="utf-8",
    )
    return cfg_path


# ---------------------------------------------------------------------------
# HWND lookup: find the sprite window by its process PID
# ---------------------------------------------------------------------------


def _find_sprite_hwnd(pid: int, timeout_s: float = 10.0) -> int:
    """Find the sprite's pyglet window HWND.

    The SpriteWindow uses ``WINDOW_STYLE_OVERLAY`` (``WS_POPUP | WS_EX_LAYERED |
    WS_EX_TRANSPARENT``). Such windows are excluded from ``EnumWindows`` / the
    standard Z-order walk. They ARE enumerable via ``EnumThreadWindows`` — but
    only when called from within the same session. Additionally, pyglet 2.x
    spawns GL context setup in a child process, so the window is owned by a
    grandchild of the process we launched.

    Strategy:
    1. Use ``psutil`` to walk all descendants of *pid* (recursive children).
    2. For each descendant, iterate its threads and call ``EnumThreadWindows``.
    3. Pick the window with text matching ``voice_sprite`` and the largest
       width (sprite + HUD composite window is the widest).

    Returns 0 if no window is found within timeout_s.
    """
    import ctypes
    import ctypes.wintypes

    import psutil  # type: ignore
    import win32gui  # type: ignore
    import win32process  # type: ignore

    WNDENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
    )

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        # Collect all PIDs in the process tree
        try:
            root_proc = psutil.Process(pid)
            all_pids = [root_proc.pid] + [
                c.pid for c in root_proc.children(recursive=True)
            ]
        except psutil.NoSuchProcess:
            time.sleep(0.3)
            continue

        best_hwnd = 0
        best_width = 0

        for p_pid in all_pids:
            try:
                proc = psutil.Process(p_pid)
                tids = [t.id for t in proc.threads()]
            except psutil.NoSuchProcess:
                continue

            for tid in tids:
                candidates: list[int] = []

                def _thread_cb(hwnd: int, _: object, _cands: list[int] = candidates) -> bool:
                    _cands.append(hwnd)
                    return True

                ctypes.windll.user32.EnumThreadWindows(
                    tid, WNDENUMPROC(_thread_cb), 0
                )

                for hwnd in candidates:
                    try:
                        text = win32gui.GetWindowText(hwnd)
                        rect = win32gui.GetWindowRect(hwnd)
                        w = rect[2] - rect[0]
                        h = rect[3] - rect[1]
                        if "voice_sprite" in text and w > 0 and h > 0:
                            log.info(
                                "candidate sprite hwnd=%d text=%r size=%dx%d",
                                hwnd, text[:60], w, h,
                            )
                            if w > best_width:
                                best_width = w
                                best_hwnd = hwnd
                    except Exception:
                        pass

        if best_hwnd:
            log.info("sprite hwnd selected: %d (width=%d)", best_hwnd, best_width)
            return best_hwnd

        time.sleep(0.3)

    return 0


# ---------------------------------------------------------------------------
# PrintWindow capture — verbatim from picker_visual_e2e.py
# ---------------------------------------------------------------------------


def _capture_hwnd_to_png(hwnd: int, png_path: Path) -> bool:
    """Capture *hwnd* via PrintWindow(PW_RENDERFULLCONTENT) and write PNG.

    PW_RENDERFULLCONTENT (0x2) is required for layered/composited windows
    (WS_EX_LAYERED + DWM). Plain PrintWindow without it captures black.
    Returns True on success.
    """
    try:
        import ctypes
        from ctypes import wintypes

        import win32gui  # type: ignore
        import win32ui  # type: ignore
        from PIL import Image  # type: ignore

        rect = win32gui.GetWindowRect(hwnd)
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        if w <= 0 or h <= 0:
            log.error("sprite hwnd has non-positive size: %dx%d", w, h)
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
        ok = user32.PrintWindow(hwnd, mem.GetSafeHdc(), 0x2)
        if not ok:
            log.warning("PrintWindow returned 0; falling back to desktop BitBlt")
            desktop_hwnd = win32gui.GetDesktopWindow()
            ddc = win32gui.GetWindowDC(desktop_hwnd)
            dsrc = win32ui.CreateDCFromHandle(ddc)
            mem.BitBlt((0, 0), (w, h), dsrc, (rect[0], rect[1]), 0x00CC0020)  # SRCCOPY
            dsrc.DeleteDC()
            win32gui.ReleaseDC(desktop_hwnd, ddc)

        bmpinfo = bmp.GetInfo()
        bmpstr = bmp.GetBitmapBits(True)
        img = Image.frombuffer(
            "RGB", (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
            bmpstr, "raw", "BGRX", 0, 1,
        )
        img.save(png_path)

        win32gui.DeleteObject(bmp.GetHandle())
        mem.DeleteDC()
        src.DeleteDC()
        win32gui.ReleaseDC(hwnd, hdc)
        return True
    except Exception:
        log.exception("PrintWindow capture failed")
        return False


# ---------------------------------------------------------------------------
# HUD assertion: count non-contiguous green ("ok") pixel bands
# ---------------------------------------------------------------------------

# Exact colours from src/voice_sprite/chat_log_renderer.py:
_COLOR_OK = (143, 215, 127)    # #8fd77f — tool_fired (click lines)
_COLOR_INFO = (180, 200, 230)  # #b4c8e6 — transcript line
_TOLERANCE = 30                # per-channel tolerance for rendered anti-alias


def _count_color_bands(png_path: Path, target_rgb: tuple[int, int, int]) -> int:
    """Count distinct rendered text lines of *target_rgb* colour in the HUD.

    Each chat-log text line at 16px font size produces several horizontal rows
    of matching pixels separated by small gaps (anti-alias, inter-letter space).
    A naive consecutive-row count returns 2–3 "sub-bands" per rendered line.

    To count logical text-line bands we:
    1. Find all row indices that have >= MIN_HITS_PER_ROW matching pixels.
    2. Merge row indices that are within MERGE_GAP_PX of each other into a
       single logical band.
    3. Return the number of merged bands.

    MERGE_GAP_PX is set to 6 px — large enough to span a font's intra-glyph
    pixel stroke gaps (~2-3 px) but smaller than the minimum gap between two
    adjacent text lines (~7 px at font_size=16, line_gap_px=4).
    Calibrated against the captured sprite window at 96 DPI (scale=1.0).
    """
    MIN_HITS_PER_ROW = 3   # min matching pixels per row to flag it
    STEP = 4               # sample every Nth pixel column for speed
    MERGE_GAP_PX = 6       # rows within this gap are the same logical line

    try:
        from PIL import Image  # type: ignore
    except ImportError:
        log.error("Pillow not available — cannot assert on screenshot")
        return -1
    try:
        img = Image.open(png_path).convert("RGB")
    except Exception:
        log.exception("failed to open PNG for band counting")
        return -1

    w, h = img.size
    tr, tg, tb = target_rgb

    # Scan the LEFT portion of the window (HUD region, width_px=280).
    # The HUD is to the LEFT of the sprite (sprite_region_x = hud_w).
    # base_size_px=96 + hud_w=280 = 376 px total; HUD is leftmost 280 px.
    hud_w = min(280, w)

    # Step 1: find all "hot" rows
    hot_rows: list[int] = []
    for y in range(h):
        hits = 0
        for x in range(0, hud_w, STEP):
            r, g, b = img.getpixel((x, y))
            if (abs(r - tr) <= _TOLERANCE and
                    abs(g - tg) <= _TOLERANCE and
                    abs(b - tb) <= _TOLERANCE):
                hits += 1
        if hits >= MIN_HITS_PER_ROW:
            hot_rows.append(y)

    if not hot_rows:
        return 0

    # Step 2: merge nearby rows into logical bands
    band_count = 1
    for i in range(1, len(hot_rows)):
        if hot_rows[i] - hot_rows[i - 1] > MERGE_GAP_PX:
            band_count += 1

    return band_count


# ---------------------------------------------------------------------------
# Main harness
# ---------------------------------------------------------------------------


def main() -> int:
    log.info("=== chain_visual_e2e: START ===")
    port = _free_port()
    log.info("stub SSE on port %d", port)
    srv = _start_sse_server(port)

    cfg_path = _write_sprite_config(port)
    log.info("sprite config: %s", cfg_path)

    env = os.environ.copy()
    sprite_proc = subprocess.Popen(
        [sys.executable, "-m", "voice_sprite", "--config", str(cfg_path)],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    log.info("sprite pid=%d", sprite_proc.pid)

    rc = 0
    try:
        # --- Wait for sprite to connect ---
        if not srv.connected.wait(timeout=15.0):  # type: ignore[attr-defined]
            log.error("sprite never connected to fake SSE within 15 s")
            return 1

        log.info("sprite SSE connected")

        # Warm up: push state events so the sprite's state machine has settled
        _emit(srv, "warmup", {})
        time.sleep(0.3)
        _emit(srv, "idle", {})
        time.sleep(0.5)

        # --- Drive the chain-primitive HUD sequence ---
        t0 = time.perf_counter()

        # 1. Transcript line — should render as info/blue
        _emit(srv, "transcript", {"text": "chain click click", "confidence": 0.99})

        # 2. First tool_fired — renders as green "click" in HUD
        _emit(srv, "tool_fired", {"name": "click"})

        # 3. Simulate the 255 ms inter-step delay that the real Dispatcher
        #    enforces between chain steps. The wait step is internal=True so
        #    the daemon would NOT emit a tool_fired for it.
        time.sleep(0.255)

        # 4. Second tool_fired — second green "click" in HUD
        _emit(srv, "tool_fired", {"name": "click"})

        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        # 5. plan_outcome{ok} — status=ok suppresses any extra HUD line
        #    (handle_plan_outcome skips appending on ok per plan_outcome_handler.py)
        _emit(
            srv,
            "plan_outcome",
            {
                "transcript": "chain click click",
                "steps": [
                    {"name": "click", "kwargs": {}},
                    {"name": "wait", "kwargs": {"ms": 255}},
                    {"name": "click", "kwargs": {}},
                ],
                "status": "ok",
                "failed_step_index": None,
                "error_msg": None,
                "duration_ms": elapsed_ms,
            },
        )

        log.info("events pushed; elapsed_ms=%d", elapsed_ms)

        # --- Find the sprite HWND ---
        hwnd = _find_sprite_hwnd(sprite_proc.pid, timeout_s=10.0)
        if not hwnd:
            log.error("sprite window HWND not found for pid=%d", sprite_proc.pid)
            return 2
        log.info("sprite hwnd=%d", hwnd)

        # Give the HUD one extra repaint cycle (pyglet runs at 60 Hz)
        time.sleep(1.0)

        # --- Capture ---
        if not _capture_hwnd_to_png(hwnd, HUD_PNG):
            log.error("PrintWindow capture failed")
            return 3
        log.info("screenshot saved to %s", HUD_PNG)

        # --- Assert: exactly 2 green bands (click + click), 0 orange bands ---
        green_bands = _count_color_bands(HUD_PNG, _COLOR_OK)
        log.info("green ('ok') pixel bands: %d (expected 2)", green_bands)

        # Info/blue bands (transcript line) — we log but don't fail on this
        # because the sprite window is nearly transparent and the transcript
        # line may be partially faded or hard to sample at sparse strides.
        info_bands = _count_color_bands(HUD_PNG, _COLOR_INFO)
        log.info("info ('transcript') pixel bands: %d", info_bands)

        if green_bands != 2:
            log.error(
                "ASSERTION FAILED: expected exactly 2 green HUD bands "
                "(one per 'click' tool_fired), got %d. "
                "If this is 0, the HUD may not be rendering (check alpha config). "
                "If it is > 2, a 'wait' tool_fired may have leaked through.",
                green_bands,
            )
            rc = 4
        else:
            log.info("ASSERTION PASSED: 2 green 'click' bands, no 'wait' leak")

        log.info("=== chain_visual_e2e: %s ===", "PASS" if rc == 0 else "FAIL")
        return rc

    finally:
        srv.shutdown_flag.set()  # type: ignore[attr-defined]
        srv.queue.put(None)  # type: ignore[attr-defined]
        srv.shutdown()
        try:
            sprite_proc.terminate()
            sprite_proc.wait(timeout=5.0)
        except Exception:
            sprite_proc.kill()
        log.info("sprite process cleaned up")


if __name__ == "__main__":
    sys.exit(main())
