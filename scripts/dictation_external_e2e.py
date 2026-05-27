"""Visual E2E for ADR 0102 — external dictation backend (passthrough).

External mode reuses the dictation sprite machinery but never publishes
``dictation.processing``.  This harness drives the sprite with the exact event
sequence the daemon emits in external mode and asserts on PrintWindow captures:

  enter  : session_started + unmuted + dictation.start
           -> amber "DICTATING" badge present, cat bright, NO light-blue
              "PROCESSING" badge.
  exit    : dictation.end {reason: done} + session_stopped  (owned-session case)
           -> badge cleared, cat dim, still NO processing badge.

Run:  python scripts/dictation_external_e2e.py
Exit: 0 = PASS, 1 = FAIL.

Config/window-finding adapted from scripts/sprite_dim_e2e.py (the proven
reference for this repo's sprite): [web].host/port for the SSE URL, and
win32gui.EnumWindows for PID-based window discovery.
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
LOG_PATH = OUT / "dictation_external_e2e.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("dictation_external_e2e")


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
    # Uses the same [web].host/port pattern as sprite_dim_e2e.py — the proven
    # reference.  The sprite reads daemon_url from [web].host + [web].port
    # (voice_sprite/config.py:192-194), NOT from [sprite].sse_url.
    cfg_path = OUT / "_dictation_external_e2e_config.toml"
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
    # Identical to sprite_dim_e2e.py — the proven window-discovery approach
    # for this repo's sprite (win32gui.EnumWindows + IsWindowVisible + rect size).
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
        log.exception("PrintWindow capture failed")
        return False


def _bright_metric(png_path: Path) -> float:
    """Top-1% luminance of the upper 85% of the sprite window (badge strip excluded).

    The DICTATING badge occupies the bottom ~15% (``y=2`` from window bottom).
    Cropping it out ensures amber badge pixels don't inflate the brightness
    metric for the enter phase, keeping the bright/dim comparison clean.
    Returns 0.0 if the image can't be read.
    """
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        return 0.0
    try:
        raw = Image.open(png_path).convert("RGB")
    except Exception:
        return 0.0
    w, h = raw.size
    crop_h = int(h * 0.85)
    img = raw.crop((0, 0, w, crop_h))
    lums = sorted(0.299 * r + 0.587 * g + 0.114 * b for r, g, b in img.getdata())
    if not lums:
        return 0.0
    top = lums[int(len(lums) * 0.99):]
    return sum(top) / max(1, len(top))


def _badge_pixels(png_path: Path, check: Any) -> int:
    """Count pixels in the bottom 18% strip (where badges draw) matching *check*."""
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        return 0
    try:
        raw = Image.open(png_path).convert("RGB")
    except Exception:
        return 0
    w, h = raw.size
    strip = raw.crop((0, int(h * 0.82), w, h))
    return sum(1 for px in strip.getdata() if check(px))


def _is_amber(px: tuple[int, int, int]) -> bool:
    """DICTATING badge color: (245, 194, 66) with tolerance."""
    r, g, b = px[:3]
    return 200 <= r <= 255 and 160 <= g <= 220 and 30 <= b <= 110


def _is_lightblue(px: tuple[int, int, int]) -> bool:
    """PROCESSING badge color: (100, 200, 255) with tolerance."""
    r, g, b = px[:3]
    return 60 <= r <= 140 and 170 <= g <= 230 and 215 <= b <= 255


def main() -> int:
    port = _free_port()
    srv = _start_sse_server(port)
    cfg = _write_temp_config(port)

    # Force the subprocess to import voice_sprite from THIS checkout's src/,
    # not whatever path the editable install points at.  Identical to
    # sprite_dim_e2e.py lines 256-269 (worktree-safe pattern).
    env = dict(os.environ)
    src_dir = str(ROOT / "src")
    env["PYTHONPATH"] = (
        src_dir + os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else src_dir
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "voice_sprite", "--config", str(cfg)],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        env=env,
    )
    log.info("sprite pid=%d", proc.pid)
    ok = False
    try:
        if not srv.connected.wait(timeout=10.0):  # type: ignore[attr-defined]
            log.error("sprite never connected to fake SSE")
            return 1
        hwnd = _find_window_by_pid(proc.pid)
        if not hwnd:
            log.error("could not locate sprite window")
            return 1
        log.info("sprite hwnd=%d", hwnd)

        # Settle to a known idle state.
        _emit(srv, "warmup_done", {})
        time.sleep(1.0)

        # ENTER (external passthrough): exact event sequence the daemon emits in
        # external mode — session_started + unmuted + dictation.start, with NO
        # dictation.processing (no server round-trip in passthrough).
        _emit(srv, "session_started", {})
        _emit(srv, "unmuted", {})
        _emit(srv, "dictation.start", {})
        time.sleep(1.2)

        enter_png = OUT / "dictation_external_enter.png"
        if not _capture_window(hwnd, enter_png):
            log.error("enter capture failed")
            return 1
        enter_bright = _bright_metric(enter_png)
        enter_amber = _badge_pixels(enter_png, _is_amber)
        enter_blue = _badge_pixels(enter_png, _is_lightblue)
        log.info(
            "ENTER  bright=%.1f  amber_px=%d  blue_px=%d",
            enter_bright,
            enter_amber,
            enter_blue,
        )

        # EXIT (owned-session case): dictation.end then session_stopped.
        # Per ADR 0099 the sprite must restore the pre-dictation pose (IDLE, dim)
        # after dictation.end + session_stopped.
        _emit(srv, "dictation.end", {"reason": "done"})
        _emit(srv, "session_stopped", {})
        time.sleep(1.2)

        exit_png = OUT / "dictation_external_exit.png"
        if not _capture_window(hwnd, exit_png):
            log.error("exit capture failed")
            return 1
        exit_bright = _bright_metric(exit_png)
        exit_amber = _badge_pixels(exit_png, _is_amber)
        exit_blue = _badge_pixels(exit_png, _is_lightblue)
        log.info(
            "EXIT   bright=%.1f  amber_px=%d  blue_px=%d",
            exit_bright,
            exit_amber,
            exit_blue,
        )

        # ------------------------------------------------------------------ #
        # Assertions                                                           #
        # ------------------------------------------------------------------ #
        ok = True

        # 1. DICTATING (amber) badge must be visible on enter.
        if enter_amber < 3:
            log.error(
                "FAIL: DICTATING (amber) badge not visible on enter (amber_px=%d < 3)",
                enter_amber,
            )
            ok = False

        # 2. PROCESSING (light-blue) badge must NEVER appear — the defining
        #    contract of external mode (no server round-trip, no processing phase).
        if enter_blue >= 3:
            log.error(
                "FAIL: PROCESSING (light-blue) badge appeared during enter in "
                "external mode (blue_px=%d) — external mode must never emit "
                "dictation.processing",
                enter_blue,
            )
            ok = False
        if exit_blue >= 3:
            log.error(
                "FAIL: PROCESSING badge appeared at exit (blue_px=%d)",
                exit_blue,
            )
            ok = False

        # 3. DICTATING badge must be cleared on exit.
        if exit_amber >= 3:
            log.error(
                "FAIL: DICTATING badge not cleared on exit (amber_px=%d >= 3)",
                exit_amber,
            )
            ok = False

        # 4. Cat must still be visible at exit (not a black rectangle).
        if exit_bright <= 0:
            log.error("FAIL: cat not visible at exit (metric=%.1f)", exit_bright)
            ok = False

        # 5. Enter (dictation capture, session open) must be measurably brighter
        #    than exit (session closed, IDLE/dim).  Use 0.6 ratio guard matching
        #    sprite_dim_e2e.py — dim is ~0.4× of bright, 0.6 leaves margin.
        if exit_bright > 0 and enter_bright <= exit_bright / 0.6:
            log.error(
                "FAIL: enter not measurably brighter than exit "
                "(enter=%.1f, exit=%.1f) — cat should be bright during DICTATING",
                enter_bright,
                exit_bright,
            )
            ok = False

        if ok:
            log.info(
                "PASS — external mode: DICTATING+bright on enter, no PROCESSING, dim on exit"
            )
            return 0
        return 1
    finally:
        srv.shutdown_flag.set()  # type: ignore[attr-defined]
        srv.queue.put(None)  # type: ignore[attr-defined]
        srv.shutdown()
        try:
            proc.terminate()
            proc.wait(timeout=5.0)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
