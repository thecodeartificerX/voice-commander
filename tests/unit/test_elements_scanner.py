"""Unit tests for elements scanner filtering + indexing (ADR 0087)."""

from voice_commander.elements.scanner import Element, RawControl, build_elements

WINDOW = (0, 0, 1000, 800)  # left, top, right, bottom


def _raw(name: str, bounds: tuple[int, int, int, int], *, offscreen: bool = False) -> RawControl:
    return RawControl(name=name, control_type="ButtonControl", bounds=bounds, is_offscreen=offscreen)


def test_keeps_visible_controls_and_indexes_in_reading_order() -> None:
    raw = [
        _raw("bottom", (10, 500, 110, 540)),
        _raw("top-right", (400, 20, 500, 60)),
        _raw("top-left", (10, 20, 110, 60)),
    ]
    result = build_elements(raw, WINDOW, max_elements=200)
    assert [e.index for e in result] == [1, 2, 3]
    assert [e.label for e in result] == ["top-left", "top-right", "bottom"]


def test_computes_rect_and_center() -> None:
    raw = [_raw("b", (100, 200, 140, 260))]
    [el] = build_elements(raw, WINDOW, max_elements=200)
    assert el.rect == (100, 200, 40, 60)
    assert el.center == (120, 230)


def test_drops_offscreen_controls() -> None:
    raw = [_raw("hidden", (10, 20, 110, 60), offscreen=True)]
    assert build_elements(raw, WINDOW, max_elements=200) == []


def test_drops_zero_area_controls() -> None:
    raw = [_raw("zero", (10, 20, 10, 60)), _raw("neg", (50, 50, 40, 40))]
    assert build_elements(raw, WINDOW, max_elements=200) == []


def test_drops_controls_outside_window_bounds() -> None:
    raw = [_raw("offscreen-right", (1100, 20, 1200, 60))]
    assert build_elements(raw, WINDOW, max_elements=200) == []


def test_dedupes_identical_rects() -> None:
    raw = [_raw("a", (10, 20, 110, 60)), _raw("b", (10, 20, 110, 60))]
    result = build_elements(raw, WINDOW, max_elements=200)
    assert len(result) == 1
    assert result[0].label == "a"


def test_caps_at_max_elements() -> None:
    raw = [_raw(f"b{i}", (10, i * 10, 110, i * 10 + 8)) for i in range(50)]
    result = build_elements(raw, WINDOW, max_elements=10)
    assert len(result) == 10
    assert [e.index for e in result] == list(range(1, 11))


def test_element_is_frozen() -> None:
    el = Element(index=1, label="x", control_type="ButtonControl", rect=(0, 0, 1, 1), center=(0, 0))
    import dataclasses
    import pytest

    with pytest.raises(dataclasses.FrozenInstanceError):
        el.index = 2  # type: ignore[misc]
