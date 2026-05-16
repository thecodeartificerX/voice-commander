"""Unit tests for the elements clicker (ADR 0087)."""

import sys
import types
from unittest.mock import MagicMock


def test_click_point_clicks_at_coordinates(monkeypatch) -> None:
    fake_pyautogui = types.ModuleType("pyautogui")
    fake_pyautogui.click = MagicMock()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)

    from voice_commander.elements.clicker import click_point

    click_point(640, 360, settle_ms=0)

    fake_pyautogui.click.assert_called_once_with(x=640, y=360)
