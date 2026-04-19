from pathlib import Path
import textwrap
import pytest
from voice_commander.config import Config, HotkeyConfig, AudioConfig


def test_load_defaults_when_file_has_no_overrides(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("")
    cfg = Config.load(cfg_file)
    assert cfg.hotkey.key == "scroll_lock"
    assert cfg.audio.sample_rate == 16000
    assert cfg.matching.threshold == 85.0
    assert cfg.feedback.toast_enabled is True


def test_load_applies_overrides(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(textwrap.dedent("""
        [matching]
        threshold = 70.0
        [audio]
        sample_rate = 48000
    """))
    cfg = Config.load(cfg_file)
    assert cfg.matching.threshold == 70.0
    assert cfg.audio.sample_rate == 48000
    # untouched sections keep defaults
    assert cfg.hotkey.key == "scroll_lock"


def test_load_missing_file_returns_all_defaults(tmp_path):
    cfg = Config.load(tmp_path / "nonexistent.toml")
    assert cfg.audio.sample_rate == 16000


def test_invalid_type_raises(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[audio]\nsample_rate = "not-an-int"\n')
    with pytest.raises((TypeError, ValueError)):
        Config.load(cfg_file)


def test_config_is_frozen(tmp_path):
    cfg = Config.load(tmp_path / "nope.toml")
    with pytest.raises((AttributeError, TypeError)):
        cfg.hotkey.key = "a"
