from __future__ import annotations

import pytest

from voice_sprite.charsheet import CharSheetError, load_charsheet
from voice_sprite.state_machine import SpriteState


@pytest.fixture()
def valid_toml(tmp_path):
    toml = tmp_path / "charsheet.toml"
    toml.write_text(
        "frame_width = 64\nframe_height = 64\nfps = 12\n\n"
        "[states.idle]\nrow = 0\nframes = 2\n\n"
        "[states.listening]\nrow = 1\nframes = 2\n\n"
        "[states.hearing_speech]\nrow = 2\nframes = 2\n\n"
        "[states.thinking]\nrow = 3\nframes = 2\n\n"
        "[states.llm_thinking]\nrow = 4\nframes = 2\n\n"
        "[states.success]\nrow = 5\nframes = 2\n\n"
        "[states.miss]\nrow = 6\nframes = 2\n\n"
        "[states.tool_error]\nrow = 7\nframes = 2\n\n"
        "[states.warmup]\nrow = 8\nframes = 2\n\n"
        "[states.crashed]\nrow = 9\nframes = 2\n\n"
        # ADR 0096 D5: processing state — reuses idle row; badge is the visual cue.
        "[states.processing]\nrow = 0\nframes = 2\n\n"
    )
    return toml


@pytest.fixture()
def valid_png(tmp_path):
    """Create a minimal 128x640 PNG (2 cols x 10 rows of 64x64 frames)."""
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("Pillow not installed")
    img = Image.new("RGBA", (128, 640), (0, 0, 0, 0))
    path = tmp_path / "charsheet.png"
    img.save(path)
    return path


def test_load_charsheet_valid(valid_toml, valid_png):
    cs = load_charsheet(valid_toml, valid_png)
    assert cs.frame_width == 64
    assert cs.frame_height == 64
    assert cs.fps == 12
    assert len(cs.states) == 11  # 10 original + PROCESSING (ADR 0096 D5)


def test_load_charsheet_missing_state(tmp_path, valid_png):
    toml = tmp_path / "charsheet.toml"
    toml.write_text(
        "frame_width = 64\nframe_height = 64\nfps = 12\n\n[states.idle]\nrow = 0\nframes = 2\n"
    )
    with pytest.raises(CharSheetError, match="missing"):
        load_charsheet(toml, valid_png)


def test_load_charsheet_oob_row(tmp_path):
    """Row * frame_height exceeds PNG height."""
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("Pillow not installed")
    # 64x64 PNG — only 1 row fits
    img = Image.new("RGBA", (128, 64), (0, 0, 0, 0))
    png = tmp_path / "charsheet.png"
    img.save(png)
    toml = tmp_path / "charsheet.toml"
    # row 1 requires y >= 64, but PNG is only 64px tall
    toml.write_text(
        "frame_width = 64\nframe_height = 64\nfps = 12\n\n"
        + "".join(
            f"[states.{s.value}]\nrow = {i}\nframes = 2\n\n" for i, s in enumerate(SpriteState)
        )
    )
    with pytest.raises(CharSheetError, match="bounds"):
        load_charsheet(toml, png)


def test_load_charsheet_unknown_keys_warns(valid_toml, valid_png, caplog):
    """Unknown TOML keys should WARN, not fail."""
    content = valid_toml.read_text() + '\nfuture_key = "hi"\n'
    valid_toml.write_text(content)
    cs = load_charsheet(valid_toml, valid_png)
    assert cs.frame_width == 64  # still loads


def test_charsheet_get_state_anim(valid_toml, valid_png):
    cs = load_charsheet(valid_toml, valid_png)
    anim = cs.get_state_anim(SpriteState.IDLE)
    assert anim.row == 0
    assert anim.frames == 2


def test_charsheet_transitions(tmp_path, valid_png):
    toml = tmp_path / "charsheet.toml"
    content = (
        "frame_width = 64\nframe_height = 64\nfps = 12\n\n"
        + "".join(
            f"[states.{s.value}]\nrow = {i}\nframes = 2\n\n" for i, s in enumerate(SpriteState)
        )
        + '[transitions."listening->thinking"]\nrow = 10\nframes = 3\nonce = true\n'
    )
    toml.write_text(content)
    # Need PNG tall enough: 11 rows * 64 = 704
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("Pillow not installed")
    img = Image.new("RGBA", (192, 704), (0, 0, 0, 0))
    png = tmp_path / "charsheet.png"
    img.save(png)
    cs = load_charsheet(toml, png)
    t = cs.get_transition(SpriteState.LISTENING, SpriteState.THINKING)
    assert t is not None
    assert t.frames == 3
    assert t.once is True
