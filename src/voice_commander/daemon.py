from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import queue
import signal
import threading
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
import numpy.typing as npt
import soundfile as sf
from silero_vad import load_silero_vad

from .config import Config
from .dispatcher import Dispatcher
from .feedback import FeedbackSink, WindowsFeedbackSink
from .hotkey import HotkeyController
from .llm_router import LLMRouter
from .plan import Plan
from .registry import ToolRegistry, discover
from .resolver import Resolver
from .streaming_recorder import StreamingRecorder
from .tool_metadata import ToolMetadataStore
from .tool_schema import sig_to_json_schema
from .transcriber import Transcriber, TranscriptionResult
from .vad_gate import VADGate
from .validator import validate_config_or_die, validate_or_die
from .web.app import create_app
from .web.server import WebServer

logger = logging.getLogger(__name__)


class StreamingDaemon:
    """VAD-streaming daemon: hotkey toggles a live voice-command session.

    While a session is open, silero-vad auto-segments utterances on natural
    silence. Each utterance fires transcribe → gate → match → dispatch
    immediately. No keypresses between commands.

    Optionally runs an embedded uvicorn-hosted web UI for managing tool
    metadata (phrases / descriptions / enabled flag) via sidecar TOML files.
    The web server, registry, and LLM router all share a single
    :class:`threading.Lock` so metadata reloads never race with
    live tool-call routing.
    """

    def __init__(
        self,
        feedback: FeedbackSink,
        recorder: StreamingRecorder | None,
        transcriber: Transcriber,
        resolver: Resolver,
        dispatcher: Dispatcher,
        *,
        registry: ToolRegistry | None = None,
        min_confidence: float = 0.30,
        min_word_count: int = 1,
        max_no_speech_prob: float = 0.6,
        output_dir: str = "outputs",
        web_server: WebServer | None = None,
    ) -> None:
        self._feedback = feedback
        self._recorder = recorder
        self._transcriber = transcriber
        self._resolver = resolver
        self._dispatcher = dispatcher
        self._registry = registry
        self._min_confidence = min_confidence
        self._min_word_count = min_word_count
        self._max_no_speech_prob = max_no_speech_prob
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._web_server = web_server

        self._utt_q: queue.Queue[npt.NDArray[np.float32] | None] = queue.Queue(maxsize=8)
        self._pipeline_thread: threading.Thread | None = None
        self._hotkey: HotkeyController | None = None
        self._shutdown = threading.Event()
        self._shutdown_lock = threading.Lock()
        # Single-worker executor for fire-and-forget async WAV writes (outputs/last_utterance.wav).
        self._wav_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="wav-writer",
        )
        self._session_active: bool = False
        self._muted: bool = False

    # ------------------------------------------------------------------
    # Hotkey callbacks
    # ------------------------------------------------------------------

    def on_scroll_lock(self) -> None:
        """Toggle session on/off. Clears muted flag on both open and close."""
        if self._recorder is None:
            logger.warning("on_scroll_lock called but recorder is not yet initialised; ignoring")
            return
        if self._session_active:
            # Close session from any sub-state (muted or unmuted).
            if not self._muted:
                self._recorder.close_session()
            # else: stream already closed by mute
            self._drain_utt_q()
            self._session_active = False
            self._muted = False
            self._feedback.on_recording_stop()
            logger.info("Session closed")
        else:
            try:
                self._recorder.open_session()
                self._session_active = True
                self._muted = False
                self._feedback.on_recording_start()
                logger.info("Session opened")
            except Exception as e:
                self._session_active = False
                self._muted = False
                self._feedback.on_error("recorder.open_session", e)

    def on_mute_toggle(self) -> None:
        """Toggle mute within an active session. No-op when session is inactive."""
        if not self._session_active:
            return  # Silent no-op (requirement 4)
        if self._recorder is None:
            return
        if self._muted:
            # Unmute: reopen stream
            try:
                self._recorder.open_session()
                self._muted = False
                logger.info("Session unmuted")
            except Exception as e:
                self._feedback.on_error("recorder.open_session", e)
        else:
            # Mute: close stream, drain queue
            try:
                self._recorder.close_session()
            except Exception:
                logger.exception("close_session() failed during mute; treating as muted")
            self._drain_utt_q()
            self._muted = True
            logger.info("Session muted")

    def _drain_utt_q(self) -> None:
        """Discard all pending utterances from the queue."""
        drained = 0
        while True:
            try:
                self._utt_q.get_nowait()
                drained += 1
            except queue.Empty:
                break
        if drained:
            logger.debug("Drained %d utterance(s) from utt_q", drained)

    # ------------------------------------------------------------------
    # Pipeline worker (transcribe → gate → resolve → dispatch)
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

        # Mute guard: utterance may have been mid-transcription when mute fired.
        if self._muted:
            logger.debug("Mute guard: dropping utterance '%s' (muted during pipeline)", result.text)
            return

        plan = self._resolver.resolve(result.text)
        if plan is None:
            return  # resolver already fired on_miss
        if self._registry is None:
            logger.error("Registry not set — cannot execute plan for '%s'", result.text)
            return
        self._dispatcher.run_plan(result.text, plan, self._registry)
        self._write_plan_async(result.text, plan)

    def _write_utterance_async(self, utterance: npt.NDArray[np.float32]) -> None:
        path = self._output_dir / "last_utterance.wav"

        def _write() -> None:
            try:
                sf.write(path, utterance, 16000, subtype="FLOAT")
            except Exception:
                logger.exception("Failed to write %s", path)

        self._wav_executor.submit(_write)

    def _write_plan_async(self, transcript: str, plan: Plan) -> None:
        path = self._output_dir / "last_plan.json"

        def _write() -> None:
            try:
                artifact = {
                    "transcript": transcript,
                    "steps": [
                        {"name": s.name, "kwargs": s.kwargs}
                        for s in plan.steps
                    ],
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
                path.write_text(
                    json.dumps(artifact, indent=2), encoding="utf-8",
                )
            except Exception:
                logger.exception("Failed to write %s", path)

        self._wav_executor.submit(_write)

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

    def run(self, hotkey_key: str, mute_key: str = "") -> None:
        # Load models before accepting hotkey presses.
        try:
            self._transcriber.load()
        except Exception as e:
            logger.exception("Transcriber.load() failed; aborting startup")
            self._feedback.on_error("transcriber.load", e)
            raise

        # Start pipeline worker thread.
        self._pipeline_thread = threading.Thread(
            target=self._pipeline_loop,
            name="vc-pipeline",
            daemon=True,
        )
        self._pipeline_thread.start()

        # Start embedded web server (if configured) before the hotkey listener
        # so the UI is responsive as soon as the daemon is ready for input.
        if self._web_server is not None:
            try:
                if not self._web_server.start():
                    logger.warning("Web UI unavailable — continuing without dashboard")
            except Exception as e:
                logger.exception("WebServer.start() failed; continuing without UI")
                self._feedback.on_error("web.start", e)

        # Start hotkey listener.
        try:
            bindings: dict[str, Callable[[], None]] = {hotkey_key: self.on_scroll_lock}
            if mute_key:
                bindings[mute_key] = self.on_mute_toggle
            self._hotkey = HotkeyController(bindings)
            self._hotkey.start()
        except Exception as e:
            logger.exception("HotkeyController.start() failed; aborting startup")
            self._feedback.on_error("hotkey.start", e)
            raise

        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, lambda *_: self.shutdown())
        else:
            logger.warning(
                "run() called from a non-main thread; SIGINT handler not registered"
            )
        logger.info("StreamingDaemon running. Press Ctrl+C to exit.")
        try:
            while not self._shutdown.wait(0.5):
                pass
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received, shutting down")
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        with self._shutdown_lock:
            if self._shutdown.is_set():
                return
            self._shutdown.set()

        # Stop the web server before tearing down the pipeline so no late UI
        # request lands on a half-dead registry.
        if self._web_server is not None:
            try:
                self._web_server.stop()
            except Exception:
                logger.exception("Error stopping web server during shutdown")

        # Close any open session.
        if self._recorder is not None and self._session_active:
            if not self._muted:
                try:
                    self._recorder.close_session()
                except Exception:
                    logger.exception("Error closing session during shutdown")
            self._session_active = False
            self._muted = False

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

        # Shut down the WAV writer executor.
        self._wav_executor.shutdown(wait=False)

        # Release model.
        try:
            self._transcriber.unload()
        except Exception:
            logger.exception("Error unloading transcriber")


def build_streaming_daemon(cfg: Config) -> StreamingDaemon:
    """Factory: wire all subsystems into a StreamingDaemon.

    Also wires the embedded web UI when enabled. The metadata store, registry,
    LLM router, and web app all share a single ``threading.Lock`` so hot reloads
    from the UI never race with the LLM router reading tool metadata on the
    pipeline thread.
    """
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

    # Metadata store + reload lock shared between registry, LLM router, and web server.
    tools_dir = Path(__file__).resolve().parent / "tools"
    store = ToolMetadataStore(tools_dir)
    reload_lock = threading.Lock()

    registry = discover("voice_commander.tools", store=store)

    # Generate JSON schemas for all tools (used by LLM router).
    all_meta = store.load_all()
    for entry in registry.all():
        meta = all_meta.get(entry.name)
        args_meta = meta.args if meta else {}
        entry.params_schema = sig_to_json_schema(
            entry.func, args_meta, tool_name=entry.name, description=entry.description,
        )

    # Startup validation — refuse to run on bad config or tool/TOML drift.
    validate_config_or_die(cfg)
    validate_or_die(registry, store)

    dispatcher = Dispatcher(feedback)

    # LLM Router — always created.
    llm_router = LLMRouter(cfg.llm, registry)
    if cfg.llm.warmup_on_startup:
        if llm_router.warmup():
            logger.info("LLM router warmup succeeded")
        else:
            logger.warning("LLM router warmup failed — LM Studio may be offline")

    resolver = Resolver(llm_router, feedback)

    # Web server — enabled by config + not suppressed by env var.
    web_server: WebServer | None = None
    if cfg.web.enabled and os.environ.get("VOICE_COMMANDER_WEB_DISABLED") != "1":
        app = create_app(registry, store, reload_lock)
        web_server = WebServer(app, host=cfg.web.host, port=cfg.web.port)

    # Build daemon without a recorder first so _on_utterance is available,
    # then wire the recorder with the real callback.
    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=None,
        transcriber=transcriber,
        resolver=resolver,
        dispatcher=dispatcher,
        registry=registry,
        min_confidence=cfg.transcription.min_confidence,
        min_word_count=cfg.vad.gates.min_word_count,
        max_no_speech_prob=cfg.vad.gates.max_no_speech_prob,
        output_dir=cfg.audio.output_dir,
        web_server=web_server,
    )
    daemon._recorder = StreamingRecorder(
        device=cfg.audio.device if cfg.audio.device >= 0 else None,
        channels=cfg.audio.channels,
        vad_gate=vad_gate,
        utterance_sink=daemon._on_utterance,
    )
    return daemon
