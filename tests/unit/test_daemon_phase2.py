import queue
import threading
from pathlib import Path
from unittest.mock import MagicMock
from voice_commander.daemon import Phase2Daemon
from voice_commander.transcriber import TranscriptionResult
from voice_commander.feedback import CapturingFeedbackSink


def test_worker_drains_queue_and_reports_transcript(tmp_path):
    feedback = CapturingFeedbackSink()
    recorder = MagicMock()
    transcriber = MagicMock()
    transcriber.transcribe.return_value = TranscriptionResult(
        text="hello world", language="en", duration_ms=1000, confidence=0.9
    )
    daemon = Phase2Daemon(feedback=feedback, recorder=recorder, transcriber=transcriber)
    daemon.start_worker()

    fake_wav = tmp_path / "x.wav"
    fake_wav.write_bytes(b"")
    daemon._queue.put(fake_wav)
    daemon._queue.put(None)  # poison pill
    daemon._worker.join(timeout=2.0)

    assert any(call[0] == "on_transcript" for call in feedback.calls)
    transcript_calls = [c for c in feedback.calls if c[0] == "on_transcript"]
    assert transcript_calls[0][1] == ("hello world", 0.9)
