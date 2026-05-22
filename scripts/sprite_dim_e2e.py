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
  post-end    (session still open)-> bright   (ADR 0099 regression)

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
    """Mean luminance of the brightest 1% of pixels in the cat-body region.

    The sprite body occupies the upper portion of the window; badge text
    labels (DICTATING amber, PROCESSING blue, CANCELLED red) are pinned to
    the bottom 15% of the window at ``y=2``.  We crop the bottom 15% before
    sampling so that badge-text pixels — which are intentionally vivid
    regardless of dim state — do not inflate the metric for dim phases.

    Transparent background captures as black, so we ignore it by sampling only
    the brightest pixels — the cat's lit pixels — and comparing that across
    phases. Returns 0.0 if the image can't be read.
    """
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        return 0.0
    try:
        raw = Image.open(png_path).convert("RGB")
    except Exception:
        return 0.0
    # Crop the bottom 15% to exclude badge label pixels.
    w, h = raw.size
    crop_h = int(h * 0.85)
    img = raw.crop((0, 0, w, crop_h))
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
    # Force the subprocess to import voice_sprite from THIS checkout's src/,
    # not whatever path the editable install points at. Without this, a sprite
    # launched from a git worktree silently runs the main working copy's code
    # (the editable .pth is absolute), so the harness would validate the wrong
    # source. Prepending src/ to PYTHONPATH makes the local tree win.
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

        # --- Phase: post-end (server done; session still open -> bright) ---
        # ADR 0099 regression: dictation.end must restore the cat to bright
        # LISTENING when the session is still open — not leave it parked in the
        # dim PROCESSING pose.
        _emit(srv, "dictation.end", {"reason": "done"})
        time.sleep(1.0)
        metrics["post_end"] = _capture_phase(hwnd, "post_end")

        if any(v < 0 for v in metrics.values()):
            log.error("a capture failed; metrics=%s", metrics)
            return 1

        # Assertions: bright phases must be clearly brighter than dim phases.
        # Use a 0.6 ratio guard (dim is ~0.4x of bright; 0.6 leaves margin).
        # post_end is a BRIGHT phase: the session is still listening after the
        # server round-trip, so the cat must have left the dim PROCESSING pose.
        bright = min(metrics["listening"], metrics["capture"], metrics["post_end"])
        dim = max(metrics["idle"], metrics["processing"])
        log.info(
            "bright(min listening/capture/post_end)=%.1f  dim(max idle/processing)=%.1f",
            bright,
            dim,
        )
        if dim <= 0:
            log.error("dim cat not visible at all (metric<=0) — should be dimmed, not gone")
            return 1
        if dim >= bright * 0.6:
            log.error("dim phases not measurably darker than bright phases")
            return 1
        if metrics["post_end"] < max(metrics["idle"], metrics["processing"]) / 0.6:
            log.error(
                "post-end cat not bright (metric=%.1f) — stuck in dim PROCESSING "
                "pose after dictation.end (ADR 0099 regression)",
                metrics["post_end"],
            )
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
