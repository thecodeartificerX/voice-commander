"""End-to-end validation for the bare-primitive focus picker.

Two phases run in sequence; the script exits non-zero if any check fails.

PHASE A — Sprite SSE rendering
  Launch a tiny stdlib SSE server on a private port, spawn the real
  ``voice_sprite`` subprocess pointed at it, push a fake ``picker.open``
  with 7 items, locate the modal HWND via ``FindWindowW("vc-picker")``,
  capture the window with ``PrintWindow``, and assert the framebuffer
  contains the gold accent color (proving the new card UI rendered).

PHASE B — Focus dispatch on real windows
  Spawn 3 Tk windows with distinct titles, snapshot the current
  foreground, call ``voice_commander.tools.primitives.focus(_hwnd=N)``
  on the *second* window, then assert
  ``win32gui.GetForegroundWindow() == that_hwnd``.

Outputs:
  ``outputs/picker_e2e_modal.png`` — captured modal screenshot
  ``outputs/picker_e2e.log``       — full validation log
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

LOG_PATH = OUT / "picker_e2e.log"
MODAL_PNG = OUT / "picker_e2e_modal.png"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8"),
              logging.StreamHandler()],
)
log = logging.getLogger("picker_e2e")


# --------------------------------------------------------------------------
# Phase A helpers
# --------------------------------------------------------------------------


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class _SSEHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args: Any) -> None:
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
    srv.queue = queue.Queue()       # type: ignore[attr-defined]
    srv.connected = threading.Event()  # type: ignore[attr-defined]
    srv.shutdown_flag = threading.Event()  # type: ignore[attr-defined]
    t = threading.Thread(target=srv.serve_forever, daemon=True, name="fake-sse")
    t.start()
    return srv


def _emit(srv: ThreadingHTTPServer, type_: str, data: dict[str, Any]) -> None:
    srv.queue.put({"type": type_, "data": data})  # type: ignore[attr-defined]


def _write_temp_config(port: int) -> Path:
    cfg_path = OUT / "_picker_e2e_sprite_config.toml"
    # Sprite reads daemon_url from [web].host + [web].port, NOT
    # [sprite].daemon_url. See voice_sprite/config.py:172.
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

[sprite.hud]
enabled = false
""",
        encoding="utf-8",
    )
    return cfg_path


def _find_modal_hwnd(timeout_s: float = 5.0) -> int:
    import win32gui  # type: ignore

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        hwnd = win32gui.FindWindow(None, "vc-picker")
        if hwnd:
            return int(hwnd)
        time.sleep(0.1)
    return 0


def _capture_window(hwnd: int, png_path: Path) -> bool:
    """Capture *hwnd* via PrintWindow + write PNG. Returns True on success."""
    try:
        import ctypes
        from ctypes import wintypes

        import win32gui  # type: ignore
        import win32ui  # type: ignore
        from PIL import Image  # type: ignore

        rect = win32gui.GetWindowRect(hwnd)
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        if w <= 0 or h <= 0:
            log.error("modal hwnd has non-positive size: %dx%d", w, h)
            return False

        hdc = win32gui.GetWindowDC(hwnd)
        src = win32ui.CreateDCFromHandle(hdc)
        mem = src.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(src, w, h)
        mem.SelectObject(bmp)

        # PW_RENDERFULLCONTENT (0x2) is required for layered/composited
        # windows like ours (WS_EX_LAYERED via DWM). Plain PrintWindow
        # without it captures a solid-black framebuffer.
        user32 = ctypes.windll.user32
        user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
        user32.PrintWindow.restype = wintypes.BOOL
        ok = user32.PrintWindow(hwnd, mem.GetSafeHdc(), 0x2)
        if not ok:
            log.warning("PrintWindow returned 0; falling back to BitBlt screen capture")
            # Fallback: BitBlt from the desktop at the window's coords.
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


def _has_gold_pixels(png_path: Path, sample: int = 200) -> bool:
    """Sample *sample* random pixels; return True if any are 'gold-ish'."""
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        return False
    try:
        img = Image.open(png_path).convert("RGB")
    except Exception:
        return False
    w, h = img.size
    import random
    rng = random.Random(0xC0DE)
    gold = 0
    for _ in range(sample):
        px = img.getpixel((rng.randrange(w), rng.randrange(h)))
        r, g, b = px[:3]
        # Gold accent target (245, 194, 66) — accept neighborhood.
        if 200 <= r <= 255 and 160 <= g <= 220 and 30 <= b <= 110:
            gold += 1
    log.info("gold pixel hits: %d / %d", gold, sample)
    return gold > 2


def phase_a() -> bool:
    log.info("=== PHASE A: sprite SSE + modal render ===")
    port = _free_port()
    log.info("fake SSE server on port %d", port)
    srv = _start_sse_server(port)

    cfg_path = _write_temp_config(port)
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

    ok = False
    try:
        if not srv.connected.wait(timeout=10.0):  # type: ignore[attr-defined]
            log.error("sprite never connected to fake SSE")
            return False
        log.info("sprite SSE connected")

        # Drive the state machine briefly so the sprite's own state has settled
        # before we open the picker.
        _emit(srv, "warmup", {})
        time.sleep(0.2)
        _emit(srv, "idle", {})
        time.sleep(0.3)

        items = [
            {"n": 1, "label": "Chrome — voice-commander", "app": "Chrome", "title": "voice-commander"},
            {"n": 2, "label": "Code - Insiders — picker_modal.py", "app": "Code - Insiders", "title": "picker_modal.py"},
            {"n": 3, "label": "WindowsTerminal — pwsh", "app": "WindowsTerminal", "title": "pwsh"},
            {"n": 4, "label": "WhatsApp", "app": "WhatsApp", "title": ""},
            {"n": 5, "label": "Explorer — Downloads", "app": "Explorer", "title": "Downloads"},
            {"n": 6, "label": "Comet — tabs", "app": "Comet", "title": "tabs"},
            {"n": 7, "label": "Spotify — Now Playing", "app": "Spotify", "title": "Now Playing"},
        ]
        _emit(srv, "picker.open", {"verb": "focus", "items": items})

        hwnd = _find_modal_hwnd(timeout_s=6.0)
        if not hwnd:
            log.error("vc-picker window not found within timeout")
            return False
        log.info("vc-picker hwnd=%d", hwnd)

        # Give one extra frame to render the cards after window appears.
        time.sleep(0.6)

        if not _capture_window(hwnd, MODAL_PNG):
            return False
        log.info("captured modal → %s", MODAL_PNG)

        if not _has_gold_pixels(MODAL_PNG):
            log.error("captured PNG lacks gold accent — modal likely blank")
            return False

        ok = True
        _emit(srv, "picker.close", {"verb": "focus", "reason": "select"})
        time.sleep(0.3)
        return True
    finally:
        srv.shutdown_flag.set()  # type: ignore[attr-defined]
        srv.queue.put(None)  # type: ignore[attr-defined]
        srv.shutdown()
        try:
            sprite_proc.terminate()
            sprite_proc.wait(timeout=5.0)
        except Exception:
            sprite_proc.kill()
        log.info("PHASE A %s", "PASS" if ok else "FAIL")


# --------------------------------------------------------------------------
# Phase B helpers
# --------------------------------------------------------------------------


_TK_WINDOW_CODE = """
import sys, tkinter as tk
title = sys.argv[1]
root = tk.Tk()
root.title(title)
root.geometry(sys.argv[2])
tk.Label(root, text=title, font=("Segoe UI", 18)).pack(expand=True, fill="both")
root.mainloop()
"""


def _spawn_tk_window(title: str, geom: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-c", _TK_WINDOW_CODE, title, geom],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )


def _wait_for_window(title: str, timeout_s: float = 6.0) -> int:
    import win32gui  # type: ignore
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        hwnd = win32gui.FindWindow(None, title)
        if hwnd:
            return int(hwnd)
        time.sleep(0.1)
    return 0


def phase_b() -> bool:
    log.info("=== PHASE B: focus dispatch on real windows ===")

    titles = ["vc-test-A", "vc-test-B", "vc-test-C"]
    geoms = ["400x200+200+200", "400x200+650+200", "400x200+1100+200"]
    procs: list[subprocess.Popen[bytes]] = []
    hwnds: dict[str, int] = {}
    ok = False
    try:
        for t, g in zip(titles, geoms):
            procs.append(_spawn_tk_window(t, g))
        for t in titles:
            h = _wait_for_window(t, timeout_s=8.0)
            if not h:
                log.error("test window %r never appeared", t)
                return False
            hwnds[t] = h
            log.info("found %s hwnd=%d", t, h)

        time.sleep(0.4)

        target = hwnds["vc-test-B"]
        log.info("focusing target %s (hwnd=%d)", "vc-test-B", target)

        from voice_commander.tools.primitives import focus
        result = focus(_hwnd=target)
        log.info("focus() returned hwnd=%d", result)

        time.sleep(0.4)

        import win32gui  # type: ignore
        fg = int(win32gui.GetForegroundWindow())
        log.info("GetForegroundWindow() → %d (target was %d)", fg, target)

        if fg != target:
            # Some Win32 focus restrictions need the target's owner thread to
            # AttachThreadInput; the focus tool already does this. If we're
            # still off, log the active title for debugging.
            try:
                fg_title = win32gui.GetWindowText(fg)
            except Exception:
                fg_title = "<unknown>"
            log.error("foreground mismatch — actual title=%r", fg_title)
            return False

        ok = True
        log.info("PHASE B foreground == target ✔")
        return True
    finally:
        # Cleanup test windows
        for p in procs:
            try:
                p.terminate()
                p.wait(timeout=3.0)
            except Exception:
                p.kill()
        log.info("PHASE B %s", "PASS" if ok else "FAIL")


# --------------------------------------------------------------------------
# Entry
# --------------------------------------------------------------------------


def main() -> int:
    a = phase_a()
    b = phase_b()
    log.info("=== SUMMARY: phaseA=%s phaseB=%s ===", "PASS" if a else "FAIL", "PASS" if b else "FAIL")
    return 0 if (a and b) else 1


if __name__ == "__main__":
    sys.exit(main())
