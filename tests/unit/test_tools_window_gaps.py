"""Targeted tests to close coverage gaps in tools/window.py.

Covers:
- _focus_default_browser: progid is None → falls back to "chrome.exe"
- _focus_default_browser: progid_to_exe returns None → falls back to "chrome.exe"
- _focus_default_browser: normal path with a real exe name
- _focus_terminal: calls focus_window_by_exe("WindowsTerminal.exe")
"""
from __future__ import annotations

from unittest.mock import patch, call

from voice_commander.tools import window


# ---------------------------------------------------------------------------
# _focus_default_browser
# ---------------------------------------------------------------------------

def test_focus_default_browser_fallback_when_progid_is_none():
    """If default_browser_progid() returns None, fall back to chrome.exe."""
    with (
        patch("voice_commander.tools.window.default_browser_progid", return_value=None),
        patch("voice_commander.tools.window.progid_to_exe") as mock_to_exe,
        patch("voice_commander.tools.window.focus_window_by_exe") as mock_focus,
    ):
        window._focus_default_browser()

    # progid_to_exe must NOT be called when progid is None
    mock_to_exe.assert_not_called()
    mock_focus.assert_called_once_with("chrome.exe")


def test_focus_default_browser_fallback_when_exe_is_none():
    """If progid_to_exe() returns None, fall back to chrome.exe."""
    with (
        patch("voice_commander.tools.window.default_browser_progid", return_value="SomeBrowserProgid"),
        patch("voice_commander.tools.window.progid_to_exe", return_value=None),
        patch("voice_commander.tools.window.focus_window_by_exe") as mock_focus,
    ):
        window._focus_default_browser()

    mock_focus.assert_called_once_with("chrome.exe")


def test_focus_default_browser_uses_detected_exe():
    """If progid_to_exe() returns a real exe name, that exe is focused."""
    with (
        patch("voice_commander.tools.window.default_browser_progid", return_value="FirefoxURL"),
        patch("voice_commander.tools.window.progid_to_exe", return_value="firefox.exe"),
        patch("voice_commander.tools.window.focus_window_by_exe") as mock_focus,
    ):
        window._focus_default_browser()

    mock_focus.assert_called_once_with("firefox.exe")


# ---------------------------------------------------------------------------
# _focus_terminal
# ---------------------------------------------------------------------------

def test_focus_terminal_calls_windows_terminal_exe():
    """_focus_terminal() must call focus_window_by_exe with WindowsTerminal.exe."""
    with patch("voice_commander.tools.window.focus_window_by_exe") as mock_focus:
        window._focus_terminal()

    mock_focus.assert_called_once_with("WindowsTerminal.exe")
