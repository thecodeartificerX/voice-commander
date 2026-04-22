from __future__ import annotations

from unittest.mock import MagicMock, patch

from voice_sprite.dpi import get_dpi_for_monitor, get_primary_dpi


def test_get_primary_dpi_returns_96_on_fallback():
    """When ctypes calls fail, return 96."""
    with patch("voice_sprite.dpi.ctypes") as mock_ctypes:
        mock_ctypes.windll.shcore.SetProcessDpiAwarenessContext.side_effect = OSError
        mock_ctypes.windll.shcore.GetDpiForMonitor.side_effect = OSError
        result = get_primary_dpi()
    assert result == 96


def test_get_primary_dpi_reads_real_value():
    """When ctypes calls succeed, return the DPI value."""
    with patch("voice_sprite.dpi.ctypes") as mock_ctypes:
        mock_ctypes.windll.user32.MonitorFromPoint.return_value = 1
        mock_ctypes.c_uint.return_value = MagicMock(value=144)
        mock_ctypes.byref = MagicMock()
        result = get_primary_dpi()
    assert result == 144


def test_get_dpi_for_monitor_returns_dpi_x(monkeypatch):

    class FakeShcore:
        def GetDpiForMonitor(self, hmon, dpi_type, dpi_x_ptr, dpi_y_ptr):
            dpi_x_ptr._obj.value = 144
            dpi_y_ptr._obj.value = 144
            return 0

    class FakeWindll:
        shcore = FakeShcore()

    monkeypatch.setattr("voice_sprite.dpi.ctypes.windll", FakeWindll())
    assert get_dpi_for_monitor(12345) == 144


def test_get_dpi_for_monitor_falls_back_to_96_on_error(monkeypatch):
    class FakeShcore:
        def GetDpiForMonitor(self, *args):
            raise OSError("boom")

    class FakeWindll:
        shcore = FakeShcore()

    monkeypatch.setattr("voice_sprite.dpi.ctypes.windll", FakeWindll())
    assert get_dpi_for_monitor(0) == 96
