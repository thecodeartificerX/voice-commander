"""Optional OCR primitive. Engine selection chain:

1. winrt Windows.Media.Ocr (fastest, ships with Windows; requires winrt-* deps).
2. tesseract subprocess (must be on PATH).
3. raises OcrEngineUnavailable.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from voice_commander.registry import tool

logger = logging.getLogger(__name__)


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
    import os
    os.close(fd)
    img = pyautogui.screenshot(region=(x, y, w, h))
    img.save(path_str)
    return Path(path_str)


def _select_engine() -> _Engine:
    # Check config for explicit engine preference
    try:
        from pathlib import Path as _Path

        from voice_commander.config import Config
        cfg = Config.load(_Path("config.toml"))
        pref = getattr(cfg.perception, "ocr_engine", "auto")
    except Exception:
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
    """OCR via Windows.Media.Ocr (winrt)."""
    import asyncio
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

    return asyncio.run(_run())


def _ocr_tesseract(img_path: Path) -> str:
    """OCR via tesseract subprocess."""
    out = subprocess.run(
        ["tesseract", str(img_path), "-", "-l", "eng", "--psm", "6"],
        capture_output=True, text=True, timeout=10,
    )
    return out.stdout.strip()
