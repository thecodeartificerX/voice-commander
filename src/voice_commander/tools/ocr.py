"""Optional OCR primitive. Engine selection chain:

1. winrt Windows.Media.Ocr (fastest, ships with Windows; requires winrt-* deps).
2. tesseract subprocess (must be on PATH).
3. raises OcrEngineUnavailable.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from voice_commander.registry import tool

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]


class OcrEngineUnavailable(RuntimeError):
    """Raised when no OCR engine (winrt or tesseract) is available."""


_Engine = Literal["winrt", "tesseract"]


@tool
def ocr_region(x: int, y: int, w: int, h: int) -> str:
    """Run OCR on the screen region (x, y, w, h). Returns recognised text.

    Engine fallback chain: winrt Windows.Media.Ocr → tesseract subprocess.
    Requires winrt-* deps OR tesseract on PATH.
    """
    engine = _select_engine()
    img_path = _capture_region(x, y, w, h)
    try:
        if engine == "winrt":
            return _ocr_winrt(img_path)
        if engine == "tesseract":
            return _ocr_tesseract(img_path)
    finally:
        img_path.unlink(missing_ok=True)
    raise OcrEngineUnavailable("no OCR engine available")


def _capture_region(x: int, y: int, w: int, h: int) -> Path:
    """Save a PNG of the screen region to a tmp file and return the path."""
    import pyautogui  # already a project dep

    fd, path_str = tempfile.mkstemp(suffix=".png", prefix="vc_ocr_")
    os.close(fd)
    img = pyautogui.screenshot(region=(x, y, w, h))
    img.save(path_str)
    return Path(path_str)


def _select_engine() -> _Engine:
    # Check config for explicit engine preference
    try:
        from voice_commander.config import Config

        cfg = Config.load(_REPO_ROOT / "config.toml")
        pref = getattr(cfg.perception, "ocr_engine", "auto")
    except (FileNotFoundError, AttributeError):
        pref = "auto"
    except Exception:
        logger.warning(
            "Failed to read OCR engine preference from config; defaulting to auto",
            exc_info=True,
        )
        pref = "auto"

    if pref == "winrt":
        return "winrt"
    if pref == "tesseract":
        return "tesseract"

    # auto: try winrt first
    try:
        import winrt.windows.media.ocr  # noqa: F401

        return "winrt"
    except ImportError:
        pass
    if shutil.which("tesseract"):
        return "tesseract"
    raise OcrEngineUnavailable("no OCR engine: install winrt deps or tesseract")


def _ocr_winrt(img_path: Path) -> str:
    """OCR via Windows.Media.Ocr (winrt).

    Internally async (WinRT APIs are coroutine-based).  Safe to call from
    **both** sync contexts (daemon pipeline thread) and async contexts
    (FastAPI route handlers).  When a running event loop is detected the
    coroutine is executed on a throwaway thread via
    ``ThreadPoolExecutor`` + ``asyncio.run()``, giving it a fresh event
    loop and avoiding the ``RuntimeError`` that ``asyncio.run()`` raises
    inside a running loop.
    """
    import asyncio
    import concurrent.futures

    try:
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage import StorageFile
        from winrt.windows.storage.streams import FileAccessMode
    except ImportError as exc:
        raise OcrEngineUnavailable(f"winrt not available: {exc}") from exc

    async def _run() -> str:
        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise OcrEngineUnavailable(
                "OcrEngine.try_create_from_user_profile_languages() returned None"
                " — install a language pack"
            )
        file = await StorageFile.get_file_from_path_async(str(img_path))
        stream = await file.open_async(FileAccessMode.READ)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        result = await engine.recognize_async(bitmap)
        return result.text  # type: ignore[no-any-return]

    # Detect whether we're already inside a running event loop.
    try:
        asyncio.get_running_loop()
        _inside_loop = True
    except RuntimeError:
        _inside_loop = False

    if not _inside_loop:
        # Normal sync context — safe to use asyncio.run().
        return asyncio.run(_run())

    # Async context (e.g. FastAPI route) — spin up a fresh loop on a
    # throwaway thread so the WinRT coroutine can run without nesting.
    logger.debug("Running event loop detected; dispatching WinRT OCR to background thread")
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, _run())
        try:
            return future.result(timeout=15)
        except concurrent.futures.TimeoutError:
            logger.error("WinRT OCR timed out after 15s (async-context path)")
            raise OcrEngineUnavailable("WinRT OCR timed out") from None


def _ocr_tesseract(img_path: Path) -> str:
    """OCR via tesseract subprocess."""
    out = subprocess.run(
        ["tesseract", str(img_path), "-", "-l", "eng", "--psm", "6"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if out.returncode != 0:
        logger.warning(
            "tesseract exited with code %d; stderr: %s",
            out.returncode,
            out.stderr.strip(),
        )
    return out.stdout.strip()
