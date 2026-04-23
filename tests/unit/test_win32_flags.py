"""Unit tests for voice_sprite.win32_flags.apply_click_through."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from voice_sprite.win32_flags import (
    GWL_EXSTYLE,
    HWND_TOPMOST,
    SWP_NOMOVE,
    SWP_NOSIZE,
    WS_EX_NOACTIVATE,
    WS_EX_TOOLWINDOW,
    apply_click_through,
)


@pytest.fixture()
def fake_user32():
    mock = MagicMock()
    mock.GetWindowLongW.return_value = 0x00000200
    return mock


@pytest.fixture()
def fake_dwmapi():
    mock = MagicMock()
    mock.DwmEnableBlurBehindWindow.return_value = 0  # S_OK
    return mock


@pytest.fixture(autouse=True)
def _patch_win32(monkeypatch, fake_user32, fake_dwmapi):
    monkeypatch.setattr("voice_sprite.win32_flags.user32", fake_user32)
    monkeypatch.setattr("voice_sprite.win32_flags.dwmapi", fake_dwmapi)


def test_happy_path(fake_user32, fake_dwmapi):
    """All user32 + DWM calls fire in order with correct args."""
    apply_click_through(42)

    fake_user32.GetWindowLongW.assert_called_once_with(42, GWL_EXSTYLE)
    fake_user32.SetWindowLongW.assert_called_once_with(
        42, GWL_EXSTYLE, 0x00000200 | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    )
    fake_user32.SetWindowPos.assert_called_once_with(
        42, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE
    )
    fake_dwmapi.DwmEnableBlurBehindWindow.assert_called_once()


def test_exstyle_or_mask_preserves_existing_bits(fake_user32):
    """Existing WS_EX_LAYERED | WS_EX_TRANSPARENT bits are not clobbered."""
    fake_user32.GetWindowLongW.return_value = 0x00080020
    apply_click_through(99)

    expected = 0x00080020 | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    fake_user32.SetWindowLongW.assert_called_once_with(99, GWL_EXSTYLE, expected)


def test_user32_oserror_logs_and_continues_to_dwm(fake_user32, fake_dwmapi, caplog):
    """OSError in user32 block logs but does not skip DWM block."""
    fake_user32.GetWindowLongW.side_effect = OSError("boom")

    with caplog.at_level(logging.DEBUG):
        apply_click_through(1)

    assert "Failed core click-through flags" in caplog.text
    fake_dwmapi.DwmEnableBlurBehindWindow.assert_called_once()


def test_dwm_nonzero_hresult_logs_warning(fake_dwmapi, caplog):
    """Non-zero HRESULT from DwmEnableBlurBehindWindow logs a warning."""
    fake_dwmapi.DwmEnableBlurBehindWindow.return_value = 0x80070057

    with caplog.at_level(logging.WARNING):
        apply_click_through(7)

    assert "DwmEnableBlurBehindWindow HRESULT=" in caplog.text


def test_dwm_oserror_logs_exception(fake_dwmapi, caplog):
    """OSError from DwmEnableBlurBehindWindow is logged, not raised."""
    fake_dwmapi.DwmEnableBlurBehindWindow.side_effect = OSError("dwm gone")

    with caplog.at_level(logging.ERROR):
        apply_click_through(3)

    assert "DwmEnableBlurBehindWindow failed on hwnd=" in caplog.text
