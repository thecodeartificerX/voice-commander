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
