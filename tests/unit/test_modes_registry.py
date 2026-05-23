from pathlib import Path

from voice_commander.modes.registry import ModeRegistry


def _mode(tmp: Path, fname: str, trigger: str, action: str = "press a") -> None:
    (tmp / fname).write_text(
        f'[mode]\ntrigger = "{trigger}"\n\n[[command]]\nphrases=["x"]\naction="{action}"\n',
        encoding="utf-8",
    )


def test_load_all_indexes_by_trigger(tmp_path: Path) -> None:
    _mode(tmp_path, "video.toml", "video")
    _mode(tmp_path, "mouseless.toml", "mouseless")
    reg = ModeRegistry(tmp_path)
    reg.load_all()
    assert {d.trigger for d in reg.all()} == {"video", "mouseless"}
    assert reg.by_trigger("video") is not None
    assert reg.by_trigger("nope") is None


def test_bad_file_skipped_not_fatal(tmp_path: Path) -> None:
    _mode(tmp_path, "good.toml", "video")
    (tmp_path / "bad.toml").write_text('[[command]]\nphrases=["x"]\naction="frobnicate"\n', "utf-8")
    reg = ModeRegistry(tmp_path)
    reg.load_all()
    assert reg.by_trigger("video") is not None
    assert len(reg.all()) == 1


def test_missing_dir_is_empty(tmp_path: Path) -> None:
    reg = ModeRegistry(tmp_path / "does_not_exist")
    reg.load_all()
    assert reg.all() == []


def test_reload_picks_up_new_file(tmp_path: Path) -> None:
    _mode(tmp_path, "video.toml", "video")
    reg = ModeRegistry(tmp_path)
    reg.load_all()
    assert len(reg.all()) == 1
    _mode(tmp_path, "mouseless.toml", "mouseless")
    reg.reload()
    assert len(reg.all()) == 2
