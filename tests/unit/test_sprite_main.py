from __future__ import annotations

import pytest

from voice_sprite.__main__ import _make_parser, _should_reload


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
            (1000.0, 1000.5, 1.0, False),   # change < threshold → no reload
            (1000.0, 1001.0, 1.0, False),   # change == threshold → no reload (strict >)
            (1000.0, 1001.1, 1.0, True),    # change > threshold → reload
            (1000.0, 1000.0, 0.0, False),   # zero threshold, no change
            (1000.0, 1000.001, 0.0, True),  # zero threshold, any change
        ],
    )
    def test_threshold_boundary(
        self, old: float, new: float, threshold: float, expected: bool
    ) -> None:
        assert _should_reload(old, new, threshold) is expected


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

    def test_unknown_flag_raises_system_exit(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit):
            _make_parser().parse_args(["--unknown-flag"])
        # stderr consumed; argparse error message does not pollute test output
