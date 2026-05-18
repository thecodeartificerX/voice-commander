"""Unit tests for streaming dictation config loading."""

from __future__ import annotations

import pytest

from voice_commander.dictation_stream.config import StreamDictationConfig, load


def test_defaults_when_file_absent(tmp_path):
    cfg = load(tmp_path / "missing.toml")
    assert cfg == StreamDictationConfig()
    assert cfg.ws_url == "ws://192.168.4.200:8765/ws/transcribe"
    assert cfg.max_chunk_seconds == 15


def test_defaults_when_section_absent(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[hotkey]\nkey = "scroll_lock"\n')
    assert load(path) == StreamDictationConfig()


def test_section_overrides_only_named_keys(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        "[dictation_stream]\n"
        'ws_url = "ws://example:9000/ws"\n'
        "max_chunk_seconds = 10\n"
    )
    cfg = load(path)
    assert cfg.ws_url == "ws://example:9000/ws"
    assert cfg.max_chunk_seconds == 10
    assert cfg.language == "en"  # untouched default


def test_unknown_key_rejected(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[dictation_stream]\nbogus_key = 1\n")
    with pytest.raises(ValueError, match="bogus_key"):
        load(path)


def test_input_device_override(tmp_path):
    # default is None
    assert StreamDictationConfig().input_device is None
    # TOML integer overrides it
    path = tmp_path / "config.toml"
    path.write_text("[dictation_stream]\ninput_device = 4\n")
    cfg = load(path)
    assert cfg.input_device == 4
