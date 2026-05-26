import logging
import os
import textwrap
from unittest.mock import patch

import pytest

from voice_commander.config import Config, update_user_config


def test_load_defaults_when_file_has_no_overrides(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("")
    cfg = Config.load(cfg_file)
    assert cfg.hotkey.key == "scroll_lock"
    assert cfg.audio.channels == 1
    assert cfg.feedback.sounds_dir == "assets/sounds"


def test_load_applies_overrides(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        textwrap.dedent("""
        [audio]
        channels = 2
    """)
    )
    cfg = Config.load(cfg_file)
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


def test_local_file_ignored(tmp_path):
    """config.local.toml is no longer merged — base values always win."""
    base = tmp_path / "config.toml"
    base.write_text("[audio]\ndevice = 13\n")
    local = tmp_path / "config.local.toml"
    local.write_text("[audio]\ndevice = 8\n")
    cfg = Config.load(base)
    assert cfg.audio.device == 13  # local file ignored


def test_local_missing_uses_base(tmp_path):
    base = tmp_path / "config.toml"
    base.write_text("[audio]\ndevice = 5\n")
    cfg = Config.load(base)
    assert cfg.audio.device == 5


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


def test_llm_section_emits_deprecation_warning(tmp_path, caplog):
    """[llm] section in config.toml emits a deprecation warning (ADR 0082)."""
    import logging
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("[llm]\nmodel_id = \"test\"\n")
    with caplog.at_level(logging.WARNING, logger="voice_commander.config"):
        Config.load(cfg_file)
    assert any(
        "deprecated" in r.getMessage().lower() or "removed" in r.getMessage().lower()
        for r in caplog.records
    ), "Expected deprecation warning for [llm] section"


def test_llm_section_is_silently_dropped(tmp_path):
    """[llm] section does not error and Config loads successfully."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("[llm]\nmodel_id = \"test\"\n")
    cfg = Config.load(cfg_file)
    assert cfg.audio.channels == 1  # defaults still work


# ---------------------------------------------------------------------------
# update_user_config tests
# ---------------------------------------------------------------------------


def test_update_user_config_strips_audio_device_key(tmp_path):
    """Legacy audio.device int must not appear in the output after a write."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[audio]\ndevice = 7\ndevice_name = "Old"\n')

    update_user_config(cfg_file, {"audio": {"device": 99, "device_name": "X"}})

    # Read back raw TOML text
    text = cfg_file.read_text()
    # device_name must be updated
    assert 'device_name = "X"' in text
    # Legacy device int must have been stripped — no bare "device = ..." line
    lines = text.splitlines()
    device_lines = [
        ln for ln in lines
        if ln.strip().startswith("device") and "device_name" not in ln
    ]
    assert device_lines == [], f"Unexpected legacy device lines: {device_lines}"

    # Also confirm via Config.load that device_name reads back correctly
    cfg = Config.load(cfg_file)
    assert cfg.audio.device_name == "X"
    assert cfg.audio.device == -1  # default because key was stripped


def test_update_user_config_atomic_via_replace(tmp_path):
    """update_user_config must write via os.replace for atomicity."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[audio]\ndevice_name = "Before"\n')

    replace_calls: list[tuple[str, str]] = []

    original_replace = os.replace

    def spy_replace(src: str, dst: str) -> None:
        replace_calls.append((str(src), str(dst)))
        original_replace(src, dst)

    with patch("voice_commander.config.os.replace", side_effect=spy_replace):
        update_user_config(cfg_file, {"audio": {"device_name": "After"}})

    assert len(replace_calls) == 1, "os.replace should be called exactly once"
    src_path, dst_path = replace_calls[0]
    # Source must be the .tmp file next to the config
    assert src_path.endswith(".tmp")
    # Destination must be the config file itself
    assert dst_path == str(cfg_file)


def test_update_user_config_logs_warning_on_legacy_audio_device(tmp_path, caplog):
    """A WARNING must be emitted when 'device' is supplied in the audio payload."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[audio]\ndevice_name = "Test"\n')

    with caplog.at_level(logging.WARNING, logger="voice_commander.config"):
        update_user_config(cfg_file, {"audio": {"device": 5, "device_name": "Test"}})

    warning_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "device" in msg and ("legacy" in msg.lower() or "adr 0081" in msg.lower())
        for msg in warning_messages
    ), f"Expected legacy-device warning, got: {warning_messages}"


# ---------------------------------------------------------------------------
# Picker config (ADR 0083)
# ---------------------------------------------------------------------------


def test_picker_defaults(tmp_path):
    """[picker] missing entirely → safe defaults."""
    from voice_commander.config import Config

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("")
    cfg = Config.load(cfg_path)
    assert cfg.picker.enabled is True
    assert cfg.picker.timeout_sec == 5
    assert cfg.picker.cancel_words == ("cancel", "nevermind", "stop")
    assert cfg.picker.focus.cap == 5
    assert cfg.picker.focus.exclude_foreground is True
    assert cfg.picker.focus.exclude_self is True


def test_picker_overrides(tmp_path):
    from voice_commander.config import Config

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
[picker]
enabled = false
timeout_sec = 8
cancel_words = ["cancel", "abort"]

[picker.focus]
cap = 7
exclude_foreground = false
exclude_self = false
"""
    )
    cfg = Config.load(cfg_path)
    assert cfg.picker.enabled is False
    assert cfg.picker.timeout_sec == 8
    assert cfg.picker.cancel_words == ("cancel", "abort")
    assert cfg.picker.focus.cap == 7
    assert cfg.picker.focus.exclude_foreground is False
    assert cfg.picker.focus.exclude_self is False


# ---------------------------------------------------------------------------
# dictation_key rename + DictationConfig (ADR 0086)
# ---------------------------------------------------------------------------


def test_hotkey_dictation_key_default(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("[hotkey]\nkey = \"scroll_lock\"\n", encoding="utf-8")
    from voice_commander.config import Config
    cfg = Config.load(cfg_file)
    assert cfg.hotkey.dictation_key == "ctrl_r"
    assert not hasattr(cfg.hotkey, "mute_key")


def test_dictation_config_defaults(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("[hotkey]\nkey = \"scroll_lock\"\n", encoding="utf-8")
    from voice_commander.config import Config
    cfg = Config.load(cfg_file)
    assert cfg.dictation.ws_url == "ws://192.168.4.200:8767/ws/transcribe"
    assert cfg.dictation.end_word == "done"
    assert cfg.dictation.idle_timeout_seconds == 30
    assert cfg.dictation.max_dictation_s == 300
    # language field removed in ADR 0096 Phase 2
    assert not hasattr(cfg.dictation, "language")


def test_dictation_config_override(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        "[hotkey]\nkey = \"scroll_lock\"\n"
        "[dictation]\nws_url = \"ws://1.2.3.4:9/ws/transcribe\"\nend_word = \"finish\"\n",
        encoding="utf-8",
    )
    from voice_commander.config import Config
    cfg = Config.load(cfg_file)
    assert cfg.dictation.ws_url == "ws://1.2.3.4:9/ws/transcribe"
    assert cfg.dictation.end_word == "finish"


def test_dictation_config_cancel_word_default(tmp_path):
    """DictationConfig.cancel_word defaults to 'cancel' when not specified."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("", encoding="utf-8")
    from voice_commander.config import Config
    cfg = Config.load(cfg_file)
    assert cfg.dictation.cancel_word == "cancel"


def test_dictation_config_cancel_word_override(tmp_path):
    """DictationConfig.cancel_word can be overridden via [dictation] section."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        "[dictation]\ncancel_word = \"abort\"\n",
        encoding="utf-8",
    )
    from voice_commander.config import Config
    cfg = Config.load(cfg_file)
    assert cfg.dictation.cancel_word == "abort"


def test_stale_mute_key_warns_and_is_ignored(tmp_path, caplog):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        "[hotkey]\nkey = \"scroll_lock\"\nmute_key = \"ctrl_r\"\n",
        encoding="utf-8",
    )
    import logging

    from voice_commander.config import Config

    with caplog.at_level(logging.WARNING):
        cfg = Config.load(cfg_file)
    # Stale key is ignored, daemon still loads, dictation_key keeps its default.
    assert cfg.hotkey.dictation_key == "ctrl_r"
    assert not hasattr(cfg.hotkey, "mute_key")
    assert any("mute_key" in r.message for r in caplog.records)


def test_dictation_max_dictation_s_default(tmp_path):
    """DictationConfig.max_dictation_s defaults to 300 (ADR 0096 D4)."""
    from voice_commander.config import DictationConfig

    cfg = DictationConfig()
    assert cfg.max_dictation_s == 300
    assert not hasattr(cfg, "window_step_ms")
    assert not hasattr(cfg, "window_cap_ms")


def test_dictation_max_dictation_s_override(tmp_path):
    """DictationConfig.max_dictation_s can be overridden via [dictation] section."""
    from voice_commander.config import Config

    toml = tmp_path / "config.toml"
    toml.write_text(
        "[dictation]\n"
        'ws_url = "ws://x/ws"\n'
        "max_dictation_s = 120\n",
        encoding="utf-8",
    )
    cfg = Config.load(toml)
    assert cfg.dictation.max_dictation_s == 120


# ---------------------------------------------------------------------------
# [dictation] backend selector (ADR 0102)
# ---------------------------------------------------------------------------


def test_dictation_backend_defaults_to_internal() -> None:
    from voice_commander.config import DictationConfig

    assert DictationConfig().backend == "internal"


def test_dictation_backend_parses_external(tmp_path) -> None:
    from voice_commander.config import Config

    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[dictation]\nbackend = "external"\n', encoding="utf-8")
    cfg = Config.load(cfg_file)
    assert cfg.dictation.backend == "external"


def test_dictation_backend_unknown_value_warns_and_falls_back(tmp_path, caplog) -> None:
    import logging

    from voice_commander.config import Config

    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[dictation]\nbackend = "wispr"\n', encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="voice_commander.config"):
        cfg = Config.load(cfg_file)
    assert cfg.dictation.backend == "internal"
    assert any("backend" in r.message and "internal" in r.message for r in caplog.records)


def test_dictation_backend_empty_string_falls_back(tmp_path) -> None:
    from voice_commander.config import Config

    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[dictation]\nbackend = ""\n', encoding="utf-8")
    cfg = Config.load(cfg_file)
    assert cfg.dictation.backend == "internal"
