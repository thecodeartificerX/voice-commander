import numpy as np

from voice_commander.dictation.session import DictationSession


class _FakeBus:
    def __init__(self):
        self.events = []

    def publish(self, event_type, data=None):
        self.events.append((event_type, data or {}))


def _audio(n=8000):
    return np.ones(n, dtype=np.float32)


def test_start_publishes_and_activates():
    bus = _FakeBus()
    s = DictationSession(bus)
    assert not s.active
    s.start()
    assert s.active
    assert bus.events == [("dictation.start", {})]


def test_buffers_non_end_utterances():
    bus = _FakeBus()
    s = DictationSession(bus)
    s.start()
    assert s.handle_utterance(_audio(), "hello there") == "buffered"
    assert s.handle_utterance(_audio(), "more words") == "buffered"
    audio = s.take_audio()
    assert audio is not None
    assert audio.shape[0] == 16000


def test_end_word_returns_end_and_is_not_buffered():
    bus = _FakeBus()
    s = DictationSession(bus, end_word="done")
    s.start()
    s.handle_utterance(_audio(), "some prose")
    assert s.handle_utterance(_audio(), "done") == "end"
    audio = s.take_audio()
    assert audio is not None
    assert audio.shape[0] == 8000  # only the one buffered utterance


def test_end_word_match_is_exact_standalone():
    bus = _FakeBus()
    s = DictationSession(bus, end_word="done")
    s.start()
    # "I am done with this" must NOT end dictation.
    assert s.handle_utterance(_audio(), "I am done with this") == "buffered"
    # Punctuation / case tolerated on the standalone word.
    assert s.handle_utterance(_audio(), "Done.") == "end"


def test_empty_buffer_take_audio_is_none():
    s = DictationSession(_FakeBus())
    s.start()
    assert s.take_audio() is None


def test_finish_deactivates_and_publishes():
    bus = _FakeBus()
    s = DictationSession(bus)
    s.start()
    s.finish()
    assert not s.active
    assert ("dictation.end", {"reason": "done"}) in bus.events


def test_cancel_deactivates_and_publishes():
    bus = _FakeBus()
    s = DictationSession(bus)
    s.start()
    s.cancel()
    assert not s.active
    assert ("dictation.end", {"reason": "cancel"}) in bus.events


def test_handle_utterance_when_inactive_is_noop():
    s = DictationSession(_FakeBus())
    assert s.handle_utterance(_audio(), "anything") == "buffered"
    assert s.take_audio() is None


def test_finish_when_inactive_is_noop():
    bus = _FakeBus()
    s = DictationSession(bus)
    s.finish()  # never started
    assert not s.active
    assert bus.events == []


def test_take_and_finish_returns_audio_and_deactivates():
    bus = _FakeBus()
    s = DictationSession(bus)
    s.start()
    s.handle_utterance(_audio(), "hello")
    audio = s.take_and_finish()
    assert audio is not None
    assert audio.shape[0] == 8000
    assert not s.active
    assert ("dictation.end", {"reason": "done"}) in bus.events


def test_take_and_finish_empty_buffer_returns_none():
    bus = _FakeBus()
    s = DictationSession(bus)
    s.start()
    assert s.take_and_finish() is None
    assert not s.active


def test_take_and_finish_when_inactive_returns_none_and_is_silent():
    bus = _FakeBus()
    s = DictationSession(bus)
    # never started — the "lost the race" case
    assert s.take_and_finish() is None
    assert bus.events == []


# ---------------------------------------------------------------------------
# Cancel-word tests (Task 1 — ADR 0089)
# ---------------------------------------------------------------------------


def test_cancel_word_returns_cancel_and_does_not_buffer():
    """handle_utterance returns "cancel" on exact cancel-word match and
    does NOT append the audio chunk to the buffer."""
    bus = _FakeBus()
    s = DictationSession(bus, end_word="done", cancel_word="cancel")
    s.start()
    audio = _audio()
    assert s.handle_utterance(audio, "cancel") == "cancel"
    # Audio must NOT have been buffered — take_audio returns None
    assert s.take_audio() is None


def test_cancel_word_normalized_match():
    """Normalization (lowercase, strip punctuation) applies to cancel word."""
    bus = _FakeBus()
    s = DictationSession(bus, end_word="done", cancel_word="cancel")
    s.start()
    audio = _audio()
    # "Cancel." normalizes to "cancel"
    assert s.handle_utterance(audio, "Cancel.") == "cancel"
    assert s.take_audio() is None


def test_cancel_word_partial_phrase_is_buffered():
    """A transcript containing cancel word as part of a longer phrase is buffered."""
    bus = _FakeBus()
    s = DictationSession(bus, end_word="done", cancel_word="cancel")
    s.start()
    audio = _audio()
    # "please cancel that" must NOT trigger cancel — it is not the exact word
    assert s.handle_utterance(audio, "please cancel that") == "buffered"
    assert s.take_audio() is not None


def test_cancel_word_when_inactive_returns_buffered():
    """handle_utterance returns "buffered" (no-op) when session is inactive,
    even if the transcript matches the cancel word."""
    bus = _FakeBus()
    s = DictationSession(bus, end_word="done", cancel_word="cancel")
    # Never started — inactive
    audio = _audio()
    assert s.handle_utterance(audio, "cancel") == "buffered"
    assert s.take_audio() is None


def test_cancel_word_collision_with_end_word_disables_cancel(caplog):
    """When cancel_word == end_word, __init__ logs a WARNING and sets
    _cancel_word = None, so the collision word buffers normally."""
    import logging
    bus = _FakeBus()
    with caplog.at_level(logging.WARNING, logger="voice_commander.dictation.session"):
        s = DictationSession(bus, end_word="done", cancel_word="done")
    # Warning must have been emitted
    assert any("cancel" in r.message.lower() or "collision" in r.message.lower()
               for r in caplog.records)
    # _cancel_word must be None — spoken cancel disabled
    assert s._cancel_word is None
    # The collision word now acts as the end word, not the cancel word
    s.start()
    audio = _audio()
    assert s.handle_utterance(audio, "done") == "end"


def test_cancel_word_empty_string_disables_cancel(caplog):
    """An empty or whitespace-only cancel_word logs a WARNING and disables
    spoken cancel (_cancel_word = None)."""
    import logging
    bus = _FakeBus()
    with caplog.at_level(logging.WARNING, logger="voice_commander.dictation.session"):
        s = DictationSession(bus, end_word="done", cancel_word="")
    assert any("cancel" in r.message.lower() or "empty" in r.message.lower()
               or "cancel_word" in r.message.lower()
               for r in caplog.records)
    assert s._cancel_word is None
