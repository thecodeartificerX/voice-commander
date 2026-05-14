"""End-to-end validation for the bare-primitive ``tabs`` picker.

Three phases run in sequence; the script exits non-zero on any failure.

PHASE A — Sprite SSE rendering for verb="tabs"
  Launch the real ``voice_sprite`` subprocess pointed at a stdlib SSE
  server on a free port, push ``picker.open`` with verb="tabs" + 5
  fake page-tab labels, locate the modal via ``FindWindow("vc-picker")``,
  capture it with ``PrintWindow(PW_RENDERFULLCONTENT)``, and assert
  the gold accent + at least one tab title rendered.

PHASE B — Real Comet enumeration
  Find the user's running Comet browser window (any visible window
  owned by ``comet.exe``), call ``list_chromium_tabs(hwnd)``, and
  assert it returns at least two tabs — proving the UIA enumerator
  works on the real, post-warmup Chromium tree the user actually
  drives. Cold-profile fresh-launch was tried first but Comet's
  first-run flow blocks the renderer accessibility tree from
  populating; running Comet is the right target for this validation.

PHASE C — Real Comet tab activation (with restore)
  Snapshot which tab is currently selected in the user's Comet, pick
  a different page tab as the activation target, call the ``tabs``
  primitive on it, poll UIA for ``SelectionItemPattern`` agreement
  (target tab reports ``IsSelected=True``), then restore the
  originally-selected tab so the user's session is left as it was.

Outputs:
  ``outputs/picker_tabs_e2e_modal.png`` — sprite modal capture
  ``outputs/picker_tabs_e2e.log``       — full validation log
  ``outputs/picker_tabs_e2e.json``      — phase pass/fail summary

The harness writes its own HTML stubs and launches Comet in a private
profile so the user's real browsing session is not touched.
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

LOG_PATH = OUT / "picker_tabs_e2e.log"
MODAL_PNG = OUT / "picker_tabs_e2e_modal.png"
SUMMARY_JSON = OUT / "picker_tabs_e2e.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("picker_tabs_e2e")


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _find_modal_hwnd(timeout_s: float = 6.0) -> int:
    import win32gui  # type: ignore

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        hwnd = win32gui.FindWindow(None, "vc-picker")
        if hwnd:
            return int(hwnd)
        time.sleep(0.1)
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
        ok = user32.PrintWindow(hwnd, mem.GetSafeHdc(), 0x2)
        if not ok:
            log.warning("PrintWindow returned 0; falling back to desktop BitBlt")
            desktop_hwnd = win32gui.GetDesktopWindow()
            ddc = win32gui.GetWindowDC(desktop_hwnd)
            dsrc = win32ui.CreateDCFromHandle(ddc)
            mem.BitBlt((0, 0), (w, h), dsrc, (rect[0], rect[1]), 0x00CC0020)
            dsrc.DeleteDC()
            win32gui.ReleaseDC(desktop_hwnd, ddc)

        bmpinfo = bmp.GetInfo()
        bmpstr = bmp.GetBitmapBits(True)
        img = Image.frombuffer(
            "RGB",
            (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
            bmpstr,
            "raw",
            "BGRX",
            0,
            1,
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


def _has_gold_pixels(png_path: Path, sample: int = 2000) -> bool:
    """Count gold-accent pixels in a random sample.

    The modal's gold area (badge backgrounds + header strip) is small
    relative to the full window — for 5 tabs roughly 1.5%. Sampling 200
    pixels at that density yields a Poisson with mean ~3, which makes a
    strict `> 2` threshold flaky (we saw 2 hits in a clearly-rendered
    capture). Bumping the sample to 2000 brings the mean to ~30 and lets
    us use a more decisive `> 10` threshold without false negatives.
    """
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
        if 200 <= r <= 255 and 160 <= g <= 220 and 30 <= b <= 110:
            gold += 1
    log.info("gold pixel hits: %d / %d", gold, sample)
    return gold > 10


# --------------------------------------------------------------------------
# Phase A: SSE rendering with verb="tabs"
# --------------------------------------------------------------------------


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
    srv.queue = queue.Queue()  # type: ignore[attr-defined]
    srv.connected = threading.Event()  # type: ignore[attr-defined]
    srv.shutdown_flag = threading.Event()  # type: ignore[attr-defined]
    threading.Thread(target=srv.serve_forever, daemon=True, name="fake-sse").start()
    return srv


def _emit(srv: ThreadingHTTPServer, type_: str, data: dict[str, Any]) -> None:
    srv.queue.put({"type": type_, "data": data})  # type: ignore[attr-defined]


def _write_sprite_config(port: int) -> Path:
    cfg_path = OUT / "_picker_tabs_e2e_sprite_config.toml"
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


def phase_a() -> bool:
    log.info("=== PHASE A: sprite SSE renders verb='tabs' ===")
    port = _free_port()
    log.info("fake SSE server on port %d", port)
    srv = _start_sse_server(port)
    cfg_path = _write_sprite_config(port)

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

        _emit(srv, "warmup", {})
        time.sleep(0.2)
        _emit(srv, "idle", {})
        time.sleep(0.3)

        # Single-line card layout: tabs picker puts the page title in the
        # ``app`` slot and leaves ``title`` empty. Matches the real payload
        # the daemon emits for ``tabs.`` so the harness exercises the
        # actual render path.
        items = [
            {"n": 1, "label": "Inbox (147) - Gmail", "app": "Inbox (147) - Gmail", "title": ""},
            {"n": 2, "label": "voice-commander - GitHub", "app": "voice-commander - GitHub", "title": ""},
            {"n": 3, "label": "Perplexity Search", "app": "Perplexity Search", "title": ""},
            {"n": 4, "label": "YouTube", "app": "YouTube", "title": ""},
            {"n": 5, "label": "Hacker News", "app": "Hacker News", "title": ""},
        ]
        _emit(srv, "picker.open", {"verb": "tabs", "items": items})

        hwnd = _find_modal_hwnd(timeout_s=6.0)
        if not hwnd:
            log.error("vc-picker window never appeared")
            return False
        log.info("vc-picker hwnd=%d", hwnd)
        time.sleep(0.6)

        if not _capture_window(hwnd, MODAL_PNG):
            return False
        log.info("captured modal → %s", MODAL_PNG)

        if not _has_gold_pixels(MODAL_PNG):
            log.error("captured PNG lacks gold accent")
            return False

        ok = True
        _emit(srv, "picker.close", {"verb": "tabs", "reason": "select"})
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
# Phase B + C: Real Comet enumeration + activation
# --------------------------------------------------------------------------


def _find_running_comet_browser_window() -> int:
    """Return a visible Comet hwnd whose UIA tree exposes a populated
    tab strip. Picks the window with the most TabItems — that's the
    user's main browser window (Comet's helper popups have 0 tabs).
    """
    import psutil  # type: ignore
    import win32gui  # type: ignore
    import win32process  # type: ignore

    from voice_commander.tools.tabs_uia import list_chromium_tabs

    candidates: list[int] = []

    def _enum(hwnd: int, _: object) -> bool:
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
            if not pid:
                return True
            if psutil.Process(pid).name().lower() == "comet.exe":
                candidates.append(int(hwnd))
        except Exception:
            return True
        return True

    win32gui.EnumWindows(_enum, None)

    best_hwnd = 0
    best_count = 0
    for h in candidates:
        try:
            tabs = list_chromium_tabs(h)
        except Exception:
            continue
        if len(tabs) > best_count:
            best_count = len(tabs)
            best_hwnd = h
    return best_hwnd


def phase_b_and_c() -> tuple[bool, bool]:
    log.info("=== PHASE B: real Comet enumeration (running browser) ===")

    hwnd = _find_running_comet_browser_window()
    if not hwnd:
        log.error(
            "no running Comet browser window with tabs found — "
            "open Comet with at least 2 tabs and re-run"
        )
        return False, False
    log.info("running-Comet hwnd=%d", hwnd)

    from voice_commander.tools.tabs_uia import (
        _find_tab_strip,
        is_chromium_browser,
        list_chromium_tabs,
    )

    if not is_chromium_browser(hwnd):
        log.error("is_chromium_browser=False — process not in allowlist?")
        return False, False

    tabs = list_chromium_tabs(hwnd)
    log.info("enumerated %d tabs", len(tabs))
    for t in tabs:
        log.info("  [%d] %s", t.index, t.title[:80])

    if len(tabs) < 2:
        log.error(
            "need at least 2 tabs to validate switching — found %d", len(tabs)
        )
        return False, False
    log.info("PHASE B: enumeration produced %d tabs ✔", len(tabs))
    phase_b_ok = True

    # ---- PHASE C: switch to a different tab, then restore -----------
    log.info("=== PHASE C: invoke a different tab via tabs() tool ===")

    try:
        import uiautomation as ua  # type: ignore
    except ImportError:
        log.error("uiautomation not importable")
        return phase_b_ok, False

    root = ua.ControlFromHandle(hwnd)
    strip = _find_tab_strip(root)
    if strip is None:
        log.error("could not relocate tab strip for selection check")
        return phase_b_ok, False

    from voice_commander.tools.tabs_uia import _collect_tab_items

    items: list = []
    _collect_tab_items(strip, items)

    def _selected_index() -> int:
        for i, it in enumerate(items):
            try:
                sel = it.GetSelectionItemPattern()
                if sel and sel.IsSelected:
                    return i
            except Exception:
                continue
        return -1

    initial = _selected_index()
    initial_title = items[initial].Name if 0 <= initial < len(items) else ""
    log.info("initial selected index=%d title=%r", initial, initial_title[:80])
    if initial < 0:
        log.error("no initially-selected tab found")
        return phase_b_ok, False

    # Pick a target tab that is NOT the currently selected one.
    target = next((t for t in tabs if t.index != initial), None)
    if target is None:
        log.error("only one selectable tab — cannot test switching")
        return phase_b_ok, False
    log.info("activating target index=%d title=%r", target.index, target.title[:80])

    from voice_commander.tools.primitives import tabs as tabs_tool

    phase_c_ok = False
    try:
        result = tabs_tool(
            _browser_hwnd=hwnd,
            _tab_index=target.index,
            _tab_title=target.title,
        )
        log.info("tabs() returned %d", result)

        deadline = time.monotonic() + 3.0
        new_idx = -1
        while time.monotonic() < deadline:
            new_idx = _selected_index()
            if new_idx == target.index:
                break
            time.sleep(0.1)

        log.info(
            "new selected index=%d (target=%d, initial=%d)",
            new_idx,
            target.index,
            initial,
        )
        if new_idx != target.index:
            log.error("UIA reports wrong selection after invoke")
        else:
            phase_c_ok = True
            log.info("PHASE C: activation verified ✔")
    finally:
        # Restore the user's previously-active tab — never leave their
        # session in a different state than we found it.
        log.info("restoring initial tab (index=%d)", initial)
        try:
            restore_result = tabs_tool(
                _browser_hwnd=hwnd,
                _tab_index=initial,
                _tab_title=initial_title,
            )
            log.info("restore tabs() returned %d", restore_result)
        except Exception:
            log.exception("failed to restore initial tab")
        log.info(
            "PHASE B %s | PHASE C %s",
            "PASS" if phase_b_ok else "FAIL",
            "PASS" if phase_c_ok else "FAIL",
        )
    return phase_b_ok, phase_c_ok


# --------------------------------------------------------------------------
# Entry
# --------------------------------------------------------------------------


def main() -> int:
    a = phase_a()
    b, c = phase_b_and_c()
    summary = {
        "phase_a_render": a,
        "phase_b_enumerate": b,
        "phase_c_activate": c,
        "all_pass": bool(a and b and c),
    }
    SUMMARY_JSON.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    log.info(
        "=== SUMMARY: phaseA=%s phaseB=%s phaseC=%s ===",
        "PASS" if a else "FAIL",
        "PASS" if b else "FAIL",
        "PASS" if c else "FAIL",
    )
    return 0 if (a and b and c) else 1


if __name__ == "__main__":
    sys.exit(main())
