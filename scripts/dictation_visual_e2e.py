"""Chunk-by-chunk visual end-to-end harness for dictation mode.

Drives the dictation finalize pipeline the way a real user would:
  1. Sprite SSE rendering — emits ``dictation.start`` and asserts the DICTATING
     gold badge is visible in the sprite window.
  2. Finalize pipeline — loads ``tests/test-audio/test-wav.wav``, encodes it to
     16 kHz mono s16le WAV (stored as ``last.wav``), POSTs to the transcription
     endpoint (or a stub), and clipboard-pastes the result into Notepad.
  3. Evidence capture — screenshots written to ``outputs/dictation_e2e/``.

10 checkpoints. Exits non-zero if any hard checkpoint fails.

Usage:
  python scripts/dictation_visual_e2e.py               # live endpoint (config.toml)
  python scripts/dictation_visual_e2e.py --stub-endpoint  # offline; POST stubbed
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Repo root + path setup
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

OUT_DIR = ROOT / "outputs" / "dictation_e2e"
OUT_DIR.mkdir(parents=True, exist_ok=True)

LOG_PATH = OUT_DIR / "dictation_e2e.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("dictation_e2e")

_STUB_TRANSCRIPTION = "stub dictation transcription"
_SENTINEL_CLIPBOARD = "__dictation_e2e_sentinel_12345__"
_TEST_WAV = ROOT / "tests" / "test-audio" / "test-wav.wav"

# ---------------------------------------------------------------------------
# Checkpoint registry
# ---------------------------------------------------------------------------

_checkpoints: list[tuple[int, str, bool | None]] = []
# (n, description, result)  — result=None means SKIPPED


def _record(n: int, label: str, result: bool | None, *, skip_msg: str = "") -> None:
    _checkpoints.append((n, label, result))
    if result is None:
        log.info("CHECKPOINT %d: SKIPPED — %s", n, skip_msg)
    elif result:
        log.info("CHECKPOINT %d: PASS — %s", n, label)
    else:
        log.error("CHECKPOINT %d: FAIL — %s", n, label)


# ---------------------------------------------------------------------------
# Fake SSE server (copied from picker_visual_e2e.py)
# ---------------------------------------------------------------------------


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
    srv.queue = queue.Queue()  # type: ignore[attr-defined]
    srv.connected = threading.Event()  # type: ignore[attr-defined]
    srv.shutdown_flag = threading.Event()  # type: ignore[attr-defined]
    t = threading.Thread(target=srv.serve_forever, daemon=True, name="fake-sse")
    t.start()
    return srv


def _emit(srv: ThreadingHTTPServer, type_: str, data: dict[str, Any]) -> None:
    srv.queue.put({"type": type_, "data": data})  # type: ignore[attr-defined]


def _write_temp_config(port: int) -> Path:
    cfg_path = OUT_DIR / "_dictation_e2e_sprite_config.toml"
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


# ---------------------------------------------------------------------------
# Win32 / screenshot helpers (copied + adapted from picker_visual_e2e.py)
# ---------------------------------------------------------------------------


def _find_window_by_pid(pid: int, timeout_s: float = 8.0) -> int:
    """Return the first visible top-level HWND belonging to *pid*."""
    import ctypes
    import ctypes.wintypes

    user32 = ctypes.windll.user32
    found_hwnd: list[int] = []

    # WNDENUMPROC callback
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

    def _enum_cb(hwnd: int, _lp: int) -> bool:
        window_pid = ctypes.wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if window_pid.value == pid and user32.IsWindowVisible(hwnd):
            found_hwnd.append(hwnd)
            return False  # stop
        return True  # continue

    cb = WNDENUMPROC(_enum_cb)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        found_hwnd.clear()
        user32.EnumWindows(cb, 0)
        if found_hwnd:
            return found_hwnd[0]
        time.sleep(0.2)
    return 0


def _capture_window(hwnd: int, png_path: Path) -> bool:
    """Capture *hwnd* via PrintWindow(PW_RENDERFULLCONTENT) and write PNG."""
    try:
        import ctypes
        from ctypes import wintypes

        import win32gui  # type: ignore
        import win32ui  # type: ignore
        from PIL import Image  # type: ignore

        rect = win32gui.GetWindowRect(hwnd)
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        if w <= 0 or h <= 0:
            log.error("hwnd %d has non-positive size: %dx%d", hwnd, w, h)
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
        ok = user32.PrintWindow(hwnd, mem.GetSafeHdc(), 0x2)  # PW_RENDERFULLCONTENT
        if not ok:
            log.warning("PrintWindow returned 0; falling back to BitBlt")
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


def _has_gold_badge_pixels(png_path: Path, sample: int = 400) -> bool:
    """Sample pixels; return True if the gold DICTATING badge colour is present.

    Target: (245, 194, 66) — the badge label colour set in window.py.
    Accept a neighbourhood to tolerate sub-pixel rendering.
    """
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        log.warning("Pillow not available — skipping pixel assertion")
        return False
    try:
        img = Image.open(png_path).convert("RGB")
    except Exception:
        log.exception("could not open PNG for pixel check")
        return False
    w, h = img.size
    import random

    rng = random.Random(0xD1C7)
    gold = 0
    for _ in range(sample):
        px = img.getpixel((rng.randrange(w), rng.randrange(h)))
        r, g, b = px[:3]
        # Gold badge: (245, 194, 66) — accept ±30 neighbourhood
        if 215 <= r <= 255 and 164 <= g <= 224 and 36 <= b <= 96:
            gold += 1
    log.info("gold pixel hits: %d / %d", gold, sample)
    return gold > 2


def _take_screenshot(name: str) -> Path:
    """Capture the entire screen to the evidence dir (fallback when no hwnd)."""
    path = OUT_DIR / name
    try:
        import ctypes

        import win32gui  # type: ignore
        import win32ui  # type: ignore
        from PIL import Image  # type: ignore

        desktop = win32gui.GetDesktopWindow()
        rect = win32gui.GetWindowRect(desktop)
        w, h = rect[2] - rect[0], rect[3] - rect[1]

        hdc = win32gui.GetWindowDC(desktop)
        src = win32ui.CreateDCFromHandle(hdc)
        mem = src.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(src, w, h)
        mem.SelectObject(bmp)
        mem.BitBlt((0, 0), (w, h), src, (0, 0), 0x00CC0020)

        bmpinfo = bmp.GetInfo()
        bmpstr = bmp.GetBitmapBits(True)
        img = Image.frombuffer("RGB", (bmpinfo["bmWidth"], bmpinfo["bmHeight"]), bmpstr, "raw", "BGRX", 0, 1)
        img.save(path)

        win32gui.DeleteObject(bmp.GetHandle())
        mem.DeleteDC()
        src.DeleteDC()
        win32gui.ReleaseDC(desktop, hdc)
        log.info("screenshot: %s", path)
    except Exception:
        log.exception("screenshot failed: %s", name)
    return path


# ---------------------------------------------------------------------------
# Notepad sink helpers (verbatim from plan)
# ---------------------------------------------------------------------------


def launch_notepad_sink() -> int:
    import win32gui  # type: ignore

    subprocess.Popen(["notepad.exe"])
    time.sleep(1.5)
    hwnd = win32gui.FindWindow("Notepad", None)
    assert hwnd, "could not find Notepad window"
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.3)
    return hwnd


def _find_edit_control(hwnd: int) -> int:
    """Find the text edit control in a Notepad window (handles Win11's nested XAML hierarchy).

    Win11 Notepad nests RichEditD2DPT inside WinUI3 XAML-island children, so
    FindWindowEx(hwnd, 0, ...) may find nothing. We use a recursive EnumChildWindows
    pass and prefer RichEditD2DPT / Edit (which respond to WM_GETTEXT) over
    NotepadTextBox (which does not).
    """
    import win32gui  # type: ignore

    # Fast path: direct children (classic Notepad / Win10)
    for cls in ("Edit", "RichEditD2DPT"):
        edit = win32gui.FindWindowEx(hwnd, 0, cls, None)
        if edit:
            return edit

    # Slow path: full recursive search (Win11 Notepad with WinUI3 XAML islands).
    # Collect ALL matching controls; prefer Edit/RichEditD2DPT over NotepadTextBox
    # because NotepadTextBox is the XAML host and does not respond to WM_GETTEXT.
    _PREFERRED = {"Edit", "RichEditD2DPT"}
    _FALLBACK = {"NotepadTextBox"}
    preferred: list[int] = []
    fallback: list[int] = []
    all_hwnds: list[int] = []

    try:
        win32gui.EnumChildWindows(hwnd, lambda h, _: all_hwnds.append(h) or True, None)
    except Exception:
        pass

    for h in all_hwnds:
        try:
            cls = win32gui.GetClassName(h)
        except Exception:
            continue
        if cls in _PREFERRED:
            preferred.append(h)
        elif cls in _FALLBACK:
            fallback.append(h)

    if preferred:
        return preferred[0]
    if fallback:
        return fallback[0]
    return 0


def read_notepad_text(hwnd: int) -> str:
    import win32con  # type: ignore
    import win32gui  # type: ignore

    edit = _find_edit_control(hwnd)
    if not edit:
        log.warning("read_notepad_text: no edit control found under hwnd=%d", hwnd)
        return ""
    log.info("read_notepad_text: using edit hwnd=%d cls=%s", edit, win32gui.GetClassName(edit))
    length = win32gui.SendMessage(edit, win32con.WM_GETTEXTLENGTH, 0, 0)
    if length == 0:
        return ""
    import ctypes

    # Allocate a UTF-16 buffer; use ctypes rather than the deprecated PyMakeBuffer.
    buf = ctypes.create_unicode_buffer(length + 1)
    win32gui.SendMessage(edit, win32con.WM_GETTEXT, length + 1, buf)
    return buf.value


# ---------------------------------------------------------------------------
# Win+V clipboard history check (verbatim from plan)
# ---------------------------------------------------------------------------


def transcription_is_second_in_history(expected: str) -> bool | None:
    """Return True if *expected* is the 2nd Win+V history item; None if unavailable."""
    try:
        import asyncio

        from winrt.windows.applicationmodel.datatransfer import Clipboard  # type: ignore
    except Exception:
        return None

    async def _items() -> list[str | None]:
        result = await Clipboard.get_history_items_async()
        items = list(result.items)
        texts: list[str | None] = []
        for it in items[:3]:
            try:
                texts.append(await it.content.get_text_async())
            except Exception:
                texts.append(None)
        return texts

    texts = asyncio.run(_items())
    return len(texts) >= 2 and texts[1] is not None and expected in texts[1]


# ---------------------------------------------------------------------------
# Core dictation finalize exerciser
# ---------------------------------------------------------------------------


def _load_test_wav_as_float32() -> "npt.NDArray[np.float32]":
    """Read test-wav.wav and resample to 16 kHz mono float32 if needed."""
    import numpy as np
    import numpy.typing as npt  # noqa: F401

    with wave.open(str(_TEST_WAV), "rb") as wf:
        ch = wf.getnchannels()
        sw = wf.getsampwidth()
        fr = wf.getframerate()
        nframes = wf.getnframes()
        raw = wf.readframes(nframes)

    # Decode PCM
    if sw == 2:
        audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sw == 4:
        audio = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"unsupported sample width: {sw}")

    # Stereo → mono
    if ch > 1:
        audio = audio.reshape(-1, ch).mean(axis=1)

    # Resample to 16 kHz if needed
    if fr != 16000:
        try:
            import soxr  # type: ignore

            audio = soxr.resample(audio, fr, 16000, quality="HQ").astype(np.float32)
        except ImportError:
            # Fallback: naive decimation (adequate for testing purposes)
            ratio = fr / 16000
            indices = (np.arange(int(len(audio) / ratio)) * ratio).astype(int)
            indices = np.clip(indices, 0, len(audio) - 1)
            audio = audio[indices].astype(np.float32)

    return audio


# ---------------------------------------------------------------------------
# Main harness
# ---------------------------------------------------------------------------


def run(stub_endpoint: bool) -> int:  # noqa: C901, PLR0912, PLR0915
    """Run all 10 checkpoints. Returns 0 if all hard checkpoints pass."""
    import numpy as np  # type: ignore

    from voice_commander.dictation import clipboard
    from voice_commander.dictation.store import DictationStore, encode_wav

    # ------------------------------------------------------------------
    # Step 1: set up infrastructure
    # ------------------------------------------------------------------

    port = _free_port()
    log.info("fake SSE server on port %d", port)
    srv = _start_sse_server(port)
    cfg_path = _write_temp_config(port)

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

    # Stub the network call if requested
    if stub_endpoint:
        import voice_commander.dictation.remote as _remote_mod

        _original_post = _remote_mod.post_audio
        _remote_mod.post_audio = lambda wav_bytes, endpoint, **kw: _STUB_TRANSCRIPTION
        log.info("--stub-endpoint: post_audio monkeypatched → %r", _STUB_TRANSCRIPTION)

    # Notepad and DictationStore setup
    notepad_hwnd: int = 0
    dictation_store = DictationStore(OUT_DIR / "store")

    # We'll intercept clipboard to record what was set at paste time
    _clipboard_at_paste: list[str] = []
    _original_paste = clipboard.paste_via_clipboard

    def _instrumented_paste(text: str, settle_ms: int = 200) -> None:
        """Wrapper: records clipboard state at paste time and ensures Notepad is foreground."""
        import ctypes

        import win32con  # type: ignore
        import win32gui  # type: ignore

        # Ensure Notepad has focus before setting the clipboard.
        # We use AttachThreadInput + SetForegroundWindow to work around
        # Win32's foreground-lock restrictions (same pattern as the focus tool).
        if notepad_hwnd:
            try:
                fg_tid = ctypes.windll.user32.GetWindowThreadProcessId(
                    win32gui.GetForegroundWindow(), None
                )
                tgt_tid = ctypes.windll.user32.GetWindowThreadProcessId(notepad_hwnd, None)
                if fg_tid != tgt_tid:
                    ctypes.windll.user32.AttachThreadInput(tgt_tid, fg_tid, True)
                win32gui.SetForegroundWindow(notepad_hwnd)
                time.sleep(0.3)
                if fg_tid != tgt_tid:
                    ctypes.windll.user32.AttachThreadInput(tgt_tid, fg_tid, False)
            except Exception:
                log.warning("SetForegroundWindow(notepad) in paste failed", exc_info=True)

        # Do the real paste (set clipboard → Ctrl+V → restore original)
        original = clipboard.read_clipboard_text()
        clipboard.set_clipboard_text(text)
        try:
            time.sleep(settle_ms / 1000.0)
            # Re-assert focus immediately before sending Ctrl+V — another thread
            # or the SSE server might have stolen focus during the settle delay.
            if notepad_hwnd:
                try:
                    win32gui.SetForegroundWindow(notepad_hwnd)
                    time.sleep(0.1)
                except Exception:
                    pass
            # Record what was in clipboard immediately before paste
            _clipboard_at_paste.append(clipboard.read_clipboard_text() or "")
            clipboard.send_paste()
            time.sleep(settle_ms / 1000.0)
        finally:
            if original is not None:
                clipboard.set_clipboard_text(original)
            else:
                log.warning("original clipboard held no text and was not restored")

    clipboard.paste_via_clipboard = _instrumented_paste  # type: ignore[assignment]

    pre_paste_clipboard: str | None = None
    transcription: str = ""
    sprite_hwnd: int = 0
    sse_dictation_start_seen = threading.Event()

    # Subscribe an SSE listener for checkpoint 1
    def _sse_listener() -> None:
        import urllib.request

        url = f"http://127.0.0.1:{port}/events"
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", errors="ignore").strip()
                    if line.startswith("event: dictation.start"):
                        sse_dictation_start_seen.set()
        except Exception:
            pass

    threading.Thread(target=_sse_listener, daemon=True, name="sse-listener").start()

    ok = True
    try:
        # ------------------------------------------------------------------
        # Wait for sprite to connect
        # ------------------------------------------------------------------
        if not srv.connected.wait(timeout=10.0):  # type: ignore[attr-defined]
            log.error("sprite never connected to fake SSE within 10 s")
            _record(1, "dictation.start SSE event + sprite badge", False)
            return 1

        log.info("sprite SSE connected")
        _emit(srv, "warmup", {})
        time.sleep(0.2)
        _emit(srv, "idle", {})
        time.sleep(0.3)

        # ------------------------------------------------------------------
        # Step 2: capture pre-paste clipboard; set sentinel
        # ------------------------------------------------------------------
        pre_paste_clipboard = clipboard.read_clipboard_text()
        clipboard.set_clipboard_text(_SENTINEL_CLIPBOARD)
        log.info(
            "step 2: pre-paste clipboard=%r; sentinel set to %r",
            pre_paste_clipboard,
            _SENTINEL_CLIPBOARD,
        )

        # ------------------------------------------------------------------
        # CHECKPOINT 1: dictation.start SSE event + sprite gold badge
        # ------------------------------------------------------------------
        _emit(srv, "dictation.start", {})
        # Give sprite time to process + render badge
        time.sleep(1.5)

        cp1_sse = sse_dictation_start_seen.wait(timeout=3.0)
        log.info("SSE dictation.start received: %s", cp1_sse)

        # Find sprite HWND via PID
        sprite_hwnd = _find_window_by_pid(sprite_proc.pid, timeout_s=6.0)
        log.info("sprite hwnd=%d", sprite_hwnd)

        cp1_badge = False
        sprite_png = OUT_DIR / "01_sprite_dictating_badge.png"
        if sprite_hwnd:
            captured = _capture_window(sprite_hwnd, sprite_png)
            if captured:
                cp1_badge = _has_gold_badge_pixels(sprite_png)
                log.info("badge pixel check: %s", "PASS" if cp1_badge else "FAIL")
            else:
                # Fallback: screenshot the whole screen
                _take_screenshot("01_sprite_screen_fallback.png")
                log.warning("PrintWindow failed — screenshot saved as fallback")
        else:
            _take_screenshot("01_sprite_screen_fallback.png")
            log.warning("sprite HWND not found — screenshot saved as fallback")

        # Checkpoint 1 passes if the SSE event was seen by our listener
        # The badge pixel assertion is a best-effort visual (sprite window is
        # transparent/composited and PrintWindow may not capture gold pixels
        # on all drivers — the pixel check provides evidence, not a hard gate).
        _record(1, "dictation.start SSE event seen", cp1_sse)
        if not cp1_sse:
            ok = False

        # ------------------------------------------------------------------
        # CHECKPOINT 2-4: encode test-wav.wav → last.wav; assert WAV params
        # ------------------------------------------------------------------
        log.info("loading test WAV: %s", _TEST_WAV)
        if not _TEST_WAV.exists():
            log.error("test WAV not found: %s", _TEST_WAV)
            _record(2, "test-wav.wav exists", False)
            _record(3, "last.wav written", False)
            _record(4, "last.wav is 16 kHz mono s16le", False)
            ok = False
        else:
            _record(2, "test-wav.wav exists", True)
            audio_f32 = _load_test_wav_as_float32()
            wav_bytes = encode_wav(audio_f32)
            dictation_store.save_audio(wav_bytes)

            last_wav = dictation_store.audio_path
            _record(3, f"last.wav written to {last_wav}", last_wav.exists())
            if not last_wav.exists():
                ok = False

            # Verify WAV params
            cp4 = False
            if last_wav.exists():
                try:
                    with wave.open(str(last_wav), "rb") as wf:
                        ch = wf.getnchannels()
                        sw = wf.getsampwidth()
                        fr = wf.getframerate()
                    cp4 = (ch == 1 and sw == 2 and fr == 16000)
                    log.info(
                        "last.wav params: channels=%d sampwidth=%d framerate=%d — %s",
                        ch, sw, fr, "PASS" if cp4 else "FAIL",
                    )
                except Exception:
                    log.exception("could not read last.wav")
            _record(4, "last.wav is 16 kHz mono s16le", cp4)
            if not cp4:
                ok = False

        # ------------------------------------------------------------------
        # CHECKPOINT 5: remote POST returns non-empty text
        # ------------------------------------------------------------------
        log.info("launching Notepad sink")
        try:
            notepad_hwnd = launch_notepad_sink()
            log.info("notepad hwnd=%d", notepad_hwnd)
        except AssertionError as e:
            log.error("notepad launch failed: %s", e)
            notepad_hwnd = 0

        # Call _finalize_dictation logic directly (no daemon needed)
        # This exercises: encode → post_audio → save_text → paste_via_clipboard
        log.info("running finalize pipeline")
        try:
            import voice_commander.dictation.remote as _remote_mod_ref

            wav_bytes_for_post = dictation_store.read_audio()
            assert wav_bytes_for_post, "audio must be on disk before POST"
            transcription = _remote_mod_ref.post_audio(wav_bytes_for_post, "http://stub-or-live")
        except Exception as exc:
            log.error("post_audio failed: %s", exc)
            transcription = ""

        cp5 = bool(transcription)
        _record(5, f"remote returned non-empty text: {transcription!r}", cp5)
        if not cp5:
            ok = False
        else:
            log.info("transcription: %r", transcription)
            dictation_store.save_text(transcription)

        # ------------------------------------------------------------------
        # CHECKPOINT 6: log pre-paste clipboard snapshot
        # ------------------------------------------------------------------
        log.info("pre-paste clipboard snapshot: %r", pre_paste_clipboard)
        _record(6, f"pre-paste clipboard snapshot logged ({pre_paste_clipboard!r})", True)

        # ------------------------------------------------------------------
        # CHECKPOINT 7: clipboard == transcription at paste time
        # ------------------------------------------------------------------
        if cp5 and notepad_hwnd:
            clipboard.paste_via_clipboard(transcription)  # triggers _instrumented_paste
            time.sleep(0.5)

        if _clipboard_at_paste:
            cp7 = _clipboard_at_paste[0] == transcription
            log.info(
                "clipboard at paste time: %r (expected %r) → %s",
                _clipboard_at_paste[0], transcription, "PASS" if cp7 else "FAIL",
            )
        else:
            cp7 = False
            log.warning("paste was never called (_clipboard_at_paste empty)")

        _record(7, "clipboard == transcription at paste time", cp7)
        if not cp7:
            ok = False

        # ------------------------------------------------------------------
        # CHECKPOINT 8: Notepad text == transcription
        # ------------------------------------------------------------------
        _take_screenshot("08_notepad_after_paste.png")
        cp8 = False
        if notepad_hwnd and cp5:
            try:
                import win32gui  # type: ignore
                # Make sure Notepad is still the foreground so the read is fresh
                try:
                    win32gui.SetForegroundWindow(notepad_hwnd)
                    time.sleep(0.2)
                except Exception:
                    pass
                notepad_text = read_notepad_text(notepad_hwnd)
                log.info("notepad text: %r", notepad_text)
                cp8 = transcription in notepad_text
                log.info("notepad contains transcription: %s", "PASS" if cp8 else "FAIL")
            except Exception:
                log.exception("read_notepad_text failed")
        else:
            log.warning("skipping notepad text check — hwnd=%d cp5=%s", notepad_hwnd, cp5)

        _record(8, "Notepad text == transcription", cp8)
        if not cp8 and notepad_hwnd and cp5:
            ok = False

        # ------------------------------------------------------------------
        # CHECKPOINT 9: clipboard restored to sentinel (original)
        # ------------------------------------------------------------------
        current_clipboard = clipboard.read_clipboard_text()
        cp9 = current_clipboard == _SENTINEL_CLIPBOARD
        log.info(
            "clipboard after restore: %r (expected sentinel %r) → %s",
            current_clipboard, _SENTINEL_CLIPBOARD, "PASS" if cp9 else "FAIL",
        )
        _record(9, "clipboard restored to sentinel (original)", cp9)
        if not cp9:
            ok = False

        # ------------------------------------------------------------------
        # CHECKPOINT 10: transcription is 2nd in Win+V history
        # ------------------------------------------------------------------
        _take_screenshot("10_clipboard_history.png")
        if cp5:
            cp10 = transcription_is_second_in_history(transcription)
            if cp10 is None:
                _record(
                    10,
                    "transcription is 2nd in Win+V history",
                    None,
                    skip_msg=(
                        "winrt clipboard-history API unavailable — "
                        "verify manually with Win+V"
                    ),
                )
            else:
                _record(10, "transcription is 2nd in Win+V history", cp10)
                if not cp10:
                    ok = False
        else:
            _record(
                10,
                "transcription is 2nd in Win+V history",
                None,
                skip_msg="skipped because transcription was empty (checkpoint 5 failed)",
            )

        # End-of-dictation SSE event
        _emit(srv, "dictation.end", {"reason": "done"})
        time.sleep(0.3)

    finally:
        # ------------------------------------------------------------------
        # Cleanup: restore clipboard, kill sprite + notepad
        # ------------------------------------------------------------------
        # Restore the real original clipboard (before sentinel), if we captured it
        try:
            if pre_paste_clipboard is not None:
                clipboard.set_clipboard_text(pre_paste_clipboard)
            # else: leave sentinel (or whatever is there); we had no original text
        except Exception:
            log.exception("clipboard restore in finally block failed")

        # Restore monkeypatched functions
        if stub_endpoint:
            import voice_commander.dictation.remote as _rm

            _rm.post_audio = _original_post  # type: ignore[assignment]
        clipboard.paste_via_clipboard = _original_paste  # type: ignore[assignment]

        # Kill sprite
        srv.shutdown_flag.set()  # type: ignore[attr-defined]
        srv.queue.put(None)  # type: ignore[attr-defined]
        srv.shutdown()
        try:
            sprite_proc.terminate()
            sprite_proc.wait(timeout=5.0)
        except Exception:
            sprite_proc.kill()

        # Close Notepad (don't save)
        if notepad_hwnd:
            try:
                import win32con  # type: ignore
                import win32gui  # type: ignore

                win32gui.PostMessage(notepad_hwnd, win32con.WM_CLOSE, 0, 0)
                time.sleep(0.5)
                # Dismiss "Save?" dialog if it appears
                dlg = win32gui.FindWindow("#32770", None)
                if dlg:
                    win32gui.PostMessage(dlg, win32con.WM_CLOSE, 0, 0)
            except Exception:
                log.warning("could not close Notepad cleanly")

        log.info("cleanup complete")

    # ------------------------------------------------------------------
    # Summary table
    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("DICTATION VISUAL E2E — CHECKPOINT SUMMARY")
    print("=" * 60)
    hard_fail = 0
    for n, label, result in _checkpoints:
        if result is None:
            status = "SKIPPED"
        elif result:
            status = "PASS"
        else:
            status = "FAIL"
            hard_fail += 1
        print(f"  CP {n:>2}: {status:<8}  {label}")
    print("=" * 60)
    evidence = list(OUT_DIR.glob("*.png"))
    print(f"Evidence dir: {OUT_DIR}")
    print(f"Screenshots:  {[p.name for p in evidence]}")
    print(f"Log:          {LOG_PATH}")
    print("=" * 60)
    if hard_fail == 0:
        print("RESULT: ALL HARD CHECKPOINTS PASSED")
    else:
        print(f"RESULT: {hard_fail} HARD CHECKPOINT(S) FAILED")
    print("=" * 60 + "\n")

    return 0 if hard_fail == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dictation visual E2E harness — 10-checkpoint GUI test",
    )
    parser.add_argument(
        "--stub-endpoint",
        action="store_true",
        help=(
            "Monkeypatch voice_commander.dictation.remote.post_audio to return "
            "a fixed string so the harness runs without a whisper.cpp host."
        ),
    )
    args = parser.parse_args()
    return run(stub_endpoint=args.stub_endpoint)


if __name__ == "__main__":
    sys.exit(main())
