from voice_sprite.state_machine import SpriteState, StateMachine


def test_dictation_start_sets_flag_no_state_change():
    sm = StateMachine()
    assert sm.dictating is False
    result = sm.on_event("dictation.start", {})
    assert result is None  # no SpriteState change
    assert sm.dictating is True


def test_dictation_end_clears_flag():
    sm = StateMachine()
    sm.on_event("dictation.start", {})
    result = sm.on_event("dictation.end", {})
    # ADR 0099: dictation.end restores the pre-dictation pose so the cat leaves
    # the dimmed PROCESSING state. No session was opened here → IDLE.
    assert result == SpriteState.IDLE
    assert sm.dictating is False


def test_dictation_end_without_prior_start_is_idempotent():
    sm = StateMachine()
    result = sm.on_event("dictation.end", {})
    assert result == SpriteState.IDLE  # ADR 0099: restores to IDLE (no open session)
    assert sm.dictating is False


def test_repeated_dictation_start_is_idempotent():
    sm = StateMachine()
    sm.on_event("dictation.start", {})
    result = sm.on_event("dictation.start", {})
    assert result is None
    assert sm.dictating is True


# ---------------------------------------------------------------------------
# Dictation cancel-cue tests (Task 8 — ADR 0089)
# ---------------------------------------------------------------------------


def test_dictation_end_cancel_reason_sets_cancelled_cue():
    """dictation.end with reason='cancel' sets dictating=False AND
    sets a distinct cancelled_cue flag on the state machine."""
    from voice_sprite.state_machine import StateMachine

    sm = StateMachine()
    sm.on_event("dictation.start", {})
    assert sm.dictating is True

    sm.on_event("dictation.end", {"reason": "cancel"})
    assert sm.dictating is False, "dictating must be False after cancel"
    assert sm.cancelled_cue is True, (
        "cancelled_cue must be True when reason='cancel' — "
        "sprite needs a distinct visual to surface"
    )


def test_dictation_end_done_reason_does_not_set_cancelled_cue():
    """dictation.end with reason='done' (normal finish) must NOT set
    cancelled_cue — only 'cancel' reason triggers the cue."""
    from voice_sprite.state_machine import StateMachine

    sm = StateMachine()
    sm.on_event("dictation.start", {})
    sm.on_event("dictation.end", {"reason": "done"})
    assert sm.dictating is False
    assert sm.cancelled_cue is False, (
        "cancelled_cue must stay False for a normal 'done' end"
    )


def test_dictation_end_cancel_reason_retroactively_covers_scroll_lock_cancel():
    """scroll-lock cancel also emits reason='cancel' — the same sprite update
    covers both spoken cancel and scroll-lock cancel."""
    from voice_sprite.state_machine import StateMachine

    sm = StateMachine()
    sm.on_event("dictation.start", {})
    # Simulate scroll-lock cancel (also emits {"reason": "cancel"})
    sm.on_event("dictation.end", {"reason": "cancel"})
    assert sm.cancelled_cue is True


def test_cancelled_cue_resets_on_new_dictation_start():
    """Starting a new dictation session clears cancelled_cue so the prior
    cancel visual does not bleed into the new session."""
    from voice_sprite.state_machine import StateMachine

    sm = StateMachine()
    sm.on_event("dictation.start", {})
    sm.on_event("dictation.end", {"reason": "cancel"})
    assert sm.cancelled_cue is True  # sanity

    # New session start must reset the cue
    sm.on_event("dictation.start", {})
    assert sm.cancelled_cue is False, (
        "cancelled_cue must be cleared when a new dictation session starts"
    )


def test_dictation_end_missing_reason_key_does_not_raise():
    """dictation.end with no 'reason' key in data must not KeyError.

    Other dictation.end publishers may omit the reason key; the handler must
    use data.get('reason') defensively (REV 8).
    """
    from voice_sprite.state_machine import StateMachine

    sm = StateMachine()
    sm.on_event("dictation.start", {})
    # No 'reason' key — must not raise KeyError
    sm.on_event("dictation.end", {})
    assert sm.dictating is False
    # Missing reason → not "cancel" → cancelled_cue stays False
    assert sm.cancelled_cue is False
