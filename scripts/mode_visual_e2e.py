"""Visual E2E: assert the sprite renders a persistent mode badge on mode.enter
and clears it on mode.exit.

Reuses the SSE-server + sprite-subprocess + PrintWindow scaffolding from
picker_visual_e2e.py and sprite_dim_e2e.py.

Phases:
  1. Emit warmup_done + session_started to settle the sprite into LISTENING.
  2. Emit mode.enter {"name":"video","badge":"🎬 VIDEO"} — badge must appear
     in green (120, 220, 140) at the top-centre of the sprite window.
  3. Capture outputs/mode_badge_on.png — assert green pixels present.
  4. Emit mode.exit {"name":"video","reason":"end_phrase"} — badge must clear.
  5. Capture outputs/mode_badge_off.png — assert NO green badge pixels.

Outputs:
  outputs/mode_badge_on.png   — captured sprite with mode badge visible
  outputs/mode_badge_off.png  — captured sprite after badge cleared
  outputs/mode_visual_e2e.log — full validation log

Exit code 0 on PASS, 1 on FAIL.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs"
OUT.mkdir(parents=True, exist_ok=True)

LOG_PATH = OUT / "mode_visual_e2e.log"
BADGE_ON_PNG = OUT / "mode_badge_on.png"
BADGE_OFF_PNG = OUT / "mode_badge_off.png"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("mode_visual_e2e")

# ---------------------------------------------------------------------------
# Import reusable scaffolding from picker_visual_e2e (same scripts/ dir)
# ---------------------------------------------------------------------------

sys.path.insert(0, str(ROOT / "scripts"))
from picker_visual_e2e import (  # type: ignore[import]
    _capture_window,
    _emit,
    _start_sse_server,
    _write_temp_config,
)

# ---------------------------------------------------------------------------
# Badge colour assertion
# ---------------------------------------------------------------------------

# green (120, 220, 140) — matches the exact RGB baked into window.py line 375.
# Do NOT change this without updating window.py too.
_BADGE_RGB = (120, 220, 140)
_BADGE_TOLERANCE = 40  # per-channel tolerance for JPEG/rendering variation


def _has_badge_pixels(png_path: Path, *, top_fraction: float = 0.35) -> bool:
    """Return True if the top *top_fraction* of *png_path* contains green badge pixels.

    The mode badge renders at pyglet y = height - 2 (near the sprite window
    top).  In PIL coordinates (y=0 is the top) that is within the first few
    rows.  top_fraction=0.35 gives a generous band that covers the badge
    regardless of window height while excluding the bottom-centre cue badges.
    """
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        log.error("Pillow not installed; cannot check badge pixels")
        return False
    try:
        img = Image.open(png_path).convert("RGB")
    except Exception:
        log.exception("could not open %s", png_path)
        return False
    w, h = img.size
    band = int(h * top_fraction)
    px = img.load()
    hits = 0
    for y in range(0, band):
        for x in range(0, w):
            r, g, b = px[x, y]
            if (
                abs(r - _BADGE_RGB[0]) < _BADGE_TOLERANCE
                and abs(g - _BADGE_RGB[1]) < _BADGE_TOLERANCE
                and abs(b - _BADGE_RGB[2]) < _BADGE_TOLERANCE
            ):
                hits += 1
    log.info("badge pixel hits in top %.0f%% of %s: %d", top_fraction * 100, png_path.name, hits)
    return hits > 5


# ---------------------------------------------------------------------------
# Window discovery — variant of sprite_dim_e2e._find_window_by_pid that also
# searches child PIDs.  On Windows, when subprocess.Popen redirects stdio
# (stdout=DEVNULL), the venv python.exe launcher forks a second python.exe
# child that actually runs the code and owns the pyglet window.  The harness
# gets the PARENT pid from Popen.pid, but the window belongs to the CHILD.
# We fix this by building the full PID set = {parent} ∪ {direct children}
# before each EnumWindows sweep.
# ---------------------------------------------------------------------------


def _get_child_pids(parent_pid: int) -> set[int]:
    """Return the set of direct child PIDs of *parent_pid* via the Toolhelp32 snapshot API."""
    try:
        # Use a snapshot via TH32CS_SNAPPROCESS to enumerate children.
        import ctypes
        TH32CS_SNAPPROCESS = 0x00000002

        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", ctypes.c_ulong),
                ("cntUsage", ctypes.c_ulong),
                ("th32ProcessID", ctypes.c_ulong),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", ctypes.c_ulong),
                ("cntThreads", ctypes.c_ulong),
                ("th32ParentProcessID", ctypes.c_ulong),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", ctypes.c_ulong),
                ("szExeFile", ctypes.c_char * 260),
            ]

        kernel32 = ctypes.windll.kernel32
        snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap == ctypes.c_void_p(-1).value:
            return set()
        try:
            pe = PROCESSENTRY32()
            pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
            children: set[int] = set()
            if kernel32.Process32First(snap, ctypes.byref(pe)):
                while True:
                    if pe.th32ParentProcessID == parent_pid:
                        children.add(pe.th32ProcessID)
                    if not kernel32.Process32Next(snap, ctypes.byref(pe)):
                        break
            return children
        finally:
            kernel32.CloseHandle(snap)
    except Exception:
        return set()


def _find_window_by_pid(pid: int, timeout_s: float = 10.0) -> int:
    """Find a visible non-zero-size window owned by *pid* or any of its children."""
    import win32gui  # type: ignore
    import win32process  # type: ignore

    deadline = time.monotonic() + timeout_s
    found: list[int] = []

    while time.monotonic() < deadline:
        found.clear()
        # Rebuild the PID set each sweep so we catch children that spawn late.
        pid_set = {pid} | _get_child_pids(pid)

        def _cb(hwnd: int, _: Any) -> bool:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            _, wpid = win32process.GetWindowThreadProcessId(hwnd)
            if wpid in pid_set:
                rect = win32gui.GetWindowRect(hwnd)
                if (rect[2] - rect[0]) > 0 and (rect[3] - rect[1]) > 0:
                    found.append(hwnd)
            return True

        win32gui.EnumWindows(_cb, None)
        if found:
            log.info("found sprite window hwnd=%d (pid_set=%s)", found[0], pid_set)
            return int(found[0])
        time.sleep(0.15)
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    port_val = __import__("socket").socket()
    port_val.bind(("127.0.0.1", 0))
    port = port_val.getsockname()[1]
    port_val.close()

    log.info("=== mode_visual_e2e start ===")
    log.info("fake SSE server on port %d", port)

    srv = _start_sse_server(port)
    cfg_path = _write_temp_config(port)
    log.info("sprite config: %s", cfg_path)

    # Force the subprocess to import voice_sprite from THIS worktree's src/,
    # not whatever path the editable install's .pth points at.  Without this,
    # a sprite launched from a git worktree silently runs the main working
    # copy's code.  Prepending src/ to PYTHONPATH makes the local tree win —
    # copied verbatim from sprite_dim_e2e.py.
    env = dict(os.environ)
    src_dir = str(ROOT / "src")
    env["PYTHONPATH"] = (
        src_dir + os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else src_dir
    )

    sprite_proc = subprocess.Popen(
        [sys.executable, "-m", "voice_sprite", "--config", str(cfg_path)],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        env=env,
    )
    log.info("sprite pid=%d", sprite_proc.pid)

    ok = False
    try:
        # ----------------------------------------------------------------
        # Wait for SSE connection
        # ----------------------------------------------------------------
        if not srv.connected.wait(timeout=10.0):  # type: ignore[attr-defined]
            log.error("sprite never connected to fake SSE server")
            return 1
        log.info("sprite SSE connected")

        # ----------------------------------------------------------------
        # Discover sprite window HWND
        # ----------------------------------------------------------------
        hwnd = _find_window_by_pid(sprite_proc.pid, timeout_s=10.0)
        if not hwnd:
            log.error("sprite window not found within timeout")
            return 1
        log.info("sprite hwnd=%d", hwnd)

        # ----------------------------------------------------------------
        # Settle the sprite into LISTENING state (mirrors sprite_dim_e2e)
        # ----------------------------------------------------------------
        _emit(srv, "warmup_done", {})
        time.sleep(0.5)
        _emit(srv, "session_started", {})
        time.sleep(1.0)

        # ----------------------------------------------------------------
        # Phase 1 — emit mode.enter, capture badge-ON screenshot
        # ----------------------------------------------------------------
        log.info("emitting mode.enter {name:video, badge:'🎬 VIDEO'}")
        _emit(srv, "mode.enter", {"name": "video", "badge": "🎬 VIDEO"})
        time.sleep(1.2)  # allow pyglet to render the new label

        if not _capture_window(hwnd, BADGE_ON_PNG):
            log.error("capture failed for badge-ON phase")
            return 1
        log.info("captured badge-ON → %s", BADGE_ON_PNG)

        badge_on = _has_badge_pixels(BADGE_ON_PNG)
        if not badge_on:
            log.error(
                "FAIL: mode badge NOT visible after mode.enter — "
                "no green (120,220,140) pixels in top 35%% of %s",
                BADGE_ON_PNG,
            )
            return 1
        log.info("badge-ON assertion PASSED")

        # ----------------------------------------------------------------
        # Phase 2 — emit mode.exit, capture badge-OFF screenshot
        # ----------------------------------------------------------------
        log.info("emitting mode.exit {name:video, reason:end_phrase}")
        _emit(srv, "mode.exit", {"name": "video", "reason": "end_phrase"})
        time.sleep(1.2)  # allow pyglet to clear the label

        if not _capture_window(hwnd, BADGE_OFF_PNG):
            log.error("capture failed for badge-OFF phase")
            return 1
        log.info("captured badge-OFF → %s", BADGE_OFF_PNG)

        badge_off = _has_badge_pixels(BADGE_OFF_PNG)
        if badge_off:
            log.error(
                "FAIL: mode badge STILL visible after mode.exit — "
                "green pixels still present in top 35%% of %s",
                BADGE_OFF_PNG,
            )
            return 1
        log.info("badge-OFF assertion PASSED")

        ok = True
        print("PASS: mode badge shown on enter, cleared on exit")
        return 0

    except AssertionError as exc:
        log.error("AssertionError: %s", exc)
        print(f"FAIL: {exc}")
        return 1

    finally:
        # ----------------------------------------------------------------
        # Teardown — copied from picker_visual_e2e / sprite_dim_e2e
        # ----------------------------------------------------------------
        srv.shutdown_flag.set()  # type: ignore[attr-defined]
        srv.queue.put(None)  # type: ignore[attr-defined]
        srv.shutdown()
        try:
            sprite_proc.terminate()
            sprite_proc.wait(timeout=5.0)
        except Exception:
            sprite_proc.kill()
        log.info("=== mode_visual_e2e %s ===", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    sys.exit(main())
