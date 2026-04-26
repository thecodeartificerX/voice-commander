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
