from __future__ import annotations

import logging
import signal
import threading
from pathlib import Path

from .config import Config
from .feedback import FeedbackSink, WindowsFeedbackSink
from .hotkey import HotkeyController
from .recorder import Recorder

logger = logging.getLogger(__name__)


class Phase1Daemon:
    """Phase-1 daemon: hotkey + recorder + chimes. No transcription."""

    def __init__(
        self,
        feedback: FeedbackSink,
        recorder: Recorder,
    ) -> None:
        self._feedback = feedback
        self._recorder = recorder
        self._hotkey: HotkeyController | None = None
        self._shutdown = threading.Event()

    def on_toggle(self) -> None:
        if self._recorder.is_recording:
            try:
                path = self._recorder.stop()
                self._feedback.on_recording_stop()
                logger.info("Recording saved: %s", path)
            except Exception as e:
                self._feedback.on_error("recorder.stop", e)
        else:
            try:
                self._recorder.start()
                self._feedback.on_recording_start()
            except Exception as e:
                self._feedback.on_error("recorder.start", e)

    def run(self, hotkey_key: str) -> None:
        self._hotkey = HotkeyController(hotkey_key, self.on_toggle)
        self._hotkey.start()
        # Belt-and-braces SIGINT handler — useful on POSIX or if another thread
        # handles the signal.  On Windows the polled wait below is the primary
        # Ctrl+C mechanism because kernel WaitForSingleObject (used by a bare
        # threading.Event.wait()) never yields back to the Python interpreter to
        # service SIGINT, so the lambda below would never fire without the poll.
        signal.signal(signal.SIGINT, lambda *_: self.shutdown())
        logger.info("Phase1Daemon running. Press Ctrl+C to exit.")
        try:
            # Poll every 0.5 s so the Python interpreter can service SIGINT
            # between iterations.  See docs/gotchas.md §9 for the full story.
            while not self._shutdown.wait(0.5):
                pass
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received, shutting down")
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        # Idempotent: if the event is already set we have already torn down.
        if self._shutdown.is_set():
            return
        self._shutdown.set()
        if self._hotkey is not None:
            self._hotkey.stop()
            self._hotkey = None
        if self._recorder.is_recording:
            try:
                self._recorder.stop()
            except Exception:
                pass


def build_phase1(cfg: Config) -> Phase1Daemon:
    recorder = Recorder(
        output_dir=Path(cfg.audio.output_dir),
        sample_rate=cfg.audio.sample_rate,
        channels=cfg.audio.channels,
        device=cfg.audio.device if cfg.audio.device >= 0 else None,
        retention_count=cfg.audio.retention_count,
    )
    recorder.enforce_retention()
    feedback = WindowsFeedbackSink(sounds_dir=Path(cfg.feedback.sounds_dir))
    return Phase1Daemon(feedback=feedback, recorder=recorder)
