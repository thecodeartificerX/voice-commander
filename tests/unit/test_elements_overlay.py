"""Unit tests for elements-overlay tag geometry (ADR 0087)."""

from voice_sprite.elements_overlay import tag_xy


def test_tag_xy_flips_to_pyglet_origin_on_primary_monitor() -> None:
    # Element at screen (100, 50), monitor 0..1080 tall, tag 22px high.
    # pyglet origin is bottom-left, so y = 1080 - 50 - 22 = 1008.
    assert tag_xy((100, 50, 80, 30), (0, 0, 1920, 1080), tag_w=20, tag_h=22) == (100, 1008)


def test_tag_xy_is_monitor_local_on_secondary_monitor() -> None:
    # Monitor starts at x=1920; element at screen x=2000 -> local x=80.
    assert tag_xy((2000, 100, 60, 24), (1920, 0, 3840, 1080), tag_w=20, tag_h=22) == (80, 958)


def test_tag_xy_handles_nonzero_monitor_top() -> None:
    # Monitor top at y=-200, height 1080; element at screen y=0 -> local 200.
    # y = 1080 - 200 - 22 = 858.
    assert tag_xy((10, 0, 40, 40), (0, -200, 1920, 880), tag_w=20, tag_h=22) == (10, 858)
