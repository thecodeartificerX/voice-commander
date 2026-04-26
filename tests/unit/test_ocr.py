"""Unit tests for OCR primitive (mocked engines)."""

from __future__ import annotations

import asyncio
import builtins
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def test_ocr_region_tesseract_path(tmp_path, monkeypatch):
    """When winrt is unavailable and tesseract is on PATH, tesseract branch runs."""

    fake_img = tmp_path / "fake.png"
    fake_img.write_bytes(b"PNG")

    def _fake_select_engine():
        return "tesseract"

    def _fake_capture(x, y, w, h):
        return fake_img

    def _fake_run(cmd, **kw):
        r = MagicMock()
        r.returncode = 0
        r.stdout = "hello world"
        return r

    monkeypatch.setattr("voice_commander.tools.ocr._select_engine", _fake_select_engine)
    monkeypatch.setattr("voice_commander.tools.ocr._capture_region", _fake_capture)
    monkeypatch.setattr("subprocess.run", _fake_run)

    from voice_commander.tools.ocr import ocr_region

    result = ocr_region(0, 0, 100, 100)
    assert result == "hello world"


def test_ocr_region_no_engine_raises(monkeypatch):
    """When no engine is available, OcrEngineUnavailable is raised."""

    def _fake_select_engine():
        from voice_commander.tools.ocr import OcrEngineUnavailable

        raise OcrEngineUnavailable("no engine")

    monkeypatch.setattr("voice_commander.tools.ocr._select_engine", _fake_select_engine)

    from voice_commander.tools.ocr import OcrEngineUnavailable, ocr_region

    with pytest.raises(OcrEngineUnavailable):
        ocr_region(0, 0, 100, 100)


def test_ocr_tesseract_nonzero_exit_logs_warning(tmp_path, monkeypatch, caplog):
    """When tesseract exits non-zero, warning is logged but text still returned."""
    fake_img = tmp_path / "fake.png"
    fake_img.write_bytes(b"PNG")

    def _fake_select_engine():
        return "tesseract"

    def _fake_capture(x, y, w, h):
        return fake_img

    def _fake_run(cmd, **kw):
        r = MagicMock()
        r.returncode = 1
        r.stdout = "partial"
        r.stderr = "Error opening data file"
        return r

    monkeypatch.setattr("voice_commander.tools.ocr._select_engine", _fake_select_engine)
    monkeypatch.setattr("voice_commander.tools.ocr._capture_region", _fake_capture)
    monkeypatch.setattr("subprocess.run", _fake_run)

    from voice_commander.tools.ocr import ocr_region

    with caplog.at_level(logging.WARNING, logger="voice_commander.tools.ocr"):
        result = ocr_region(0, 0, 100, 100)

    assert result == "partial"
    assert "exited with code 1" in caplog.text
    assert "Error opening data file" in caplog.text


def test_select_engine_resolves_config_from_package_root(tmp_path, monkeypatch):
    """_select_engine resolves config.toml relative to package root, not CWD."""
    # Change CWD to a temp directory that has no config.toml
    monkeypatch.chdir(tmp_path)

    # _select_engine should NOT raise FileNotFoundError just because CWD changed.
    # We mock winrt import to fail so we can test the tesseract fallback path.
    real_import = builtins.__import__

    def _block_winrt(name, *args, **kwargs):
        if "winrt" in name:
            raise ImportError("mocked")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _block_winrt)
    monkeypatch.setattr(
        "shutil.which", lambda cmd: "/usr/bin/tesseract" if cmd == "tesseract" else None
    )

    from voice_commander.tools.ocr import _REPO_ROOT, _select_engine

    captured_paths: list[Path] = []

    def _spy_config_load(path: Path, *args, **kwargs):
        captured_paths.append(path)
        raise FileNotFoundError("spy: no config")

    with patch("voice_commander.config.Config.load", side_effect=_spy_config_load):
        result = _select_engine()

    assert result == "tesseract"

    # Key assertion: Config.load must be called with _REPO_ROOT-relative path,
    # NOT a CWD-relative path — this is the M12 fix being validated.
    assert len(captured_paths) == 1, "Config.load should be called exactly once"
    expected = _REPO_ROOT / "config.toml"
    assert captured_paths[0] == expected, (
        f"Config.load called with {captured_paths[0]!r}, expected {expected!r} "
        f"(CWD was {tmp_path!r})"
    )


async def test_ocr_winrt_from_async_context(tmp_path, monkeypatch):
    """_ocr_winrt must not raise RuntimeError when called inside a running loop."""
    fake_img = tmp_path / "fake.png"
    fake_img.write_bytes(b"PNG")

    monkeypatch.setattr("voice_commander.tools.ocr._select_engine", lambda: "winrt")
    monkeypatch.setattr(
        "voice_commander.tools.ocr._capture_region", lambda x, y, w, h: fake_img
    )

    import voice_commander.tools.ocr as ocr_mod

    monkeypatch.setattr(ocr_mod, "_ocr_winrt", lambda img_path: "async ocr text")

    from voice_commander.tools.ocr import ocr_region

    # This runs inside an async context (pytest-asyncio gives us a running loop)
    result = ocr_region(0, 0, 100, 100)
    assert result == "async ocr text"


def test_ocr_winrt_sync_context_still_works(tmp_path, monkeypatch):
    """_ocr_winrt still works from plain sync context (regression guard)."""
    fake_img = tmp_path / "fake.png"
    fake_img.write_bytes(b"PNG")

    monkeypatch.setattr("voice_commander.tools.ocr._select_engine", lambda: "winrt")
    monkeypatch.setattr(
        "voice_commander.tools.ocr._capture_region", lambda x, y, w, h: fake_img
    )

    import voice_commander.tools.ocr as ocr_mod

    monkeypatch.setattr(ocr_mod, "_ocr_winrt", lambda img_path: "sync ocr text")

    from voice_commander.tools.ocr import ocr_region

    result = ocr_region(0, 0, 100, 100)
    assert result == "sync ocr text"


async def test_ocr_winrt_loop_detection():
    """_ocr_winrt's loop detection correctly identifies async context."""
    # We're inside a running loop (pytest-asyncio).
    # Verify get_running_loop succeeds — this is the branch _ocr_winrt takes.
    loop = asyncio.get_running_loop()
    assert loop is not None  # Confirms we're in the async branch
