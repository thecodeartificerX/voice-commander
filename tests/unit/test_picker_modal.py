from __future__ import annotations

from voice_sprite.picker_modal import (
    ModalGeometry,
    ModalState,
    compute_modal_position,
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
    state.close()
    assert state.visible is False
    assert state.rows == []
