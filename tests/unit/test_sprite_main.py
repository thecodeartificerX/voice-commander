from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from voice_sprite.__main__ import (
    _CANCELLED_CUE_DURATION_S,
    _apply_cancelled_cue,
    _make_parser,
    _should_reload,
)


class TestShouldReload:
    """Pure mtime comparison logic — no I/O required."""

    def test_same_mtime_returns_false(self) -> None:
        assert _should_reload(1000.0, 1000.0) is False

    def test_different_mtime_returns_true(self) -> None:
        assert _should_reload(1000.0, 1001.0) is True

    def test_mtime_decreased_returns_true(self) -> None:
        # Filesystem clock skew or NTP correction can move mtime back.
        assert _should_reload(1001.0, 1000.0) is True

    def test_zero_old_nonzero_new_returns_true(self) -> None:
        # old=0 sentinel (file did not exist) → new file detected.
        assert _should_reload(0.0, 1234567890.5) is True

    def test_both_zero_returns_false(self) -> None:
        # Neither file existed before nor now — no change.
        assert _should_reload(0.0, 0.0) is False

    @pytest.mark.parametrize(
        "old,new,threshold,expected",
        [
            (1000.0, 1000.5, 1.0, False),  # change < threshold → no reload
            (1000.0, 1001.0, 1.0, False),  # change == threshold → no reload (strict >)
            (1000.0, 1001.1, 1.0, True),  # change > threshold → reload
            (1000.0, 1000.0, 0.0, False),  # zero threshold, no change
            (1000.0, 1000.001, 0.0, True),  # zero threshold, any change
        ],
    )
    def test_threshold_boundary(
        self, old: float, new: float, threshold: float, expected: bool
    ) -> None:
        assert _should_reload(old, new, threshold) is expected


class TestApplyCancelledCue:
    """Unit tests for the _apply_cancelled_cue wiring helper (ADR 0089 FIX 1)."""

    def _make_sm(self, cancelled: bool) -> MagicMock:
        sm = MagicMock()
        sm.cancelled_cue = cancelled
        return sm

    def test_cancelled_cue_true_calls_set_cancelled_cue(self) -> None:
        """When sm.cancelled_cue is True, window.set_cancelled_cue(True) is called."""
        sm = self._make_sm(True)
        window = MagicMock()
        schedule_fn = MagicMock()

        _apply_cancelled_cue(sm, window, schedule_fn)

        window.set_cancelled_cue.assert_called_once_with(True)

    def test_cancelled_cue_true_schedules_auto_clear(self) -> None:
        """When sm.cancelled_cue is True, a clear is scheduled for _CANCELLED_CUE_DURATION_S."""
        sm = self._make_sm(True)
        window = MagicMock()
        schedule_fn = MagicMock()

        _apply_cancelled_cue(sm, window, schedule_fn)

        assert schedule_fn.call_count == 1
        # pyglet.clock.schedule_once signature is (func, delay) — verify both
        # positionally so a swapped-argument regression is caught here.
        func, delay = schedule_fn.call_args[0]
        assert callable(func)
        assert delay == pytest.approx(_CANCELLED_CUE_DURATION_S)

    def test_auto_clear_callback_resets_sm_and_window(self) -> None:
        """The scheduled callback clears sm.cancelled_cue and hides the badge."""
        sm = self._make_sm(True)
        window = MagicMock()
        captured_callbacks: list = []

        def schedule_fn(cb, delay: float) -> None:  # noqa: ANN001
            captured_callbacks.append(cb)

        _apply_cancelled_cue(sm, window, schedule_fn)
        assert len(captured_callbacks) == 1

        # Fire the scheduled callback (simulating pyglet.clock firing after delay)
        captured_callbacks[0](2.5)

        assert sm.cancelled_cue is False
        assert window.set_cancelled_cue.call_args_list == [call(True), call(False)]

    def test_cancelled_cue_false_calls_set_cancelled_cue_false(self) -> None:
        """When sm.cancelled_cue is False, window.set_cancelled_cue(False) is called."""
        sm = self._make_sm(False)
        window = MagicMock()
        schedule_fn = MagicMock()

        _apply_cancelled_cue(sm, window, schedule_fn)

        window.set_cancelled_cue.assert_called_once_with(False)
        schedule_fn.assert_not_called()

    def test_cancelled_cue_false_does_not_schedule(self) -> None:
        """When sm.cancelled_cue is False, no auto-clear timer is scheduled."""
        sm = self._make_sm(False)
        window = MagicMock()
        schedule_fn = MagicMock()

        _apply_cancelled_cue(sm, window, schedule_fn)

        schedule_fn.assert_not_called()

    def test_cancel_badge_distinct_from_dictating_badge(self) -> None:
        """The cancelled cue must NOT affect sm.dictating.

        This confirms the two badges are fully independent.
        """
        sm = self._make_sm(True)
        sm.dictating = False
        window = MagicMock()
        schedule_fn = MagicMock()

        _apply_cancelled_cue(sm, window, schedule_fn)

        # Only set_cancelled_cue should be called — NOT set_dictating / set_dim
        window.set_dictating.assert_not_called()
        window.set_dim.assert_not_called()


class TestMakeParser:
    """CLI argument parser factory — no subprocess required."""

    def test_default_config_path(self) -> None:
        args = _make_parser().parse_args([])
        assert args.config == "config.toml"

    def test_custom_config_flag(self) -> None:
        args = _make_parser().parse_args(["--config", "custom/path.toml"])
        assert args.config == "custom/path.toml"

    def test_prog_name(self) -> None:
        parser = _make_parser()
        assert parser.prog == "voice-sprite"

    def test_unknown_flag_raises_system_exit(self) -> None:
        with pytest.raises(SystemExit):
            _make_parser().parse_args(["--unknown-flag"])
