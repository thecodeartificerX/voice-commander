from __future__ import annotations

import logging

import pyautogui

from ..registry import tool
from ._win32 import COMET_EXE, COMET_LAUNCH_PATH, FocusWindowError, focus_window_by_exe

logger = logging.getLogger(__name__)


@tool
def minimize() -> None:
    pyautogui.hotkey("win", "down")


@tool
def maximize() -> None:
    pyautogui.hotkey("win", "up")


@tool
def focus_browser() -> None:
    _focus_comet()


def _focus_comet() -> None:
    try:
        focus_window_by_exe(COMET_EXE, launch_path=str(COMET_LAUNCH_PATH))
    except FocusWindowError:
        logger.error(
            "focus_browser: failed to focus exe=%r launch_path=%r",
            COMET_EXE,
            str(COMET_LAUNCH_PATH),
        )
        raise


@tool
def focus_terminal() -> None:
    _focus_terminal()


def _focus_terminal() -> None:
    try:
        focus_window_by_exe("WindowsTerminal.exe")
    except FocusWindowError:
        logger.error("focus_terminal: failed to focus exe=%r", "WindowsTerminal.exe")
        raise
