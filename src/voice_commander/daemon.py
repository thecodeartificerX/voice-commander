from __future__ import annotations

import logging
import queue
import signal
import threading
from pathlib import Path

import numpy as np
import numpy.typing as npt
import soundfile as sf
from silero_vad import load_silero_vad

from .config import Config
from .dispatcher import Dispatcher
from .feedback import FeedbackSink, WindowsFeedbackSink
from .hotkey import HotkeyController
from .matcher import Matcher
from .registry import discover
from .streaming_recorder import StreamingRecorder
from .transcriber import Transcriber, TranscriptionResult
from .vad_gate import VADGate

logger = logging.getLogger(__name__)


class StreamingDaemon:
    """VAD-streaming daemon: hotkey toggles a live voice-command session.

    While a session is open, silero-vad auto-segments utterances on natural
    silence. Each utterance fires transcribe → gate → match → dispatch
    immediately. No keypresses between commands.
    """

    def __init__(
        self,
        feedback: FeedbackSink,
        recorder: StreamingRecorder,
        transcriber: Transcriber,
        matcher: Matcher,
        dispatcher: Dispatcher,
        *,
        min_confidence: float = 0.30,
        min_word_count: int = 1,
        max_no_speech_prob: float = 0.6,
        output_dir: str = "outputs",
    ) -> None:
        self._feedback = feedback
        self._recorder = recorder
        self._transcriber = transcriber
        self._matcher = matcher
        self._dispatcher = dispatcher
        self._min_confidence = min_confidence
        self._min_word_count = min_word_count
        self._max_no_speech_prob = max_no_speech_prob
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

        self._utt_q: queue.Queue[npt.NDArray[np.float32] | None] = queue.Queue(maxsize=8)
        self._pipeline_thread: threading.Thread | None = None
        self._hotkey: HotkeyController | None = None
        self._shutdown = threading.Event()

    # ------------------------------------------------------------------
    # Hotkey callback
    # ------------------------------------------------------------------

    def on_toggle(self) -> None:
        if self._recorder.is_open:
            self._recorder.close_session()
            self._feedback.on_recording_stop()
            logger.info("Session closed")
        else:
            try:
                self._recorder.open_session()
                self._feedback.on_recording_start()
                logger.info("Session opened")
            except Exception as e:
                self._feedback.on_error("recorder.open_session", e)

    # ------------------------------------------------------------------
    # Pipeline worker (transcribe → gate → match → dispatch)
    # ------------------------------------------------------------------

    def _pipeline_loop(self) -> None:
        while True:
            utterance = self._utt_q.get()
            if utterance is None:
                break
            try:
                self._process_utterance(utterance)
            except Exception as e:
                self._feedback.on_error("pipeline", e)

    def _process_utterance(self, utterance: npt.NDArray[np.float32]) -> None:
        # Async write for post-mortem debugging
        self._write_utterance_async(utterance)

        result: TranscriptionResult = self._transcriber.transcribe(utterance)
        self._feedback.on_transcript(result.text, result.confidence)

        # Gate: word-count
        word_count = len(result.text.split())
        if word_count < self._min_word_count:
            logger.debug("Gate: word-count %d < %d, dropping", word_count, self._min_word_count)
            return

        # Gate: no_speech_prob
        if result.no_speech_prob > self._max_no_speech_prob:
            logger.debug(
                "Gate: no_speech_prob %.2f > %.2f, dropping",
                result.no_speech_prob,
                self._max_no_speech_prob,
            )
            return

        # Gate: confidence
        if result.confidence < self._min_confidence:
            self._feedback.on_miss(result.text, ())
            return

        match = self._matcher.match(result.text)
        self._dispatcher.dispatch(result.text, match)

    def _write_utterance_async(self, utterance: npt.NDArray[np.float32]) -> None:
        path = self._output_dir / "last_utterance.wav"

        def _write() -> None:
            try:
                sf.write(path, utterance, 16000, subtype="FLOAT")
            except Exception:
                logger.exception("Failed to write %s", path)

        threading.Thread(target=_write, name="wav-writer", daemon=True).start()

    # ------------------------------------------------------------------
    # Utterance sink (called from StreamingRecorder's VAD worker thread)
    # ------------------------------------------------------------------

    def _on_utterance(self, utterance: npt.NDArray[np.float32]) -> None:
        try:
            self._utt_q.put_nowait(utterance)
        except queue.Full:
            logger.warning("utt_q full — dropping utterance (%d samples)", len(utterance))
            self._feedback.on_miss("(queue overflow)", ())

    # ------------------------------------------------------------------
    # Run / shutdown
    # ------------------------------------------------------------------

    def run(self, hotkey_key: str) -> None:
        # Load models before accepting hotkey presses.
        try:
            self._transcriber.load()
        except Exception as e:
            logger.exception("Transcriber.load() failed; aborting startup")
            self._feedback.on_error("transcriber.load", e)
            return

        # Start pipeline worker thread.
        self._pipeline_thread = threading.Thread(
            target=self._pipeline_loop,
            name="vc-pipeline",
            daemon=True,
        )
        self._pipeline_thread.start()

        # Start hotkey listener.
        try:
            self._hotkey = HotkeyController(hotkey_key, self.on_toggle)
            self._hotkey.start()
        except Exception as e:
            logger.exception("HotkeyController.start() failed; aborting startup")
            self._feedback.on_error("hotkey.start", e)
            return

        signal.signal(signal.SIGINT, lambda *_: self.shutdown())
        logger.info("StreamingDaemon running. Press Ctrl+C to exit.")
        try:
            while not self._shutdown.wait(0.5):
                pass
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received, shutting down")
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        if self._shutdown.is_set():
            return
        self._shutdown.set()

        # Close any open session.
        if self._recorder.is_open:
            try:
                self._recorder.close_session()
            except Exception:
                logger.exception("Error closing session during shutdown")

        # Stop hotkey listener.
        if self._hotkey is not None:
            self._hotkey.stop()
            self._hotkey = None

        # Poison pipeline queue.
        self._utt_q.put(None)
        if self._pipeline_thread is not None:
            self._pipeline_thread.join(timeout=5.0)
            if self._pipeline_thread.is_alive():
                logger.warning("Pipeline thread did not exit within 5 s")
            self._pipeline_thread = None

        # Release model.
        try:
            self._transcriber.unload()
        except Exception:
            logger.exception("Error unloading transcriber")


def build_streaming_daemon(cfg: Config) -> StreamingDaemon:
    """Factory: wire all subsystems into a StreamingDaemon."""
    import torch

    torch.set_num_threads(1)
    vad_model = load_silero_vad(onnx=True)

    vad_gate = VADGate(
        model=vad_model,
        threshold=cfg.vad.threshold,
        min_speech_ms=cfg.vad.min_speech_duration_ms,
        min_silence_ms=cfg.vad.min_silence_duration_ms,
        speech_pad_ms=cfg.vad.speech_pad_ms,
        pre_roll_ms=cfg.vad.pre_roll_ms,
        max_utterance_ms=cfg.vad.max_utterance_ms,
    )

    feedback = WindowsFeedbackSink(
        sounds_dir=Path(cfg.feedback.sounds_dir),
        start_sound=cfg.feedback.start_sound,
        stop_sound=cfg.feedback.stop_sound,
        miss_sound=cfg.feedback.miss_sound,
    )

    transcriber = Transcriber(
        model_size=cfg.transcription.model_size,
        device=cfg.transcription.device,
        compute_type=cfg.transcription.compute_type,
    )

    registry = discover("voice_commander.tools")
    matcher = Matcher(registry, threshold=cfg.matching.threshold)
    dispatcher = Dispatcher(feedback)

    # StreamingDaemon creates the utterance_sink binding, so build the recorder
    # with a placeholder and patch after.
    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=StreamingRecorder(
            device=cfg.audio.device if cfg.audio.device >= 0 else None,
            channels=cfg.audio.channels,
            vad_gate=vad_gate,
            utterance_sink=lambda _: None,  # patched below
            vad_sample_rate=cfg.vad.sample_rate,
        ),
        transcriber=transcriber,
        matcher=matcher,
        dispatcher=dispatcher,
        min_confidence=cfg.transcription.min_confidence,
        min_word_count=cfg.vad.gates.min_word_count,
        max_no_speech_prob=cfg.vad.gates.max_no_speech_prob,
        output_dir=cfg.audio.output_dir,
    )
    # Patch the utterance_sink to point at the daemon's queue push.
    daemon._recorder._utterance_sink = daemon._on_utterance
    return daemon
