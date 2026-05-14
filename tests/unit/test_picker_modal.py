from __future__ import annotations

from voice_sprite.picker_modal import (
    MAX_CARDS,
    ModalGeometry,
    ModalState,
    compute_modal_position,
    format_cards,
    format_rows,
)


def test_format_rows_renders_numbered_lines():
    rows = format_rows([{"n": 1, "label": "Chrome"}, {"n": 2, "label": "VS Code"}])
    assert rows == ["1. Chrome", "2. VS Code"]


def test_format_rows_handles_empty_list():
    assert format_rows([]) == []


def test_compute_modal_position_centres_on_work_area():
    geometry = ModalGeometry(width=300, height=180)
    pos = compute_modal_position(
        geometry=geometry,
        work_area=(0, 0, 1920, 1080),
    )
    assert pos == (1920 // 2 - 150, 1080 // 2 - 90)


def test_compute_modal_position_centres_on_secondary_monitor():
    geometry = ModalGeometry(width=300, height=180)
    pos = compute_modal_position(
        geometry=geometry,
        work_area=(1920, 0, 1920, 1080),
    )
    assert pos == (1920 + 1920 // 2 - 150, 1080 // 2 - 90)


def test_modal_state_open_close_round_trip():
    state = ModalState()
    assert state.visible is False
    state.open(verb="focus", items=[{"n": 1, "label": "Chrome"}])
    assert state.visible is True
    assert state.verb == "focus"
    assert state.rows == ["1. Chrome"]
    assert state.cards == [{"n": 1, "app": "Chrome", "title": ""}]
    state.close()
    assert state.visible is False
    assert state.rows == []
    assert state.cards == []


def test_format_cards_uses_explicit_app_title():
    cards = format_cards([
        {"n": 1, "label": "Chrome — voice-commander", "app": "Chrome", "title": "voice-commander"},
        {"n": 2, "label": "VS Code — picker_modal.py", "app": "VS Code", "title": "picker_modal.py"},
    ])
    assert cards == [
        {"n": 1, "app": "Chrome", "title": "voice-commander"},
        {"n": 2, "app": "VS Code", "title": "picker_modal.py"},
    ]


def test_format_cards_falls_back_to_splitting_label():
    """Older daemons send only ``label``; the modal still renders cards."""
    cards = format_cards([
        {"n": 1, "label": "Notepad — Untitled"},
        {"n": 2, "label": "Calculator"},
    ])
    assert cards == [
        {"n": 1, "app": "Notepad", "title": "Untitled"},
        {"n": 2, "app": "Calculator", "title": ""},
    ]


def test_default_geometry_fits_seven_cards():
    geometry = ModalGeometry()
    # Sanity: width comfortably wider than old 320, tall enough for 7 rows.
    assert geometry.width >= 480
    assert geometry.height >= 400
    assert MAX_CARDS == 7
