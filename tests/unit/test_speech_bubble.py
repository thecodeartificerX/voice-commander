from voice_sprite.speech_bubble import SpeechBubble


def test_initial_state_not_visible():
    b = SpeechBubble()
    assert b.visible is False
    assert b.text == ""
    assert b.opacity == 0.0


def test_show_makes_visible():
    b = SpeechBubble(fade_ms=2000)
    b.show("copy")
    assert b.visible is True
    assert b.text == "copy"
    assert b.opacity == 1.0


def test_tick_fades():
    b = SpeechBubble(fade_ms=1000)
    b.show("paste")
    b.tick(0.5)
    assert b.visible is True
    assert 0.4 < b.opacity < 0.6


def test_tick_past_duration_hides():
    b = SpeechBubble(fade_ms=1000)
    b.show("paste")
    b.tick(1.1)
    assert b.visible is False
    assert b.text == ""


def test_show_resets_timer():
    b = SpeechBubble(fade_ms=1000)
    b.show("first")
    b.tick(0.8)
    b.show("second")
    assert b.text == "second"
    assert b.opacity == 1.0


def test_zero_fade_ms():
    b = SpeechBubble(fade_ms=0)
    b.show("instant")
    assert b.opacity == 0.0
