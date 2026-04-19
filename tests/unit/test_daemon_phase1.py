import threading
from unittest.mock import MagicMock

import pytest

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


@pytest.mark.hardware
def test_clean_shutdown_via_run(tmp_path):
    """daemon.run() unblocks within 2 s when shutdown() is called from another thread.

    Marked hardware because run() calls HotkeyController.start() which installs
    a global OS keyboard hook — not safe in pure unit-test CI environments.
    """
    feedback = CapturingFeedbackSink()
    recorder = MagicMock()
    recorder.is_recording = False

    daemon = Phase1Daemon(feedback=feedback, recorder=recorder)

    bg = threading.Thread(target=daemon.run, args=("scroll_lock",), daemon=True)
    bg.start()

    # Give the listener thread a moment to start, then request shutdown.
    import time
    time.sleep(0.2)
    daemon.shutdown()

    bg.join(timeout=2.0)
    assert not bg.is_alive(), "daemon.run() did not return within 2 s after shutdown()"


def test_shutdown_idempotent():
    """Calling shutdown() twice must not raise, even without ever calling run()."""
    feedback = CapturingFeedbackSink()
    recorder = MagicMock()
    recorder.is_recording = False

    daemon = Phase1Daemon(feedback=feedback, recorder=recorder)

    # Neither call should raise.
    daemon.shutdown()
    daemon.shutdown()


def test_shutdown_stops_recording_if_active(tmp_path):
    """shutdown() must stop an in-progress recording."""
    feedback = CapturingFeedbackSink()
    recorder = MagicMock()
    recorder.is_recording = True  # pretend recording is active
    recorder.stop.return_value = tmp_path / "out.wav"

    daemon = Phase1Daemon(feedback=feedback, recorder=recorder)
    daemon.shutdown()

    recorder.stop.assert_called_once()
