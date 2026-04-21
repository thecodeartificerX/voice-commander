"""Targeted tests to close coverage gaps in tools/window.py.

Covers:
- _focus_comet: calls focus_window_by_exe with comet.exe and full launch path
- _focus_terminal: calls focus_window_by_exe("WindowsTerminal.exe")
"""
from __future__ import annotations

from unittest.mock import patch

from voice_commander.tools import window


# ---------------------------------------------------------------------------
# _focus_comet
# ---------------------------------------------------------------------------

def test_focus_comet_targets_comet_exe_with_launch_path():
    """_focus_comet() must target comet.exe and pass the full launch path."""
    with patch("voice_commander.tools.window.focus_window_by_exe") as mock_focus:
        window._focus_comet()

    mock_focus.assert_called_once_with(
        window.COMET_EXE, launch_path=str(window.COMET_LAUNCH_PATH)
    )


def test_comet_launch_path_points_into_perplexity_dir():
    """Sanity check: the hardcoded launch path ends at Perplexity\\Comet\\Application\\comet.exe."""
    parts = window.COMET_LAUNCH_PATH.parts[-4:]
    assert parts == ("Perplexity", "Comet", "Application", "comet.exe")


# ---------------------------------------------------------------------------
# _focus_terminal
# ---------------------------------------------------------------------------

def test_focus_terminal_calls_windows_terminal_exe():
    """_focus_terminal() must call focus_window_by_exe with WindowsTerminal.exe."""
    with patch("voice_commander.tools.window.focus_window_by_exe") as mock_focus:
        window._focus_terminal()

    mock_focus.assert_called_once_with("WindowsTerminal.exe")
