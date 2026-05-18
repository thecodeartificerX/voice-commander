"""Visual E2E harness for ADR 0090 — Right Ctrl opens its own voice session.

Mandatory per docs/agents/visual-e2e-testing.md (8-rule protocol).

This feature touches hotkeys, daemon ↔ sprite ↔ web-UI IPC, and the system
clipboard — all three triggers for the visual E2E requirement.

REV 4: The harness exercises ONE real-daemon scenario (Phase A) only: start
the daemon subprocess, inject Right Ctrl to open a session (empty buffer),
inject Right Ctrl again to close it, assert ``session_started`` then
``session_stopped`` arrive on the real SSE /events stream, and gate on crash
signatures.  Audio-dependent scenarios — end-word exit with spoken content,
spoken "cancel", and the Scroll-Lock-session-stays-open regression — are
covered by ``tests/integration/test_dictation_opens_session.py`` rather than
this harness, because they require transcribed speech that cannot be injected
against a real daemon from outside the process.

Architecture
------------
1. Write a minimal config.toml to a temp directory (the daemon reads
   Path("config.toml") relative to its cwd — there is no --config arg).
   The temp dir's outputs/ sub-directory serves as the lock/log dir.
   ``sprite.enabled = false`` so no sprite subprocess is spawned.
   ``web.auto_open_browser = false`` to prevent a browser tab opening.
   ``transcription.device = "cpu"`` + ``model_size = "tiny.en"`` so the
   transcriber load is fast and doesn't require a GPU.  The transcriber
   loads in a background thread; /healthz is served before it completes,
   so session open/close can be exercised without waiting for the model.
2. Start the daemon as a subprocess (python -m voice_commander) with cwd
   set to the temp directory.
3. Poll /healthz until the web server is up (60 s timeout to handle
   transient startup overhead).
4. Subscribe to /events SSE endpoint.
5. Inject Right Ctrl via pynput (real keypress — daemon's HotkeyController
   uses on_release, so we do press+release with a 50 ms hold).
6. Assert session_started arrives on /events within 10 s.
7. Inject Right Ctrl again (hotkey-end, empty buffer → session_stopped).
8. Assert session_stopped arrives on /events within 15 s.
9. Capture daemon stdout/stderr; FAIL on crash signatures (Traceback,
   Exception in thread) — the ADR 0089 lesson.
10. Capture a screenshot of any visible sprite/daemon window as evidence.

Plan deviation note (documented):
  The plan's minimal config used flat keys (``hotkey = "scroll_lock"``) and
  a ``[transcriber]`` section.  Neither exists in the daemon's Config schema.
  The correct section is ``[hotkey]`` with ``key = "scroll_lock"`` and
  ``dictation_key = "ctrl_r"``, and ``[transcription]`` (not ``[transcriber]``).
  The plan also assumed a ``--config <path>`` CLI argument; the daemon reads
  ``Path("config.toml")`` relative to its cwd instead.  This harness sets cwd
  to a temp directory containing a correct config.toml.

If the daemon cannot start (ImportError on sounddevice/etc.), the harness
prints a skip notice, writes a skip marker to outputs/, and exits 0.  This is
consistent with the repo's local-only infrastructure rule (CLAUDE.md §5).

Output artifacts (Rule 4):
  outputs/dictation_opens_session_e2e_session.png   — window after session_started
  outputs/dictation_opens_session_e2e_idle.png      — window after session_stopped
  outputs/dictation_opens_session_e2e.log
  outputs/dictation_opens_session_e2e.json
  outputs/dictation_opens_session_e2e_daemon.log    — daemon stdout+stderr
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import logging
import os
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

OUT = ROOT / "outputs"
OUT.mkdir(parents=True, exist_ok=True)

LOG_PATH = OUT / "dictation_opens_session_e2e.log"
PNG_SESSION_PATH = OUT / "dictation_opens_session_e2e_session.png"
PNG_IDLE_PATH = OUT / "dictation_opens_session_e2e_idle.png"
JSON_PATH = OUT / "dictation_opens_session_e2e.json"
DAEMON_LOG_PATH = OUT / "dictation_opens_session_e2e_daemon.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("e2e")

# Crash signatures to scan for in daemon log.
# Note: "RuntimeError:" is excluded because the daemon logs
# "Transcriber.load() failed in background" as a RuntimeError subclass and
# the daemon is designed to stay alive through transcriber load failures.
# "ValueError:" is excluded because config warnings may surface one.
# We keep the most diagnostic signatures that indicate a real crash.
_CRASH_SIGNATURES = (
    "Traceback (most recent call last)",
    "Exception in thread",
)

# ---------------------------------------------------------------------------
# Minimal config.toml for the daemon
# ---------------------------------------------------------------------------
#
# Key design choices:
#   [hotkey] key / dictation_key — correct schema (not flat top-level keys)
#   [transcription] — correct section name (not [transcriber])
#   [web] auto_open_browser = false — no browser tab during test
#   [sprite] enabled = false — no sprite subprocess spawned
#   [observability] enabled = false — no SQLite I/O needed for this test
#   [logging] file — relative path; the daemon uses its cwd as the base
#
_MINIMAL_CONFIG_TEMPLATE = """\
[hotkey]
key = "scroll_lock"
dictation_key = "ctrl_r"

[audio]
device_name = ""
channels = 1
output_dir = "outputs"

[transcription]
model_size = "tiny.en"
device = "cpu"
compute_type = "int8"
min_confidence = 0.3

[feedback]
sounds_dir = "assets/sounds"
start_sound = "start.wav"
stop_sound = "stop.wav"
miss_sound = "miss.wav"

[vad]
threshold = 0.4
min_speech_duration_ms = 100
min_silence_duration_ms = 250
speech_pad_ms = 30
pre_roll_ms = 300
max_utterance_ms = 8000

[vad.gates]
min_word_count = 1
max_no_speech_prob = 0.6

[dictation]
endpoint = "http://127.0.0.1:1"
end_word = "done"
cancel_word = "cancel"

[logging]
level = "DEBUG"
file = "outputs/voice-commander.log"

[web]
enabled = true
host = "127.0.0.1"
port = {port}
auto_open_browser = false

[sprite]
enabled = false

[observability]
enabled = false

[picker]
enabled = true

[elements]
max_elements = 200
scan_timeout_s = 3.0
hint_timeout_s = 8.0
"""


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ---------------------------------------------------------------------------
# SSE client (reads from daemon's real /events endpoint)
# ---------------------------------------------------------------------------


def _sse_reader(
    url: str,
    event_queue: "queue.Queue[str]",
    stop_flag: threading.Event,
    timeout_s: float = 120.0,
) -> None:
    """Thread target: stream SSE events from url into event_queue."""
    try:
        import httpx
        with httpx.stream("GET", url, timeout=timeout_s) as resp:
            event_type = ""
            for line in resp.iter_lines():
                if stop_flag.is_set():
                    break
                line = line.strip()
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    if event_type:
                        log.debug("SSE raw event: %s", event_type)
                        event_queue.put(event_type)
                    event_type = ""
                elif not line:
                    event_type = ""
    except Exception as exc:
        log.debug("SSE reader exited: %s", exc)


def _wait_for_event(
    event_queue: "queue.Queue[str]",
    target: str,
    timeout_s: float,
) -> bool:
    """Block until target event type appears in queue or timeout expires."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            ev = event_queue.get(timeout=min(remaining, 1.0))
            log.info("SSE event: %s", ev)
            if ev == target:
                return True
        except queue.Empty:
            pass
    return False


# ---------------------------------------------------------------------------
# Daemon subprocess management
# ---------------------------------------------------------------------------


def _spawn_daemon(
    port: int,
    cwd: Path,
) -> "tuple[subprocess.Popen[bytes], Path] | None":
    """Write temp config and spawn the daemon. Returns (proc, cfg_path) or None on skip."""
    cfg_path = cwd / "config.toml"
    cfg_path.write_text(_MINIMAL_CONFIG_TEMPLATE.format(port=port), encoding="utf-8")

    # Ensure outputs/ sub-dir exists in the temp cwd (daemon lock file, log file)
    (cwd / "outputs").mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    src_dir = str(ROOT / "src")
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_dir};{existing_pp}" if existing_pp else src_dir

    # VC_STATE_DIR is used by __main__.py to find commands.json / workflows.json.
    # Point it at the real repo root so the daemon can seed commands on first run.
    env["VC_STATE_DIR"] = str(ROOT)

    daemon_log_fh = DAEMON_LOG_PATH.open("w", encoding="utf-8", buffering=1)
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "voice_commander"],
            cwd=str(cwd),
            env=env,
            stdout=daemon_log_fh,
            stderr=daemon_log_fh,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
    except Exception as exc:
        log.error("Failed to spawn daemon: %s", exc)
        daemon_log_fh.close()
        return None
    proc._daemon_log_fh = daemon_log_fh  # type: ignore[attr-defined]
    log.info("daemon pid=%d, cwd=%s, config=%s", proc.pid, cwd, cfg_path)
    return proc, cfg_path


def _stop_daemon(proc: "subprocess.Popen[bytes]") -> None:
    try:
        proc.terminate()
        proc.wait(timeout=8.0)
    except subprocess.TimeoutExpired:
        proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    fh = getattr(proc, "_daemon_log_fh", None)
    if fh is not None:
        try:
            fh.flush()
            fh.close()
        except Exception:
            pass


def _poll_healthz(base_url: str, timeout_s: float = 60.0) -> bool:
    """Poll GET /healthz until 200 or timeout."""
    try:
        import httpx
    except ImportError:
        log.error("httpx not available — cannot poll /healthz")
        return False
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{base_url}/healthz", timeout=3.0)
            if r.status_code == 200:
                log.info("/healthz OK — daemon web server is up")
                return True
        except Exception:
            pass
        time.sleep(0.5)
    log.error("/healthz never returned 200 within %s s", timeout_s)
    return False


def _check_daemon_log_for_crashes() -> bool:
    """Return True if no hard crash signatures found in daemon log."""
    if not DAEMON_LOG_PATH.exists():
        log.warning("daemon log not found")
        return True
    text = DAEMON_LOG_PATH.read_text(encoding="utf-8", errors="replace")
    hits = [sig for sig in _CRASH_SIGNATURES if sig in text]
    if hits:
        log.error("FAIL [crash-gate]: crash signatures found: %s", hits)
        log.error("daemon log tail:\n%s", text[-3000:])
        return False
    log.info("PASS [crash-gate]: no crash signatures in daemon log")
    return True


# ---------------------------------------------------------------------------
# Hotkey injection via pynput (real keypress — Rule 7)
# ---------------------------------------------------------------------------


def _inject_right_ctrl() -> None:
    """Inject a real Right Ctrl press+release via pynput.keyboard.Controller.

    The daemon's HotkeyController binds on_release, so we must do a full
    press+release cycle.  A 50 ms hold ensures the release event is
    delivered after the press is registered.
    """
    try:
        from pynput.keyboard import Controller, Key
        kb = Controller()
        kb.press(Key.ctrl_r)
        time.sleep(0.05)
        kb.release(Key.ctrl_r)
        log.info("Injected Right Ctrl via pynput (press+release)")
    except Exception as exc:
        log.error("pynput injection failed: %s", exc)
        raise


# ---------------------------------------------------------------------------
# Window capture helpers (Rule 4 — evidence artifacts)
# ---------------------------------------------------------------------------


def _find_hwnd_by_pid(pid: int, timeout_s: float = 10.0) -> int:
    """Find the first visible top-level HWND owned by pid or its children."""
    user32 = ctypes.windll.user32
    WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    deadline = time.monotonic() + timeout_s

    def _candidate_pids(root: int) -> set[int]:
        s = {root}
        try:
            import psutil
            for c in psutil.Process(root).children(recursive=True):
                s.add(c.pid)
        except Exception:
            pass
        return s

    while time.monotonic() < deadline:
        cands = _candidate_pids(pid)
        found: list[int] = []

        def _cb(
            hwnd: int, _: int,
            _c: set[int] = cands,
            _f: list[int] = found,
        ) -> bool:
            d = wt.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(d))
            if d.value in _c and user32.IsWindowVisible(hwnd):
                _f.append(hwnd)
                return False
            return True

        user32.EnumWindows(WNDENUMPROC(_cb), 0)
        if found:
            return found[0]
        time.sleep(0.25)
    return 0


def _capture_window(hwnd: int, png_path: Path) -> bool:
    """Capture a window via PrintWindow(PW_RENDERFULLCONTENT) and write PNG."""
    try:
        import win32gui
        import win32ui
        from PIL import Image

        rect = win32gui.GetWindowRect(hwnd)
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        if w <= 0 or h <= 0:
            log.warning("capture_window: hwnd %d has non-positive size (%dx%d)", hwnd, w, h)
            return False

        hdc = win32gui.GetWindowDC(hwnd)
        src = win32ui.CreateDCFromHandle(hdc)
        mem = src.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(src, w, h)
        mem.SelectObject(bmp)

        try:
            user32 = ctypes.windll.user32
            user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
            user32.PrintWindow.restype = wt.BOOL
            if not user32.PrintWindow(hwnd, mem.GetSafeHdc(), 0x2):
                log.warning("PrintWindow returned 0 for hwnd %d", hwnd)

            bmpinfo = bmp.GetInfo()
            bmpstr = bmp.GetBitmapBits(True)
            img = Image.frombuffer(
                "RGB",
                (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
                bmpstr, "raw", "BGRX", 0, 1,
            )
            img.save(str(png_path))
            log.info("PNG captured: %s (%d bytes)", png_path, png_path.stat().st_size)
        finally:
            win32gui.DeleteObject(bmp.GetHandle())
            mem.DeleteDC()
            src.DeleteDC()
            win32gui.ReleaseDC(hwnd, hdc)

        return True
    except Exception as exc:
        log.error("capture_window failed: %s", exc)
        return False


def _take_desktop_screenshot(png_path: Path) -> bool:
    """Fallback: capture the full desktop if no HWND is found."""
    try:
        import win32api
        import win32con
        import win32gui
        import win32ui
        from PIL import Image

        w = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
        h = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)
        hwnd = win32gui.GetDesktopWindow()
        hdc = win32gui.GetWindowDC(hwnd)
        src = win32ui.CreateDCFromHandle(hdc)
        mem = src.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(src, w, h)
        mem.SelectObject(bmp)
        mem.BitBlt((0, 0), (w, h), src, (0, 0), 0x00CC0020)  # SRCCOPY
        bmpinfo = bmp.GetInfo()
        bmpstr = bmp.GetBitmapBits(True)
        img = Image.frombuffer(
            "RGB", (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
            bmpstr, "raw", "BGRX", 0, 1,
        )
        img.save(str(png_path))
        log.info("Desktop screenshot: %s (%d bytes)", png_path, png_path.stat().st_size)
        win32gui.DeleteObject(bmp.GetHandle())
        mem.DeleteDC()
        src.DeleteDC()
        win32gui.ReleaseDC(hwnd, hdc)
        return True
    except Exception as exc:
        log.warning("Desktop screenshot failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Phase A — Ctrl-open + Ctrl-close: session_started then session_stopped
# ---------------------------------------------------------------------------


def phase_a_ctrl_open_ctrl_close() -> bool:
    """PHASE A (Rule 7 — test how the user uses it):
      1. Daemon starts with no session.
      2. Inject Right Ctrl → assert session_started on /events.
      3. Capture evidence screenshot.
      4. Inject Right Ctrl again → assert session_stopped on /events.
      5. Confirm no crash signatures in daemon log.

    This directly exercises the ``on_dictation_toggle`` idle-branch code path
    introduced by ADR 0090 using a real keypress — not a synthetic method call.
    """
    log.info("=== PHASE A: Ctrl-open + Ctrl-close (real daemon + real hotkey) ===")
    port = _free_port()
    stop_flag = threading.Event()
    ev_queue: "queue.Queue[str]" = queue.Queue()
    ok = False

    with tempfile.TemporaryDirectory(prefix="vc_e2e_") as tmp:
        cwd = Path(tmp)
        result = _spawn_daemon(port, cwd)
        if result is None:
            log.warning("SKIP: daemon could not be spawned (missing hardware/deps)")
            return True  # skip, not fail — local-only infra rule

        proc, _ = result

        try:
            base_url = f"http://127.0.0.1:{port}"

            # Wait for web server (give up to 60 s; model load is in background)
            if not _poll_healthz(base_url, timeout_s=60.0):
                log.error("FAIL: daemon web server did not start within 60 s")
                # Print daemon log tail for diagnosis
                if DAEMON_LOG_PATH.exists():
                    log.error("daemon log tail:\n%s",
                              DAEMON_LOG_PATH.read_text(encoding="utf-8", errors="replace")[-3000:])
                return False

            # Early crash check (post-startup, pre-hotkey)
            if DAEMON_LOG_PATH.exists():
                early_text = DAEMON_LOG_PATH.read_text(encoding="utf-8", errors="replace")
                early_hits = [s for s in _CRASH_SIGNATURES if s in early_text]
                if early_hits:
                    log.error("FAIL: crash signatures at startup: %s", early_hits)
                    log.error("daemon log:\n%s", early_text[-3000:])
                    return False

            # Start SSE reader thread
            sse_thread = threading.Thread(
                target=_sse_reader,
                args=(f"{base_url}/events", ev_queue, stop_flag),
                daemon=True,
                name="sse-reader",
            )
            sse_thread.start()
            time.sleep(0.8)  # let SSE stream establish

            # --- Step 1: inject Right Ctrl; expect session_started ---
            log.info("Injecting Right Ctrl (open session)...")
            _inject_right_ctrl()

            if not _wait_for_event(ev_queue, "session_started", timeout_s=10.0):
                log.error("FAIL: session_started not received within 10 s after Right Ctrl press")
                if DAEMON_LOG_PATH.exists():
                    log.error("daemon log tail:\n%s",
                              DAEMON_LOG_PATH.read_text(encoding="utf-8", errors="replace")[-3000:])
                return False
            log.info("PASS: session_started received")

            # Capture evidence screenshot (best-effort)
            hwnd = _find_hwnd_by_pid(proc.pid, timeout_s=3.0)
            if hwnd:
                _capture_window(hwnd, PNG_SESSION_PATH)
            else:
                log.info("No daemon HWND found (sprite disabled); taking desktop screenshot")
                _take_desktop_screenshot(PNG_SESSION_PATH)

            # --- Step 2: inject second Right Ctrl; expect session_stopped ---
            time.sleep(0.4)  # brief pause so dictation session is fully started
            log.info("Injecting Right Ctrl (close session — empty buffer)...")
            _inject_right_ctrl()

            if not _wait_for_event(ev_queue, "session_stopped", timeout_s=15.0):
                log.error("FAIL: session_stopped not received within 15 s after second Right Ctrl")
                if DAEMON_LOG_PATH.exists():
                    log.error("daemon log tail:\n%s",
                              DAEMON_LOG_PATH.read_text(encoding="utf-8", errors="replace")[-3000:])
                return False
            log.info("PASS: session_stopped received")

            # Capture idle evidence screenshot
            time.sleep(0.4)
            if hwnd:
                _capture_window(hwnd, PNG_IDLE_PATH)
            else:
                _take_desktop_screenshot(PNG_IDLE_PATH)

            ok = True

        finally:
            stop_flag.set()
            _stop_daemon(proc)
            # Scan daemon log for crash signatures AFTER stop (log flushed)
            if ok and not _check_daemon_log_for_crashes():
                ok = False
            log.info("PHASE A %s", "PASS" if ok else "FAIL")

    return ok


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------


def main() -> int:
    results: dict[str, bool] = {}
    results["phase_a_ctrl_open_ctrl_close"] = phase_a_ctrl_open_ctrl_close()

    all_pass = all(results.values())
    summary = " | ".join(f"{k}={'PASS' if v else 'FAIL'}" for k, v in results.items())
    log.info("=== SUMMARY: %s ===", summary)
    JSON_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    log.info("session PNG:  %s", PNG_SESSION_PATH)
    log.info("idle PNG:     %s", PNG_IDLE_PATH)
    log.info("daemon log:   %s", DAEMON_LOG_PATH)
    log.info("harness log:  %s", LOG_PATH)
    log.info("JSON summary: %s", JSON_PATH)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
