"""Unit tests for the four perception tools in voice_commander.tools.perception.

All win32 and psutil APIs are mocked via patch.dict('sys.modules', ...) because
the imports are lazy (inside each function body) and CI environments lack pywin32.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

from voice_commander.tools.perception import get_active_window_title, get_cursor_pos, read_clipboard


# ---------------------------------------------------------------------------
# read_clipboard
# ---------------------------------------------------------------------------


def test_read_clipboard_returns_unicode_text():
    import types

    fake_win32clipboard = types.SimpleNamespace(
        OpenClipboard=lambda: None,
        CloseClipboard=lambda: None,
        IsClipboardFormatAvailable=lambda fmt: True,
        GetClipboardData=lambda fmt: "hello",
    )
    fake_win32con = types.SimpleNamespace(CF_UNICODETEXT=13)

    mods = {"win32clipboard": fake_win32clipboard, "win32con": fake_win32con}
    with patch.dict(sys.modules, mods):
        assert read_clipboard() == "hello"


def test_read_clipboard_returns_empty_when_no_text():
    import types

    fake_win32clipboard = types.SimpleNamespace(
        OpenClipboard=lambda: None,
        CloseClipboard=lambda: None,
        IsClipboardFormatAvailable=lambda fmt: False,
        GetClipboardData=lambda fmt: None,
    )
    fake_win32con = types.SimpleNamespace(CF_UNICODETEXT=13)

    mods = {"win32clipboard": fake_win32clipboard, "win32con": fake_win32con}
    with patch.dict(sys.modules, mods):
        assert read_clipboard() == ""


# ---------------------------------------------------------------------------
# get_active_window_title
# ---------------------------------------------------------------------------


def test_get_active_window_title_returns_text():
    import types

    fake = types.SimpleNamespace(
        GetForegroundWindow=lambda: 0x1234,
        GetWindowText=lambda hwnd: "Comet — example.com",
    )

    with patch.dict(sys.modules, {"win32gui": fake}):
        assert get_active_window_title() == "Comet — example.com"


def test_get_active_window_title_returns_empty_on_no_foreground():
    import types

    fake = types.SimpleNamespace(
        GetForegroundWindow=lambda: 0,
        GetWindowText=lambda hwnd: "",
    )

    with patch.dict(sys.modules, {"win32gui": fake}):
        assert get_active_window_title() == ""


# ---------------------------------------------------------------------------
# get_cursor_pos
# ---------------------------------------------------------------------------


def test_get_cursor_pos_returns_tuple():
    import types

    fake_win32api = types.SimpleNamespace(GetCursorPos=lambda: (100, 200))

    with patch.dict(sys.modules, {"win32api": fake_win32api}):
        result = get_cursor_pos()
    assert result == (100, 200)


def test_get_cursor_pos_fallback_on_import_error():
    """When win32api is not importable, returns (0, 0)."""
    with patch.dict(sys.modules, {"win32api": None}):
        result = get_cursor_pos()
    assert result == (0, 0)
