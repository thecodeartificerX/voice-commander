from __future__ import annotations

from voice_sprite.state_machine import StateMachine


def test_mode_enter_sets_badge() -> None:
    sm = StateMachine()
    sm.on_event("mode.enter", {"name": "video", "badge": "🎬 VIDEO"})
    assert sm.active_mode_badge == "🎬 VIDEO"


def test_mode_exit_clears_badge() -> None:
    sm = StateMachine()
    sm.on_event("mode.enter", {"name": "video", "badge": "🎬 VIDEO"})
    sm.on_event("mode.exit", {"name": "video", "reason": "end_phrase"})
    assert sm.active_mode_badge is None


def test_mode_enter_falls_back_to_name_when_no_badge() -> None:
    sm = StateMachine()
    sm.on_event("mode.enter", {"name": "mouseless"})
    assert sm.active_mode_badge == "MOUSELESS"


class _FakeWindow:
    def __init__(self) -> None:
        self.badge: str | None = "sentinel"

    def set_mode_badge(self, text: str | None) -> None:
        self.badge = text


def test_apply_mode_badge_pushes_to_window() -> None:
    from voice_sprite.__main__ import _apply_mode_badge

    sm = StateMachine()
    win = _FakeWindow()
    _apply_mode_badge(sm, win)
    assert win.badge is None  # no mode active
    sm.on_event("mode.enter", {"name": "video", "badge": "🎬 VIDEO"})
    _apply_mode_badge(sm, win)
    assert win.badge == "🎬 VIDEO"
