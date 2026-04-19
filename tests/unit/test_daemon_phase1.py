from unittest.mock import MagicMock
from voice_commander.daemon import Phase1Daemon
from voice_commander.feedback import CapturingFeedbackSink


def test_toggle_start_stop_flow(tmp_path):
    feedback = CapturingFeedbackSink()
    recorder = MagicMock()
    recorder.is_recording = False
    def _start(): recorder.is_recording = True
    def _stop():
        recorder.is_recording = False
        return tmp_path / "out.wav"
    recorder.start.side_effect = _start
    recorder.stop.side_effect = _stop

    daemon = Phase1Daemon(feedback=feedback, recorder=recorder)

    daemon.on_toggle()  # start
    daemon.on_toggle()  # stop

    assert [c[0] for c in feedback.calls] == ["on_recording_start", "on_recording_stop"]
    recorder.start.assert_called_once()
    recorder.stop.assert_called_once()
