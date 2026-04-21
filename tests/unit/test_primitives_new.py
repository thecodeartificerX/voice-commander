"""Unit tests for the three new primitives: scroll, open_url, close_window."""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from voice_commander.tools._win32 import FocusWindowError
from voice_commander.tools.primitives import close_window, open_url, scroll

# ---------------------------------------------------------------------------
# scroll
# ---------------------------------------------------------------------------

class TestScroll:
    def test_scroll_down(self):
        """scroll('down', 5) calls pyautogui.scroll(-5)."""
        with patch("voice_commander.tools.primitives.pyautogui.scroll") as mock_scroll:
            scroll("down", 5)
        mock_scroll.assert_called_once_with(-5)

    def test_scroll_up(self):
        """scroll('up', 3) calls pyautogui.scroll(3)."""
        with patch("voice_commander.tools.primitives.pyautogui.scroll") as mock_scroll:
            scroll("up", 3)
        mock_scroll.assert_called_once_with(3)

    def test_scroll_default_amount(self):
        """scroll('down') uses default amount=3 → pyautogui.scroll(-3)."""
        with patch("voice_commander.tools.primitives.pyautogui.scroll") as mock_scroll:
            scroll("down")
        mock_scroll.assert_called_once_with(-3)

    def test_scroll_up_default_amount(self):
        """scroll('up') uses default amount=3 → pyautogui.scroll(3)."""
        with patch("voice_commander.tools.primitives.pyautogui.scroll") as mock_scroll:
            scroll("up")
        mock_scroll.assert_called_once_with(3)

    def test_scroll_direction_case_insensitive(self):
        """Direction matching is case-insensitive ('DOWN' → negative clicks)."""
        with patch("voice_commander.tools.primitives.pyautogui.scroll") as mock_scroll:
            scroll("DOWN", 2)
        mock_scroll.assert_called_once_with(-2)


# ---------------------------------------------------------------------------
# open_url
# ---------------------------------------------------------------------------

class TestOpenUrl:
    def test_open_url(self):
        """open_url delegates to webbrowser.open with the provided URL."""
        with patch("webbrowser.open") as mock_open:
            open_url("https://example.com")
        mock_open.assert_called_once_with("https://example.com")

    def test_open_url_arbitrary_scheme(self):
        """open_url works for any URL string, not just https."""
        with patch("webbrowser.open") as mock_open:
            open_url("http://localhost:8080/api")
        mock_open.assert_called_once_with("http://localhost:8080/api")


# ---------------------------------------------------------------------------
# close_window
# ---------------------------------------------------------------------------

class TestCloseWindow:
    def _make_win32_mocks(self, title: str, target_hwnd: int = 42):
        """Return (mock_win32gui, mock_win32con) pre-configured to match *title*."""
        mock_win32gui = MagicMock()
        mock_win32con = MagicMock()
        mock_win32con.WM_CLOSE = 16  # standard WM_CLOSE value

        mock_win32gui.IsWindowVisible.return_value = True
        mock_win32gui.GetWindowText.return_value = title

        def fake_enum_windows(callback, extra):
            callback(target_hwnd, None)

        mock_win32gui.EnumWindows.side_effect = fake_enum_windows
        return mock_win32gui, mock_win32con

    def test_close_window_success(self):
        """WM_CLOSE is posted to the matching window's hwnd."""
        _TARGET_HWND = 42
        mock_win32gui, mock_win32con = self._make_win32_mocks(
            "My Notepad Window", target_hwnd=_TARGET_HWND
        )

        with patch.dict(
            "sys.modules",
            {"win32gui": mock_win32gui, "win32con": mock_win32con},
        ):
            close_window("notepad")

        mock_win32gui.PostMessage.assert_called_once_with(
            _TARGET_HWND, mock_win32con.WM_CLOSE, 0, 0
        )

    def test_close_window_not_found(self):
        """FocusWindowError is raised when EnumWindows returns no matching window."""
        mock_win32gui = MagicMock()
        mock_win32con = MagicMock()

        mock_win32gui.IsWindowVisible.return_value = True
        # Window title does NOT match the target substring
        mock_win32gui.GetWindowText.return_value = "Unrelated Application"

        def fake_enum_windows(callback, extra):
            callback(99, None)

        mock_win32gui.EnumWindows.side_effect = fake_enum_windows

        with patch.dict(
            "sys.modules",
            {"win32gui": mock_win32gui, "win32con": mock_win32con},
        ), pytest.raises(FocusWindowError):
            close_window("notepad")

        mock_win32gui.PostMessage.assert_not_called()

    def test_close_window_not_found_logs_error(self, caplog):
        """An error is logged when no matching window is found."""
        mock_win32gui = MagicMock()
        mock_win32con = MagicMock()

        mock_win32gui.IsWindowVisible.return_value = True
        mock_win32gui.GetWindowText.return_value = "Unrelated Application"

        def fake_enum_windows(callback, extra):
            callback(99, None)

        mock_win32gui.EnumWindows.side_effect = fake_enum_windows

        with (
            patch.dict(
                "sys.modules",
                {"win32gui": mock_win32gui, "win32con": mock_win32con},
            ),
            caplog.at_level(logging.ERROR, logger="voice_commander.tools.primitives"),
            pytest.raises(FocusWindowError),
        ):
            close_window("notepad")

        assert "notepad" in caplog.text

    def test_close_window_import_error(self, caplog):
        """When pywin32 is unavailable, FocusWindowError is raised with a warning."""
        with (
            patch.dict("sys.modules", {"win32gui": None, "win32con": None}),
            caplog.at_level(logging.WARNING, logger="voice_commander.tools.primitives"),
            pytest.raises(FocusWindowError),
        ):
            close_window("anything")

        assert "pywin32" in caplog.text

    def test_close_window_invisible_windows_skipped(self):
        """Invisible windows are skipped; only visible matches trigger WM_CLOSE."""
        _TARGET_HWND = 77
        mock_win32gui = MagicMock()
        mock_win32con = MagicMock()
        mock_win32con.WM_CLOSE = 16

        # First hwnd (11) is invisible; second (77) is visible and matches.
        def _is_visible(hwnd):
            return hwnd == _TARGET_HWND

        mock_win32gui.IsWindowVisible.side_effect = _is_visible
        mock_win32gui.GetWindowText.return_value = "Notepad"

        def fake_enum_windows(callback, extra):
            callback(11, None)   # invisible → skipped
            callback(_TARGET_HWND, None)  # visible, matches

        mock_win32gui.EnumWindows.side_effect = fake_enum_windows

        with patch.dict(
            "sys.modules",
            {"win32gui": mock_win32gui, "win32con": mock_win32con},
        ):
            close_window("notepad")

        mock_win32gui.PostMessage.assert_called_once_with(
            _TARGET_HWND, mock_win32con.WM_CLOSE, 0, 0
        )
