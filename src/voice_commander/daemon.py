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
from typing import Any

import numpy as np
import numpy.typing as npt
import rapidfuzz.fuzz
import soundfile as sf
from silero_vad import load_silero_vad

from . import resolver as param_resolver
from .config import Config, log_llm_sources
from .dispatcher import Dispatcher
from .event_bus import EventBus
from .feedback import FeedbackSink, WindowsFeedbackSink
from .hotkey import HotkeyController
from .llm_router import LLMRouter
from .observability import Store, Tracer
from .observability.errors import classify as _classify_error
from .plan import Plan, PlanOutcome
from .registry import ToolRegistry, discover
from .streaming_recorder import StreamingRecorder
from .tool_metadata import ToolMetadataStore
from .tool_schema import sig_to_json_schema
from .tools import primitives as tool_primitives
from .transcriber import (
    RemoteTranscriber,
    Transcriber,
    TranscriberProtocol,
    TranscriptionResult,
)
from .vad_gate import VADGate
from .validator import validate_config_or_die, validate_or_die
from .web.app import create_app
from .web.server import WebServer

logger = logging.getLogger(__name__)


class _NoopStore:
    """Stand-in store passed to a disabled Tracer (no I/O ever happens)."""

    _daemon_pid = 0

    def write_run_start(self, *_a: Any, **_k: Any) -> None: ...
    def write_run_end(self, *_a: Any, **_k: Any) -> None: ...
    def write_span(self, *_a: Any, **_k: Any) -> None: ...
    def write_run_transcript_update(self, *_a: Any, **_k: Any) -> None: ...


class StreamingDaemon:
    """VAD-streaming daemon: hotkey toggles a live voice-command session.

    While a session is open, silero-vad auto-segments utterances on natural
    silence. Each utterance fires transcribe → gate → resolve → dispatch
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
        transcriber: TranscriberProtocol,
        llm_router: LLMRouter,
        dispatcher: Dispatcher,
        *,
        registry: ToolRegistry | None = None,
        min_confidence: float = 0.30,
        min_word_count: int = 1,
        max_no_speech_prob: float = 0.6,
        speak_fuzzy_threshold: int = 95,
        output_dir: str = "outputs",
        web_server: WebServer | None = None,
        event_bus: EventBus | None = None,
        tracer: Tracer | None = None,
        store: Store | None = None,
    ) -> None:
        """Composition root — wire all subsystems together. No I/O, no threads started.

        Called once from :func:`build_streaming_daemon` on the main thread before
        :meth:`run` is invoked.

        Args:
            feedback: :class:`FeedbackSink` for audio chimes and plan event
                logging.
            recorder: :class:`StreamingRecorder` that owns the audio stream and
                VAD worker.  May be ``None`` at construction time — wired after
                init (see :func:`build_streaming_daemon`).
            transcriber: :class:`Transcriber` for speech-to-text (faster-whisper).
                Model is loaded later by :meth:`run`.
            llm_router: :class:`LLMRouter` for transcript → tool-call plan via
                LM Studio.
            dispatcher: :class:`Dispatcher` that executes multi-step plans.
            registry: :class:`ToolRegistry` for tool lookup during plan execution.
                ``None`` disables dispatch (error is logged).
            min_confidence: Minimum transcription confidence ``[0, 1]``.  Transcripts
                below this threshold trigger a miss chime.
            min_word_count: Minimum word count in transcript.  Transcripts below
                this count are silently dropped (infrastructure noise gate).
            max_no_speech_prob: Maximum no-speech probability returned by faster-whisper.
                Transcripts above this threshold are silently dropped.
            output_dir: Directory for debug artefacts (``last_utterance.wav``,
                ``last_plan.json``).  Created on init if absent.
            web_server: Optional embedded :class:`WebServer` (uvicorn). Started by
                :meth:`run` before the hotkey listener.
            event_bus: Optional :class:`EventBus` for SSE telemetry consumed by
                the sprite process.

        Internal state created:
            ``_utt_q`` — bounded :class:`queue.Queue` (maxsize 8) bridging the VAD
            worker thread and the pipeline worker thread.
            ``_shutdown`` — :class:`threading.Event` that signals all threads to exit.
            ``_shutdown_lock`` — :class:`threading.Lock` guarding atomic check-and-set
            of ``_shutdown``.
            ``_wav_executor`` — single-worker :class:`~concurrent.futures.ThreadPoolExecutor`
            for fire-and-forget async WAV / JSON writes.
            ``_session_active`` / ``_speak_mode`` — boolean state flags (not thread-safe;
            primarily mutated on the pynput hotkey-listener thread, also reset by
            :meth:`shutdown` during teardown).
        """
        self._feedback = feedback
        self._recorder = recorder
        self._transcriber = transcriber
        self._llm_router = llm_router
        self._dispatcher = dispatcher
        self._registry = registry
        self._min_confidence = min_confidence
        self._min_word_count = min_word_count
        self._max_no_speech_prob = max_no_speech_prob
        self._speak_fuzzy_threshold = speak_fuzzy_threshold
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._web_server = web_server
        self._event_bus = event_bus
        self._tracer = (
            tracer
            if tracer is not None
            else Tracer(store=_NoopStore(), bus=event_bus or EventBus(), enabled=False)
        )
        self._store = store

        self._utt_q: queue.Queue[npt.NDArray[np.float32] | None] = queue.Queue(maxsize=8)
        self._pipeline_thread: threading.Thread | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._hotkey: HotkeyController | None = None
        self._shutdown = threading.Event()
        self._shutdown_lock = threading.Lock()
        # Single-worker executor for fire-and-forget async WAV writes (outputs/last_utterance.wav).
        self._wav_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="wav-writer",
        )
        self._session_active: bool = False
        self._speak_mode: bool = False

    def _publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        if self._event_bus is not None:
            self._event_bus.publish(event_type, data)

    # ------------------------------------------------------------------
    # Hotkey callbacks
    # ------------------------------------------------------------------

    def on_scroll_lock(self) -> None:
        """Toggle the voice session on/off and clear speak-mode on both transitions.

        Thread context: called exclusively on the **pynput hotkey-listener thread**.
        Must not block — delegates all heavy work to other threads via queues.
        pynput serialises key callbacks so concurrent invocations cannot happen.

        State transitions:

        * **Open → close**: if in speak-mode, synthesizes Right Ctrl first so the
          external dictation app turns off cleanly; then calls
          ``recorder.close_session()``, drains ``_utt_q``, sets
          ``_session_active = False``, resets ``_speak_mode = False``, publishes
          ``session_stopped``.
        * **Closed → open**: calls ``recorder.open_session()`` (spawns VAD worker),
          sets ``_session_active = True``, resets ``_speak_mode = False``, publishes
          ``session_started``.

        Guard: no-op (with a warning log) if ``_recorder`` is ``None`` — i.e. the
        daemon was constructed but the recorder has not yet been wired in.
        """
        if self._recorder is None:
            logger.warning("on_scroll_lock called but recorder is not yet initialised; ignoring")
            return
        if self._session_active:
            # If in speak-mode, synth Right Ctrl to turn the dictation app off cleanly.
            if self._speak_mode:
                speak_entry = self._registry.by_name("speak") if self._registry else None
                if speak_entry is not None:
                    try:
                        speak_entry.func()
                    except Exception as e:
                        logger.exception("Failed to synth Right Ctrl during Scroll Lock close")
                        self._feedback.on_error("speak.toggle", e)
                else:
                    logger.warning("speak tool not found — dictation app may remain active")
            self._recorder.close_session()
            self._drain_utt_q()
            self._session_active = False
            self._speak_mode = False
            self._feedback.on_recording_stop()
            self._publish("session_stopped")
            logger.info("Session closed")
        else:
            try:
                self._recorder.open_session()
                self._session_active = True
                self._speak_mode = False
                self._feedback.on_recording_start()
                self._publish("session_started")
                logger.info("Session opened")
            except Exception as e:
                self._session_active = False
                self._speak_mode = False
                self._feedback.on_error("recorder.open_session", e)

    def on_speak_toggle(self) -> None:
        """Toggle speak-mode within an active session. No-op when session is inactive.

        Thread context: called exclusively on the **pynput hotkey-listener thread**
        (including when triggered by a synthesized Right Ctrl keypress from
        :func:`voice_commander.tools._system.speak`).
        pynput serialises key callbacks so concurrent invocations cannot happen.

        State transitions:

        * **Normal → speak-mode**: sets ``_speak_mode = True``, publishes ``muted``.
          The audio stream stays open — only the LLM route is gated.
        * **Speak-mode → normal**: sets ``_speak_mode = False``, publishes ``unmuted``.

        No-op when ``_session_active`` is ``False`` — speak-mode toggle outside a
        session has no effect (silent return). This allows the Right Ctrl key to
        double as the external dictation app's hotkey without triggering any
        voice-commander behaviour outside an active session.
        """
        if not self._session_active:
            return
        self._speak_mode = not self._speak_mode
        if self._speak_mode:
            self._publish("muted")
            logger.info("Speak-mode entered")
        else:
            self._publish("unmuted")
            logger.info("Speak-mode exited")

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
            except MemoryError:
                raise
            except Exception as e:
                cat = _classify_error(e, where="daemon")
                logger.exception("unhandled exception in utterance processing (category=%s)", cat)
                self._feedback.on_error("pipeline", e)

    def _process_utterance(self, utterance: npt.NDArray[np.float32]) -> None:
        """Single-utterance hot path: transcribe → gate → route → dispatch.

        Thread context: called exclusively on the **``vc-pipeline`` worker thread**
        (from :meth:`_pipeline_loop`).  Never called from any other thread.

        Queues touched: none directly — the caller (:meth:`_pipeline_loop`) has
        already dequeued the utterance from ``_utt_q``.

        Side effects:

        * Submits async WAV write to ``_wav_executor`` (debug artefact).
        * Fires :class:`EventBus` events: ``transcribing``, ``llm_thinking``,
          ``plan_outcome``.
        * Calls :class:`FeedbackSink` callbacks (``on_transcript``, ``on_miss``,
          ``on_error``).
        * Submits async JSON write to ``_wav_executor`` on a successful dispatch.

        Gate chain (in order):

        1. **word-count** — below ``_min_word_count`` → silent drop (no ``plan_outcome``).
        2. **no_speech_prob** — above ``_max_no_speech_prob`` → silent drop.
        3. **speak-mode gate** — if ``_speak_mode``, check for wake-word "speak" via
           ``rapidfuzz.fuzz.ratio``; fuzzy-match exits speak-mode, all else is
           silently dropped. No LLM call in speak-mode.
        4. **confidence** — below ``_min_confidence`` → miss chime +
           ``plan_outcome`` (status=``miss``).
        5. **LLM route** — ``LLMRouter.route()`` returns ``None`` → miss chime + ``plan_outcome``.
        6. **dispatch** — ``Dispatcher.run_plan()`` executes the plan.

        Args:
            utterance: 1-D float32 ndarray at 16 kHz representing the complete
                utterance segment produced by the VAD gate.

        Does not raise: all exceptions are caught by :meth:`_pipeline_loop` and
        routed to ``feedback.on_error``.
        """
        # Async write for post-mortem debugging
        self._write_utterance_async(utterance)

        with self._tracer.run("") as run:
            start_s = time.perf_counter()

            def _publish_miss(transcript: str) -> None:
                self._publish(
                    "plan_outcome",
                    PlanOutcome(
                        transcript=transcript,
                        steps=(),
                        status="miss",
                        failed_step_index=None,
                        error_msg=None,
                        duration_ms=int((time.perf_counter() - start_s) * 1000),
                    ).to_event_dict(),
                )

            with self._tracer.span("transcribe", name="transcribe") as ts:
                self._publish("transcribing")
                result: TranscriptionResult = self._transcriber.transcribe(utterance)
                ts.set_attr("confidence", result.confidence)
                ts.set_attr("no_speech_prob", result.no_speech_prob)
                ts.set_output({"text": result.text})

            self._tracer.update_transcript(run.run_id, result.text)
            self._feedback.on_transcript(result.text, result.confidence)

            # Gate: word-count  (infrastructure noise — no plan_outcome)
            word_count = len(result.text.split())
            if word_count < self._min_word_count:
                logger.debug("Gate: word-count %d < %d, dropping", word_count, self._min_word_count)
                return

            # Gate: no_speech_prob  (infrastructure noise — no plan_outcome)
            if result.no_speech_prob > self._max_no_speech_prob:
                logger.debug(
                    "Gate: no_speech_prob %.2f > %.2f, dropping",
                    result.no_speech_prob,
                    self._max_no_speech_prob,
                )
                return

            # Speak-mode gate: in speak-mode, check for the wake-word "speak"
            # and short-circuit the rest of the pipeline.  The audio stream
            # stays open so whisper keeps transcribing, but neither the LLM
            # router nor the dispatcher are ever called.
            if self._speak_mode:
                text_norm = result.text.strip().lower().rstrip(".,!?")
                if (
                    len(text_norm.split()) == 1
                    and rapidfuzz.fuzz.ratio(text_norm, "speak") >= self._speak_fuzzy_threshold
                ):
                    speak_entry = self._registry.by_name("speak") if self._registry else None
                    if speak_entry is not None:
                        try:
                            speak_entry.func()
                        except Exception as e:
                            logger.exception("Failed to synth Right Ctrl from speak-mode wake-word")
                            self._feedback.on_error("speak.toggle", e)
                    else:
                        logger.warning("speak tool not found in registry; cannot exit speak-mode")
                else:
                    logger.debug("Speak-mode: dropping '%s' (no wake-word match)", result.text)
                return

            # Gate: confidence  (emits plan_outcome status=miss — user-visible)
            if result.confidence < self._min_confidence:
                run.set_status("miss")
                self._feedback.on_miss(result.text, ())
                _publish_miss(result.text)
                return

            self._publish("llm_thinking")
            plan = self._llm_router.route(result.text)
            if plan is None:
                # No match in the command/workflow catalog. Chime once and stop —
                # no agentic retry, no env-seeded second call. The user can either
                # rephrase or add a command for the missing intent via the UI.
                run.set_status("miss")
                self._feedback.on_miss(result.text, ())
                _publish_miss(result.text)
                return
            if self._registry is None:
                logger.error("Registry not set — cannot execute plan for '%s'", result.text)
                self._publish(
                    "plan_outcome",
                    PlanOutcome(
                        transcript=result.text,
                        steps=plan.steps,
                        status="error",
                        failed_step_index=None,
                        error_msg="registry not initialized",
                        duration_ms=int((time.perf_counter() - start_s) * 1000),
                    ).to_event_dict(),
                )
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
                    "steps": [{"name": s.name, "kwargs": s.kwargs} for s in plan.steps],
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
                path.write_text(
                    json.dumps(artifact, indent=2),
                    encoding="utf-8",
                )
            except Exception:
                logger.exception("Failed to write %s", path)

        self._wav_executor.submit(_write)

    # ------------------------------------------------------------------
    # Utterance sink (called from StreamingRecorder's VAD worker thread)
    # ------------------------------------------------------------------

    def _on_utterance(self, utterance: npt.NDArray[np.float32]) -> None:
        """Utterance sink callback — enqueue a completed utterance for the pipeline worker.

        Thread context: called on the **VAD worker thread** inside
        :class:`StreamingRecorder`.  Must not block — uses
        :meth:`queue.Queue.put_nowait`.

        Queue touched: ``_utt_q`` (:class:`queue.Queue`, maxsize 8).  On
        :exc:`queue.Full`, logs a warning and fires ``feedback.on_miss`` — the
        utterance is dropped rather than blocking the VAD thread.

        Args:
            utterance: 1-D float32 ndarray at 16 kHz representing the complete
                utterance segment produced by the VAD gate.
        """
        try:
            self._utt_q.put_nowait(utterance)
        except queue.Full:
            logger.warning("utt_q full — dropping utterance (%d samples)", len(utterance))
            self._feedback.on_miss("(queue overflow)", ())

    def _heartbeat_loop(self) -> None:
        while not self._shutdown.wait(1.0):
            self._publish("daemon_heartbeat")

    # ------------------------------------------------------------------
    # Run / shutdown
    # ------------------------------------------------------------------

    def run(self, hotkey_key: str, mute_key: str = "") -> None:
        """Blocking entry point — warm up models, start all threads, block until shutdown.

        Thread context: **must be called from the main thread** so that the
        SIGINT handler can be installed.  Logs a warning and skips SIGINT
        registration if called from any other thread.  Blocks via
        :meth:`threading.Event.wait` with a 0.5 s poll interval (see
        ``docs/gotchas.md`` §9 — Windows Ctrl+C requires polling, not a plain
        blocking wait).

        Threads started:

        * ``vc-pipeline`` — runs :meth:`_pipeline_loop` (transcribe/gate/route/dispatch).
        * ``vc-heartbeat`` — runs :meth:`_heartbeat_loop` at 1 Hz.

        Side effects:

        * Calls :meth:`Transcriber.load` to warm up the whisper model before
          accepting hotkey presses.
        * Starts :attr:`_web_server` (if configured) before the hotkey listener
          so the UI is ready when the daemon accepts input.
        * Starts :class:`HotkeyController` with the provided key bindings.

        Args:
            hotkey_key: Key name for session toggle (e.g. ``"scroll_lock"``).
            mute_key: Key name for speak-mode toggle.  Empty string (default) disables
                the hotkey; Right Ctrl (``"ctrl_r"``) is the recommended default.

        Raises:
            Exception: Re-raises if :meth:`Transcriber.load` or
                :meth:`HotkeyController.start` fails (after logging the error
                and calling ``feedback.on_error``).
        """
        # Load models before accepting hotkey presses.
        self._publish("warmup_start")
        try:
            self._transcriber.load()
        except Exception as e:
            logger.exception("Transcriber.load() failed; aborting startup")
            self._feedback.on_error("transcriber.load", e)
            raise
        self._publish("warmup_done")

        # Start pipeline worker thread.
        self._pipeline_thread = threading.Thread(
            target=self._pipeline_loop,
            name="vc-pipeline",
            daemon=True,
        )
        self._pipeline_thread.start()

        # Start heartbeat producer thread (1 Hz).
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="vc-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()

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
                bindings[mute_key] = self.on_speak_toggle
            self._hotkey = HotkeyController(bindings)
            self._hotkey.start()
        except Exception as e:
            logger.exception("HotkeyController.start() failed; aborting startup")
            self._feedback.on_error("hotkey.start", e)
            raise

        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, lambda *_: self.shutdown())
            # Windows: the supervisor sends CTRL_BREAK_EVENT to children
            # spawned with CREATE_NEW_PROCESS_GROUP, which raises SIGBREAK
            # in the receiver — NOT SIGINT. Without this handler the
            # daemon ignores supervisor shutdown requests and gets
            # force-killed by taskkill /T /F after the grace window.
            sigbreak = getattr(signal, "SIGBREAK", None)
            if sigbreak is not None:
                signal.signal(sigbreak, lambda *_: self.shutdown())
        else:
            logger.warning("run() called from a non-main thread; SIGINT handler not registered")
        logger.info("StreamingDaemon running. Press Ctrl+C to exit.")
        try:
            while not self._shutdown.wait(0.5):
                pass
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received, shutting down")
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        """Idempotent teardown — safe to call multiple times from any thread.

        Thread context: normally called from the main thread (end of :meth:`run`
        or the SIGINT handler), but may also be called from the pynput
        hotkey-listener thread via the mute primitive's scroll-lock callback.
        Multiple concurrent callers are safe — the first caller acquires
        ``_shutdown_lock``, sets the ``_shutdown`` event, and performs teardown;
        subsequent callers return immediately on the ``_shutdown.is_set()`` check.

        Locks touched: acquires ``_shutdown_lock`` to atomically check and set
        the ``_shutdown`` event.

        Teardown order:

        1. Stop the web server (no late UI requests land on a half-dead registry).
        2. Close any open recording session (calls ``recorder.close_session()`` unless
           already muted, in which case the stream was closed by the mute handler).
        3. Stop the hotkey listener.
        4. Poison ``_utt_q`` with a ``None`` sentinel → join ``vc-pipeline`` thread
           (5 s timeout, logs warning on timeout).
        5. Join ``vc-heartbeat`` thread (2 s timeout, logs warning on timeout).
        6. Shut down the WAV writer executor (non-blocking — in-flight writes may
           not complete).
        7. Unload the transcriber model.
        """
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
            try:
                self._recorder.close_session()
            except Exception:
                logger.exception("Error closing session during shutdown")
            self._session_active = False
            self._speak_mode = False

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

        # Join heartbeat thread.
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=2.0)
            if self._heartbeat_thread.is_alive():
                logger.warning("Heartbeat thread did not exit within 2 s")

        # Shut down the WAV writer executor.
        self._wav_executor.shutdown(wait=False)

        # Stop observability store.
        if self._store is not None:
            try:
                self._store.stop()
            except Exception:
                logger.exception("Error stopping observability store")

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

    transcriber: TranscriberProtocol
    if cfg.transcription.backend == "remote":
        transcriber = RemoteTranscriber(
            endpoint_url=cfg.transcription.remote_endpoint_url,
            timeout_ms=cfg.transcription.remote_timeout_ms,
        )
        logger.info(
            "Transcription backend: remote @ %s (timeout=%dms)",
            cfg.transcription.remote_endpoint_url,
            cfg.transcription.remote_timeout_ms,
        )
    else:
        transcriber = Transcriber(
            model_size=cfg.transcription.model_size,
            device=cfg.transcription.device,
            compute_type=cfg.transcription.compute_type,
        )
        logger.info(
            "Transcription backend: local (model=%s device=%s)",
            cfg.transcription.model_size,
            cfg.transcription.device,
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
            entry.func,
            args_meta,
            tool_name=entry.name,
            description=entry.description,
        )

    # Startup validation — refuse to run on bad config or tool/TOML drift.
    validate_config_or_die(cfg)
    validate_or_die(registry, store)

    # Log every [llm].* field and its winning source (env / config.toml /
    # default) before anything reads cfg.llm at runtime.
    log_llm_sources(cfg)

    # Wire the parameter resolver's threshold accessors to the live LLMConfig
    # so focus/open read focus_fuzzy_threshold / open_fuzzy_threshold from TOML.
    param_resolver._set_config(cfg.llm)

    event_bus = EventBus()

    # LLM Router — always created.
    llm_router = LLMRouter(cfg.llm, registry, reload_lock)
    if cfg.llm.warmup_on_startup:
        if llm_router.warmup():
            logger.info("LLM router warmup succeeded")
        else:
            logger.warning("LLM router warmup failed — LM Studio may be offline")

    # Load user-defined commands + workflows (first-run seeding + registration).
    from voice_commander.commands import GraphStore, seed_if_missing
    from voice_commander.commands.registrar import reload_all as reload_commands_all

    repo_root = Path(__file__).resolve().parents[2]

    # Observability — create store + tracer now that repo_root is known.
    _obs_store: Store | None = None
    _obs_tracer: Tracer | None = None
    if cfg.observability.enabled:
        db_path = repo_root / cfg.observability.db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _obs_store = Store(
            db_path,
            keep_runs=cfg.observability.keep_runs,
            queue_max=cfg.observability.queue_max,
            daemon_pid=os.getpid(),
        )
        _obs_store.start()
        recovered = _obs_store.recover_stale_runs()
        if recovered:
            logger.info("observability: marked %d stale 'running' runs as crashed", recovered)
        _obs_tracer = Tracer(
            store=_obs_store,
            bus=event_bus,
            enabled=True,
            slow_run_ms=cfg.observability.slow_run_ms,
            daemon_pid=os.getpid(),
        )
    else:
        _obs_tracer = Tracer(store=_NoopStore(), bus=event_bus, enabled=False)

    dispatcher = Dispatcher(feedback, event_bus=event_bus, tracer=_obs_tracer)
    llm_router.set_tracer(_obs_tracer)

    commands_path = repo_root / "commands.json"
    workflows_path = repo_root / "workflows.json"
    seed_if_missing(commands_path, repo_root / "commands.default.json")
    seed_if_missing(workflows_path, repo_root / "workflows.default.json")

    command_store = GraphStore(commands_path, kind="command")
    workflow_store = GraphStore(workflows_path, kind="workflow")
    with reload_lock:
        cmd_names, wf_names = reload_commands_all(registry, command_store, workflow_store)
    logger.info(
        "Loaded %d commands + %d workflows",
        len(cmd_names),
        len(wf_names),
    )

    # Web server — enabled by config + not suppressed by env var.
    web_server: WebServer | None = None
    if cfg.web.enabled and os.environ.get("VOICE_COMMANDER_WEB_DISABLED") != "1":
        app = create_app(
            registry,
            store,
            reload_lock,
            event_bus=event_bus,
            command_store=command_store,
            workflow_store=workflow_store,
            config_path=repo_root / "config.toml",
            llm_router=llm_router,
            observability_store=_obs_store,
            observability_tracer=_obs_tracer,
        )
        web_server = WebServer(app, host=cfg.web.host, port=cfg.web.port)

    # Build daemon without a recorder first so _on_utterance is available,
    # then wire the recorder with the real callback.
    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=None,
        transcriber=transcriber,
        llm_router=llm_router,
        dispatcher=dispatcher,
        registry=registry,
        min_confidence=cfg.transcription.min_confidence,
        min_word_count=cfg.vad.gates.min_word_count,
        max_no_speech_prob=cfg.vad.gates.max_no_speech_prob,
        speak_fuzzy_threshold=cfg.speak.fuzzy_threshold,
        output_dir=cfg.audio.output_dir,
        web_server=web_server,
        event_bus=event_bus,
        tracer=_obs_tracer,
        store=_obs_store,
    )
    daemon._recorder = StreamingRecorder(
        device=cfg.audio.device if cfg.audio.device >= 0 else None,
        channels=cfg.audio.channels,
        vad_gate=vad_gate,
        utterance_sink=daemon._on_utterance,
    )

    # Wire the mute() primitive to the daemon's scroll-lock handler so a
    # voice-driven "mute" utterance ends the session exactly like pressing
    # the Scroll Lock hotkey.
    tool_primitives._set_mute_callback(daemon.on_scroll_lock)

    return daemon
