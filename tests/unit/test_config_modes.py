from pathlib import Path

from voice_commander.config import Config, ModesConfig


def test_modes_defaults() -> None:
    cfg = ModesConfig()
    assert cfg.enabled is True
    assert cfg.dir == "modes"


def test_config_load_reads_modes_section(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text('[modes]\nenabled = false\ndir = "my_modes"\n', encoding="utf-8")
    cfg = Config.load(p)
    assert cfg.modes.enabled is False
    assert cfg.modes.dir == "my_modes"


def test_config_load_modes_defaults_when_absent(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text("", encoding="utf-8")
    cfg = Config.load(p)
    assert cfg.modes.enabled is True
    assert cfg.modes.dir == "modes"
