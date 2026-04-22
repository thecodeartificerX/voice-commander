"""Tests for SpriteRenderer — animation timing, frame selection, transitions."""

from __future__ import annotations

from voice_sprite.charsheet import AnimInfo, CharSheet
from voice_sprite.sprite_renderer import SpriteRenderer
from voice_sprite.state_machine import SpriteState


def _make_charsheet() -> CharSheet:
    """Build a minimal charsheet with 4-frame anims per state and one transition."""
    states = {s: AnimInfo(row=i, frames=4) for i, s in enumerate(SpriteState)}
    transitions = {
        (SpriteState.LISTENING, SpriteState.IDLE): AnimInfo(row=20, frames=3, once=True),
    }
    return CharSheet(
        frame_width=128,
        frame_height=128,
        fps=12,
        states=states,
        transitions=transitions,
    )


class TestSpriteRendererInit:
    def test_initial_state_is_warmup(self):
        r = SpriteRenderer(_make_charsheet())
        assert r._state == SpriteState.WARMUP

    def test_initial_frame_region_is_valid(self):
        r = SpriteRenderer(_make_charsheet())
        x, y, w, h = r.frame_region
        assert w == 128
        assert h == 128
        assert x == 0  # frame 0

    def test_charsheet_property(self):
        cs = _make_charsheet()
        r = SpriteRenderer(cs)
        assert r.charsheet is cs


class TestSetState:
    def test_set_state_without_transition(self):
        r = SpriteRenderer(_make_charsheet())
        r.set_state(SpriteState.IDLE)
        x, y, w, h = r.frame_region
        idle_row = r.charsheet.states[SpriteState.IDLE].row
        assert y == idle_row * 128
        assert x == 0  # reset to frame 0

    def test_set_state_with_transition(self):
        r = SpriteRenderer(_make_charsheet())
        r.set_state(SpriteState.LISTENING)  # get to LISTENING first
        r.set_state(SpriteState.IDLE)  # LISTENING→IDLE has transition
        x, y, w, h = r.frame_region
        assert y == 20 * 128  # transition row

    def test_set_state_resets_frame_index(self):
        r = SpriteRenderer(_make_charsheet())
        r.set_state(SpriteState.IDLE)
        r.tick(1.0 / 12)  # advance one frame
        r.set_state(SpriteState.LISTENING)  # switch state
        x, y, w, h = r.frame_region
        assert x == 0  # frame reset to 0


class TestTick:
    def test_tick_advances_frame(self):
        r = SpriteRenderer(_make_charsheet())
        r.set_state(SpriteState.IDLE)
        x_before = r.frame_region[0]
        r.tick(1.0 / 12)  # advance exactly one frame
        x_after = r.frame_region[0]
        assert x_after == x_before + 128  # next frame

    def test_tick_loops_animation(self):
        r = SpriteRenderer(_make_charsheet())
        r.set_state(SpriteState.IDLE)  # 4 frames, looping
        # Advance past all 4 frames
        for _ in range(5):
            r.tick(1.0 / 12)
        # Should loop back to frame 1 (index wrapped)
        x, y, w, h = r.frame_region
        assert x == 128  # frame index 1 after wrapping

    def test_tick_no_crash_with_no_anim(self):
        r = SpriteRenderer(_make_charsheet())
        r._current_anim = None
        r.tick(1.0 / 12)  # should not crash

    def test_once_transition_completes_to_target(self):
        r = SpriteRenderer(_make_charsheet())
        r.set_state(SpriteState.LISTENING)
        r.set_state(SpriteState.IDLE)  # triggers once=True transition (3 frames)
        # Tick through all 3 transition frames
        for _ in range(4):
            r.tick(1.0 / 12)
        # Should now be on IDLE state anim, not transition
        x, y, w, h = r.frame_region
        idle_row = r.charsheet.states[SpriteState.IDLE].row
        assert y == idle_row * 128
        assert r._transition_anim is None
        assert r._transition_done is True

    def test_looping_animation_does_not_complete(self):
        r = SpriteRenderer(_make_charsheet())
        r.set_state(SpriteState.IDLE)  # 4 frames, not once
        for _ in range(20):
            r.tick(1.0 / 12)
        # Should still be on IDLE anim, looping
        assert r._transition_done is False


class TestFrameRegion:
    def test_frame_region_dimensions_match_charsheet(self):
        r = SpriteRenderer(_make_charsheet())
        _, _, w, h = r.frame_region
        assert w == 128
        assert h == 128

    def test_frame_region_x_advances_by_frame_width(self):
        r = SpriteRenderer(_make_charsheet())
        r.set_state(SpriteState.IDLE)
        regions = []
        for _ in range(4):
            regions.append(r.frame_region)
            r.tick(1.0 / 12)
        xs = [reg[0] for reg in regions]
        assert xs == [0, 128, 256, 384]

    def test_frame_region_fallback_when_no_anim(self):
        r = SpriteRenderer(_make_charsheet())
        r._current_anim = None
        x, y, w, h = r.frame_region
        assert (x, y) == (0, 0)
        assert w == 128 and h == 128


class TestReloadCharsheet:
    def test_reload_resets_to_current_state_anim(self):
        r = SpriteRenderer(_make_charsheet())
        r.set_state(SpriteState.IDLE)
        r.tick(0.5)  # advance partway
        new_cs = _make_charsheet()
        r.reload_charsheet(new_cs)
        assert r.charsheet is new_cs
        assert r._frame_index == 0  # reset
        assert r._transition_anim is None

    def test_reload_clears_transition(self):
        r = SpriteRenderer(_make_charsheet())
        r.set_state(SpriteState.LISTENING)
        r.set_state(SpriteState.IDLE)  # start transition
        assert r._transition_anim is not None
        r.reload_charsheet(_make_charsheet())
        assert r._transition_anim is None
