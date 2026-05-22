"""Regression: the cat must leave the dimmed PROCESSING pose when dictation ends.

Bug: ``dictation.processing`` parks the StateMachine at ``PROCESSING`` (a dim
state) and ``dictation.end`` only cleared the ``processing`` flag — it never
restored ``target_state``/``current_state`` off ``PROCESSING``. Because
``is_dim(PROCESSING, ...)`` is True regardless of the flag, the cat stayed dimmed
after the server round-trip finished, even though the voice session was still
listening.

End criterion (ADR 0099): after ``dictation.end`` the cat returns to its
pre-dictation visual — bright LISTENING when a session is still open, dim IDLE
when the (dictation-owned) session has closed.
"""

from __future__ import annotations

from voice_sprite.state_machine import SpriteState, StateMachine, is_dim


def _drive(sm: StateMachine, events: list[tuple[str, dict]]) -> None:
    for event_type, data in events:
        sm.on_event(event_type, data)


def test_dictation_end_in_open_session_returns_bright_listening() -> None:
    """Scroll-Lock session stays open after dictation → bright LISTENING.

    No ``session_stopped`` is published in this path; the session keeps
    listening, so the cat must be bright once processing finishes.
    """
    sm = StateMachine()
    _drive(
        sm,
        [
            ("session_started", {}),
            ("dictation.start", {}),
            ("dictation.processing", {}),
            ("dictation.end", {"reason": "done"}),
        ],
    )

    assert sm.target_state == SpriteState.LISTENING
    assert sm.current_state == SpriteState.LISTENING
    assert is_dim(sm.target_state, sm.processing) is False


def test_dictation_end_returns_listening_state_for_renderer() -> None:
    """on_event('dictation.end') must return the restored state so the renderer
    leaves the frozen PROCESSING pose (not None, which would keep the old pose)."""
    sm = StateMachine()
    _drive(
        sm,
        [
            ("session_started", {}),
            ("dictation.start", {}),
            ("dictation.processing", {}),
        ],
    )
    result = sm.on_event("dictation.end", {"reason": "done"})
    assert result == SpriteState.LISTENING


def test_dictation_end_in_owned_session_returns_dim_idle() -> None:
    """Right-Ctrl-owned session auto-closes on dictation end → dim IDLE.

    Real SSE order on the owned-session close path: ``session_stopped`` is
    published (by ``_end_owned_session_if_needed``) *before* ``dictation.processing``
    / ``dictation.end`` (by ``_finalize_dictation``). The cat must end dim IDLE.
    """
    sm = StateMachine()
    _drive(
        sm,
        [
            ("session_started", {}),
            ("dictation.start", {}),
            ("session_stopped", {}),  # owned session closed before finalize
            ("dictation.processing", {}),
            ("dictation.end", {"reason": "done"}),
        ],
    )

    assert sm.target_state == SpriteState.IDLE
    assert is_dim(sm.target_state, sm.processing) is True


def test_scroll_lock_close_during_processing_returns_dim_idle() -> None:
    """Race: user presses Scroll Lock during the server wait → session closes.

    ``session_stopped`` arrives mid-processing. The cat must honour the closed
    session and end dim IDLE, not snap back to bright LISTENING.
    """
    sm = StateMachine()
    _drive(
        sm,
        [
            ("session_started", {}),
            ("dictation.start", {}),
            ("dictation.processing", {}),
            ("session_stopped", {}),  # Scroll Lock pressed during decode wait
            ("dictation.end", {"reason": "done"}),
        ],
    )

    assert sm.target_state == SpriteState.IDLE
    assert is_dim(sm.target_state, sm.processing) is True


def test_cancel_path_without_processing_restores_open_session_bright() -> None:
    """Spoken cancel in an open session skips processing; cat stays bright."""
    sm = StateMachine()
    _drive(
        sm,
        [
            ("session_started", {}),
            ("dictation.start", {}),
            ("dictation.end", {"reason": "cancel"}),
        ],
    )

    assert is_dim(sm.target_state, sm.processing) is False
    assert sm.cancelled_cue is True
