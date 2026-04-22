from __future__ import annotations

from unittest.mock import MagicMock, patch

from voice_sprite.dpi import get_primary_dpi


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
