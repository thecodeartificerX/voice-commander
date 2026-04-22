from __future__ import annotations

from pathlib import Path

from voice_sprite.config import load_sprite_config


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
