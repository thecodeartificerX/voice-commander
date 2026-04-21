import textwrap

import pytest

from voice_commander.config import Config


def test_load_defaults_when_file_has_no_overrides(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("")
    cfg = Config.load(cfg_file)
    assert cfg.hotkey.key == "scroll_lock"
    assert cfg.audio.channels == 1
    assert cfg.matching.threshold == 85.0
    assert cfg.feedback.sounds_dir == "assets/sounds"


def test_load_applies_overrides(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        textwrap.dedent("""
        [matching]
        threshold = 70.0
        [audio]
        channels = 2
    """)
    )
    cfg = Config.load(cfg_file)
    assert cfg.matching.threshold == 70.0
    assert cfg.audio.channels == 2
    # untouched sections keep defaults
    assert cfg.hotkey.key == "scroll_lock"


def test_load_missing_file_returns_all_defaults(tmp_path):
    cfg = Config.load(tmp_path / "nonexistent.toml")
    assert cfg.audio.channels == 1
    assert cfg.audio.device == -1
    assert cfg.audio.output_dir == "outputs"


def test_invalid_type_raises(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[audio]\nchannels = "not-an-int"\n')
    with pytest.raises((TypeError, ValueError)):
        Config.load(cfg_file)


def test_config_is_frozen(tmp_path):
    cfg = Config.load(tmp_path / "nope.toml")
    with pytest.raises((AttributeError, TypeError)):
        cfg.hotkey.key = "a"


def test_vad_defaults(tmp_path):
    cfg = Config.load(tmp_path / "nope.toml")
    assert cfg.vad.threshold == 0.4
    assert cfg.vad.min_speech_duration_ms == 100
    assert cfg.vad.min_silence_duration_ms == 250
    assert cfg.vad.speech_pad_ms == 30
    assert cfg.vad.pre_roll_ms == 300
    assert cfg.vad.max_utterance_ms == 8000
    assert cfg.vad.gates.min_word_count == 1
    assert cfg.vad.gates.max_no_speech_prob == 0.6


def test_local_overrides_base(tmp_path):
    base = tmp_path / "config.toml"
    base.write_text(
        textwrap.dedent("""
        [audio]
        channels = 1
        device = 13

        [vad]
        threshold = 0.4

        [vad.gates]
        min_word_count = 1
    """)
    )
    local = tmp_path / "config.local.toml"
    local.write_text(
        textwrap.dedent("""
        [audio]
        device = 8

        [vad.gates]
        min_word_count = 3
    """)
    )
    cfg = Config.load(base)
    # local overrides
    assert cfg.audio.device == 8
    assert cfg.vad.gates.min_word_count == 3
    # base values preserved where local silent
    assert cfg.audio.channels == 1
    assert cfg.vad.threshold == 0.4


def test_local_missing_uses_base(tmp_path):
    base = tmp_path / "config.toml"
    base.write_text("[audio]\ndevice = 5\n")
    cfg = Config.load(base)
    assert cfg.audio.device == 5


def test_explicit_local_path(tmp_path):
    base = tmp_path / "config.toml"
    base.write_text("[audio]\ndevice = 1\n")
    custom = tmp_path / "overrides.toml"
    custom.write_text("[audio]\ndevice = 42\n")
    cfg = Config.load(base, local_path=custom)
    assert cfg.audio.device == 42


def test_vad_overrides(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        textwrap.dedent("""
        [vad]
        threshold = 0.3
        min_silence_duration_ms = 200

        [vad.gates]
        min_word_count = 2
    """)
    )
    cfg = Config.load(cfg_file)
    assert cfg.vad.threshold == 0.3
    assert cfg.vad.min_silence_duration_ms == 200
    assert cfg.vad.gates.min_word_count == 2
    # untouched fields keep defaults
    assert cfg.vad.pre_roll_ms == 300


def test_mute_key_defaults_to_empty(tmp_path):
    cfg = Config.load(tmp_path / "nope.toml")
    assert cfg.hotkey.mute_key == ""


def test_mute_key_loads_from_toml(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[hotkey]\nmute_key = "ctrl_r"\n')
    cfg = Config.load(cfg_file)
    assert cfg.hotkey.mute_key == "ctrl_r"


def test_mute_key_invalid_type_raises(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("[hotkey]\nmute_key = 123\n")
    with pytest.raises((TypeError, ValueError)):
        Config.load(cfg_file)
