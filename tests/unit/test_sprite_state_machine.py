from voice_sprite.state_machine import StateMachine


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
    assert result is None
    assert sm.dictating is False
