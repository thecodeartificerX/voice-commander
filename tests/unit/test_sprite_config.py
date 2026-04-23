from __future__ import annotations

from pathlib import Path

import pytest

from voice_sprite.config import SpriteConfigError, load_sprite_config


def test_sprite_config_error_is_value_error():
    assert issubclass(SpriteConfigError, ValueError)
    exc = SpriteConfigError("test")
    assert isinstance(exc, ValueError)


def test_defaults():
    cfg = load_sprite_config(Path("/nonexistent.toml"))
    assert cfg.daemon_url == "http://127.0.0.1:8765"
    assert cfg.corner == "bottom_right"
    assert cfg.base_size_px == 128
    assert cfg.offset_x == 16
    assert cfg.offset_y == 16
    assert cfg.asset_path == "assets/sprite"
    assert cfg.bubble_fade_ms == 2000
    assert cfg.heartbeat_timeout_ms == 3000


def test_override(tmp_path):
    toml = tmp_path / "config.toml"
    toml.write_text('[sprite]\ncorner = "top_left"\nbase_size_px = 64\n[web]\nport = 9000\n')
    cfg = load_sprite_config(toml)
    assert cfg.corner == "top_left"
    assert cfg.base_size_px == 64
    assert cfg.daemon_url == "http://127.0.0.1:9000"


def test_local_overlay(tmp_path):
    base = tmp_path / "config.toml"
    base.write_text('[sprite]\ncorner = "bottom_right"\n')
    local = tmp_path / "config.local.toml"
    local.write_text('[sprite]\ncorner = "top_right"\n')
    cfg = load_sprite_config(base)
    assert cfg.corner == "top_right"


def test_hud_defaults(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("[sprite]\nbase_size_px = 128\n", encoding="utf-8")
    cfg = load_sprite_config(cfg_path)
    assert cfg.hud.enabled is True
    assert cfg.hud.max_lines == 5
    assert cfg.hud.hold_ms == 4000
    assert cfg.hud.fade_ms == 3000
    assert cfg.hud.font_size == 13
    assert cfg.hud.width_px == 220
    assert cfg.hud.llm_summary_timeout_ms == 800
    assert cfg.hud.llm_fallback_enabled is True


def test_hud_override(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        "[hud]\nenabled = false\nmax_lines = 3\nhold_ms = 2000\n",
        encoding="utf-8",
    )
    cfg = load_sprite_config(cfg_path)
    assert cfg.hud.enabled is False
    assert cfg.hud.max_lines == 3
    assert cfg.hud.hold_ms == 2000


def test_sprite_follow_cursor_defaults(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("", encoding="utf-8")
    cfg = load_sprite_config(cfg_path)
    assert cfg.follow_cursor is True
    assert cfg.follow_poll_hz == 30
    assert cfg.margin_x == 8
    assert cfg.margin_y == 8


# --- Negative values → raise ---
@pytest.mark.parametrize(
    "toml_text",
    [
        "[hud]\nmax_lines = -1\n",
        "[hud]\nmax_lines = 0\n",  # exact boundary for min=1
        "[hud]\nhold_ms = -500\n",
        "[hud]\nfade_ms = -1\n",
        "[hud]\nfont_size = -5\n",
        "[hud]\nfont_size = 0\n",  # exact boundary for min=1
        "[hud]\nwidth_px = 0\n",
        "[hud]\nllm_summary_timeout_ms = 0\n",
        "[sprite]\nbase_size_px = -10\n",
        "[sprite]\nbase_size_px = 0\n",  # exact boundary for min=1
        "[sprite]\nbubble_fade_ms = -1\n",
        "[sprite]\nheartbeat_timeout_ms = 0\n",
        "[sprite]\nmargin_x = -1\n",
        "[sprite]\nmargin_y = -3\n",
    ],
)
def test_negative_or_zero_invalid_raises(tmp_path, toml_text):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(toml_text, encoding="utf-8")
    with pytest.raises(SpriteConfigError):
        load_sprite_config(cfg_file)


# --- Zero values that ARE valid ---
@pytest.mark.parametrize(
    "toml_text,attr_path,expected",
    [
        ("[hud]\nfade_ms = 0\n", ("hud", "fade_ms"), 0),
        ("[hud]\nhold_ms = 0\n", ("hud", "hold_ms"), 0),
        ("[sprite]\nbubble_fade_ms = 0\n", ("bubble_fade_ms",), 0),
        ("[sprite]\nmargin_x = 0\n", ("margin_x",), 0),
        ("[sprite]\nmargin_y = 0\n", ("margin_y",), 0),
        # Boundary values (inclusive min/max)
        ("[sprite]\nfollow_poll_hz = 1\n", ("follow_poll_hz",), 1),
        ("[sprite]\nfollow_poll_hz = 60\n", ("follow_poll_hz",), 60),
        # Float valid roundtrip
        ("[sprite]\nrender_scale = 1.5\n", ("render_scale",), 1.5),
        ("[sprite]\nrender_scale = 0.001\n", ("render_scale",), 0.001),
    ],
)
def test_zero_valid_values(tmp_path, toml_text, attr_path, expected):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(toml_text, encoding="utf-8")
    cfg = load_sprite_config(cfg_file)
    obj = cfg
    for attr in attr_path:
        obj = getattr(obj, attr)
    assert obj == expected


# --- Type mismatches → raise ---
@pytest.mark.parametrize(
    "toml_text",
    [
        '[hud]\nmax_lines = "five"\n',
        '[hud]\nhold_ms = "fast"\n',
        # str not valid for int fields
        '[hud]\nmax_lines = "5"\n',  # string silently coerced in old code; must now raise
        # bool not valid for int fields (bool-trap: all 15 int fields)
        "[sprite]\nfollow_poll_hz = true\n",
        "[hud]\nmax_lines = true\n",
        "[hud]\nhold_ms = true\n",
        "[hud]\nfade_ms = true\n",
        "[hud]\nfont_size = true\n",
        "[hud]\nwidth_px = true\n",
        "[hud]\nllm_summary_timeout_ms = true\n",
        "[sprite]\nbase_size_px = true\n",
        "[sprite]\nbubble_fade_ms = true\n",
        "[sprite]\nheartbeat_timeout_ms = true\n",
        "[sprite]\nmargin_x = true\n",
        "[sprite]\nmargin_y = true\n",
        "[sprite]\noffset_x = true\n",
        "[sprite]\noffset_y = true\n",
        # bool not valid for float field
        "[sprite]\nrender_scale = true\n",
        # str not valid for bool fields
        '[sprite]\nfollow_cursor = "yes"\n',
        '[hud]\nenabled = "false"\n',
        '[hud]\nllm_fallback_enabled = "true"\n',
        # int not valid for bool fields
        "[hud]\nenabled = 1\n",
        "[sprite]\nfollow_cursor = 0\n",
        # str not valid for offset int fields
        '[sprite]\noffset_x = "left"\n',
        "[sprite]\noffset_y = true\n",
    ],
)
def test_type_mismatch_raises(tmp_path, toml_text):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(toml_text, encoding="utf-8")
    with pytest.raises(SpriteConfigError):
        load_sprite_config(cfg_file)


# --- Out-of-range ---
@pytest.mark.parametrize(
    "toml_text",
    [
        "[sprite]\nfollow_poll_hz = 61\n",  # one past max=60
        "[sprite]\nfollow_poll_hz = 0\n",
        "[sprite]\nrender_scale = 0.0\n",
        "[sprite]\nrender_scale = -1.5\n",
    ],
)
def test_out_of_range_raises(tmp_path, toml_text):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(toml_text, encoding="utf-8")
    with pytest.raises(SpriteConfigError):
        load_sprite_config(cfg_file)


# --- Missing tables entirely → defaults ---
def test_missing_sprite_table_uses_defaults(tmp_path):
    """[sprite] section absent → all sprite fields fall back to defaults."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("[web]\nport = 8765\n", encoding="utf-8")
    cfg = load_sprite_config(cfg_file)
    assert cfg.corner == "bottom_right"
    assert cfg.base_size_px == 128
    assert cfg.follow_poll_hz == 30
    assert cfg.render_scale == 0.75


def test_missing_hud_table_uses_defaults(tmp_path):
    """[hud] section absent → all hud fields fall back to defaults."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("[sprite]\nbase_size_px = 128\n", encoding="utf-8")
    cfg = load_sprite_config(cfg_file)
    assert cfg.hud.max_lines == 5
    assert cfg.hud.hold_ms == 4000
    assert cfg.hud.fade_ms == 3000
    assert cfg.hud.enabled is True


# --- Error message quality ---
def test_error_message_contains_field_name(tmp_path):
    """SpriteConfigError message must identify table and field."""
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[hud]\nmax_lines = "five"\n', encoding="utf-8")
    with pytest.raises(SpriteConfigError, match=r"\[hud\].*max_lines"):
        load_sprite_config(cfg_file)
