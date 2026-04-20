from __future__ import annotations

import logging

import pyautogui

from ..registry import tool
from ._win32 import default_browser_progid, focus_window_by_exe, progid_to_exe

logger = logging.getLogger(__name__)


@tool
def minimize() -> None:
    pyautogui.hotkey("win", "down")


@tool
def maximize() -> None:
    pyautogui.hotkey("win", "up")


@tool
def focus_browser() -> None:
    _focus_default_browser()


def _focus_default_browser() -> None:
    progid = default_browser_progid()
    exe = progid_to_exe(progid) if progid else None
    if exe is None:
        logger.warning("No default browser detected; falling back to chrome.exe")
        exe = "chrome.exe"
    focus_window_by_exe(exe)


@tool
def focus_terminal() -> None:
    _focus_terminal()


def _focus_terminal() -> None:
    focus_window_by_exe("WindowsTerminal.exe")
