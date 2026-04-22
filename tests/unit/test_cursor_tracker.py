"""Tests for CursorDock — patches Win32 ctypes calls and calls dock.tick()."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from voice_sprite.cursor_tracker import CursorDock


def _make_dock(window: MagicMock) -> CursorDock:
    """Return a CursorDock wired to *window* with standard test params."""
    return CursorDock(
        window=window,
        sprite_base_size_px=128,
        window_extra_w_px=220,
        window_extra_h_px=0,
        margin_x=8,
        margin_y=8,
    )


def _patch_win32(cursor_xy=(100, 100), rcwork=(0, 0, 1920, 1040), hmon=1, dpi=96):
    """Return a context-manager stack that patches all Win32 calls used by tick().

    Yields a dict with the individual mock objects so tests can introspect them.
    """
    import contextlib

    @contextlib.contextmanager
    def _stack():
        def fake_get_cursor_pos(byref_pt):
            # byref_pt is the result of ctypes.byref(pt); access via ._obj
            byref_pt._obj.x = cursor_xy[0]
            byref_pt._obj.y = cursor_xy[1]
            return 1  # success

        def fake_get_monitor_info(hmon_arg, byref_mi):
            mi = byref_mi._obj
            mi.rcWork.left = rcwork[0]
            mi.rcWork.top = rcwork[1]
            mi.rcWork.right = rcwork[2]
            mi.rcWork.bottom = rcwork[3]
            return 1  # success

        with (
            patch(
                "ctypes.windll.user32.GetCursorPos",
                side_effect=fake_get_cursor_pos,
            ) as m_gcp,
            patch(
                "ctypes.windll.user32.MonitorFromPoint",
                return_value=hmon,
            ) as m_mfp,
            patch(
                "ctypes.windll.user32.GetMonitorInfoW",
                side_effect=fake_get_monitor_info,
            ) as m_gmi,
            patch(
                "voice_sprite.cursor_tracker.get_dpi_for_monitor",
                return_value=dpi,
            ) as m_dpi,
        ):
            yield {
                "get_cursor_pos": m_gcp,
                "monitor_from_point": m_mfp,
                "get_monitor_info": m_gmi,
                "get_dpi_for_monitor": m_dpi,
            }

    return _stack()


# ---------------------------------------------------------------------------
# Test 1 — basic snap to bottom-right of work area at 96 DPI
# ---------------------------------------------------------------------------


def test_tick_snaps_to_bottom_right_of_workarea():
    """dock.tick() calls set_size(348, 128) and set_location(1564, 904)."""
    # DPI=96, scale=1.0
    # window_w = 128 + 220 = 348, window_h = 128 + 0 = 128
    # x = 1920 - 348 - 8 = 1564
    # y = 1040 - 128 - 8 = 904
    window = MagicMock()
    dock = _make_dock(window)

    with _patch_win32(cursor_xy=(100, 100), rcwork=(0, 0, 1920, 1040), hmon=1, dpi=96):
        dock.tick(0.016)

    window.set_size.assert_called_once_with(348, 128)
    window.set_location.assert_called_once_with(1564, 904)


# ---------------------------------------------------------------------------
# Test 2 — DPI rescale at 144 DPI (1.5×)
# ---------------------------------------------------------------------------


def test_dpi_rescale():
    """At 144 DPI (1.5×) window dimensions and position are scaled correctly."""
    # scale = 144 / 96 = 1.5
    # sprite_size = int(128 * 1.5) = 192
    # extra_w     = int(220 * 1.5) = 330
    # extra_h     = int(  0 * 1.5) = 0
    # window_w = 192 + 330 = 522, window_h = 192 + 0 = 192
    # x = 1920 - 522 - 8 = 1390
    # y = 1040 - 192 - 8 = 840
    window = MagicMock()
    dock = _make_dock(window)

    with _patch_win32(cursor_xy=(800, 400), rcwork=(0, 0, 1920, 1040), hmon=2, dpi=144):
        dock.tick(0.016)

    window.set_size.assert_called_once_with(522, 192)
    window.set_location.assert_called_once_with(1390, 840)


# ---------------------------------------------------------------------------
# Test 3 — hysteresis: same hmon on second tick → no redundant set_location
# ---------------------------------------------------------------------------


def test_hysteresis_skips_same_monitor():
    """Second tick() with the same hmon must NOT call set_location again."""
    window = MagicMock()
    dock = _make_dock(window)

    with _patch_win32(cursor_xy=(100, 100), rcwork=(0, 0, 1920, 1040), hmon=5, dpi=96):
        dock.tick(0.016)  # first tick — sets _last_hmon = 5
        dock.tick(0.016)  # second tick — same hmon, must be a no-op

    # set_location called exactly once across both ticks
    assert window.set_location.call_count == 1
    assert window.set_size.call_count == 1


# ---------------------------------------------------------------------------
# Test 4 — GetCursorPos failure → early return, no window calls
# ---------------------------------------------------------------------------


def test_get_cursor_pos_failure_returns_early():
    """When GetCursorPos returns 0 (failure), tick() must not touch the window."""
    window = MagicMock()
    dock = _make_dock(window)

    with patch("ctypes.windll.user32.GetCursorPos", return_value=0):
        dock.tick(0.016)

    window.set_size.assert_not_called()
    window.set_location.assert_not_called()


# ---------------------------------------------------------------------------
# Test 5 — cursor at (0, 0) is valid and must NOT be filtered out
# ---------------------------------------------------------------------------


def test_cursor_at_origin_is_valid():
    """Cursor at (0, 0) (top-left corner) is a legitimate position and must be processed."""
    window = MagicMock()
    dock = _make_dock(window)

    with _patch_win32(cursor_xy=(0, 0), rcwork=(0, 0, 1920, 1040), hmon=1, dpi=96):
        dock.tick(0.016)

    # tick() must have proceeded all the way to set_location
    window.set_location.assert_called_once()
