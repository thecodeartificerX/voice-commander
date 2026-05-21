from voice_sprite.state_machine import SpriteState, is_dim

# Bright = listening: any in-session pose with the mic logically engaged.
BRIGHT_STATES = {
    SpriteState.LISTENING,
    SpriteState.HEARING_SPEECH,
    SpriteState.THINKING,
    SpriteState.LLM_THINKING,
    SpriteState.SUCCESS,
    SpriteState.MISS,
    SpriteState.TOOL_ERROR,
}
# Dark = not listening: no active session, plus the dictation decode wait.
DARK_STATES = {
    SpriteState.IDLE,
    SpriteState.WARMUP,
    SpriteState.CRASHED,
    SpriteState.PROCESSING,
}


def test_bright_states_not_dim_when_not_processing():
    for state in BRIGHT_STATES:
        assert is_dim(state, processing=False) is False, state


def test_dark_states_dim_when_not_processing():
    for state in DARK_STATES:
        assert is_dim(state, processing=False) is True, state


def test_processing_flag_forces_dim_for_every_state():
    for state in SpriteState:
        assert is_dim(state, processing=True) is True, state


def test_truth_table_covers_every_state():
    assert BRIGHT_STATES | DARK_STATES == set(SpriteState)
