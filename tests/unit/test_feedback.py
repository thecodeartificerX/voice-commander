from unittest.mock import patch

from voice_commander.feedback import CapturingFeedbackSink, NullFeedbackSink, WindowsFeedbackSink


def test_null_sink_all_methods_noop():
    sink = NullFeedbackSink()
    sink.on_recording_start()
    sink.on_recording_stop()
    sink.on_transcript("hello", 0.9)
    sink.on_match("copy", "copy", 100.0)
    sink.on_miss("huh", ())
    sink.on_error("x", RuntimeError("y"))


def test_capturing_sink_records_calls():
    sink = CapturingFeedbackSink()
    sink.on_recording_start()
    sink.on_match("copy", "copy", 95.0)
    sink.on_miss("nope", (("paste", "paste", 40.0),))
    assert [c[0] for c in sink.calls] == ["on_recording_start", "on_match", "on_miss"]
    assert sink.calls[1][1] == ("copy", "copy", 95.0)


def test_windows_sink_on_error_plays_miss_chime(tmp_path):
    """on_error must play the miss chime so the user hears that something broke."""
    miss_wav = tmp_path / "miss.wav"
    miss_wav.write_bytes(b"RIFF")
    sink = WindowsFeedbackSink(sounds_dir=tmp_path, miss_sound="miss.wav")
    with patch("voice_commander.feedback.winsound.PlaySound") as play:
        sink.on_error("audio", RuntimeError("device lost"))
    assert play.called, "on_error should play miss chime"


def test_windows_sink_on_recording_start_stop_are_silent(tmp_path):
    """ADR 0014: start/stop chimes are silent."""
    sink = WindowsFeedbackSink(sounds_dir=tmp_path)
    with patch("voice_commander.feedback.winsound.PlaySound") as play:
        sink.on_recording_start()
        sink.on_recording_stop()
    assert not play.called, "start/stop must be silent per ADR 0014"
