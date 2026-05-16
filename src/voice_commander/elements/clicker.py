"""Left-click at a screen coordinate.

Uses the same ``pyautogui`` + settle-delay approach as the existing ``click``
primitive so a hint click behaves identically to a normal voice click.
"""

from __future__ import annotations

import time


def click_point(x: int, y: int, *, settle_ms: int = 50) -> None:
    """Move the mouse to ``(x, y)`` and left-click, then settle briefly."""
    import pyautogui

    pyautogui.click(x=x, y=y)
    if settle_ms > 0:
        time.sleep(settle_ms / 1000.0)
