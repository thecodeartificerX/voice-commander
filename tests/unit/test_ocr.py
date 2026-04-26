"""Unit tests for OCR primitive (mocked engines)."""

from __future__ import annotations

from unittest.mock import MagicMock

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
    import logging

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
    # It should fall back to "auto" if config not found at package root.
    # We mock winrt import to fail so we can test the tesseract fallback path.
    import builtins

    real_import = builtins.__import__

    def _block_winrt(name, *args, **kwargs):
        if "winrt" in name:
            raise ImportError("mocked")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _block_winrt)
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/tesseract" if cmd == "tesseract" else None)

    from voice_commander.tools.ocr import _select_engine

    # Should succeed with "tesseract" (not crash due to CWD-relative config path)
    result = _select_engine()
    assert result == "tesseract"
