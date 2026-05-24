from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import soundfile as sf

from voice_commander.vad_onnx import load_silero_vad

from . import resolver as param_resolver
from .chain import ChainParser
from .config import Config
from .dictation.session import DictationSession
from .dictation.store import DictationStore
from .dictation.vocab import Vocabulary, VocabStore
from .dispatcher import Dispatcher
from .elements import clicker, desktop, scanner
from .elements.session import ENTRY_WORDS, ElementsSession, ElementsState
from .elements.uia import uia_available
from .event_bus import EventBus
from .feedback import FeedbackSink, WindowsFeedbackSink
from .hotkey import HotkeyController
from .modes.registry import ModeRegistry
from .modes.session import ModeSession
from .modes.watcher import ModesWatcher
from .observability import Store, Tracer
from .observability.errors import classify as _classify_error
from .picker.registry import BarePickerRegistry
from .picker.session import PickerSession
from .plan import Plan, PlanOutcome
from .registry import ToolRegistry, discover
from .streaming_recorder import SelfTestResult, StreamingRecorder
from .tool_metadata import ToolMetadataStore
from .tool_schema import sig_to_json_schema
from .transcriber import (
    Transcriber,
    TranscriptionResult,
)
from .vad_gate import VADGate
from .validator import validate_config_or_die, validate_or_die
from .verb_router import VerbRouter, _normalize_spoken, build_default_rules
from .web.app import create_app
from .web.server import WebServer

logger = logging.getLogger(__name__)

# Time to wait for a new utterance when dictation pending-end is set.
# After this window with no new audio the pipeline considers the queue drained
# and calls _finalize_pending_dictation_end().
_DICTATION_DRAIN_TIMEOUT_S = 0.25

# Unique sentinel object — enqueued by on_dictation_toggle() to wake the
# pipeline thread from a blocking get(timeout=None) when the user presses
# the dictation hotkey to end a session. Identity comparison is safe: no
# real utterance array can be is-equal to this singleton. (ADR 0089)
_DICTATION_WAKE = object()


def _provenance_banner(cfg: Config, result: SelfTestResult) -> list[str]:
    """Return two banner lines describing resolved startup state.

    Format::

        === voice_commander | pid=<PID> | git=<SHORT_SHA> | config=<SHORT_HASH> ===
        === audio: name='<DEVICE_NAME>' -> <HOSTAPI>[<IDX>] @ <RATE>Hz | <STATUS> ===

    *SHORT_SHA* — first 7 chars of ``git rev-parse HEAD``, ``"unknown"`` on error.
    *SHORT_HASH* — first 12 chars of SHA-256 of ``config.toml`` bytes, ``"unknown"`` on error.
    *STATUS* — ``"OK"`` on success; ``"FAIL: <first line of error>"`` on failure.
    """
    # Git SHA
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        short_sha = proc.stdout.strip()[:7] if proc.returncode == 0 else "unknown"
    except Exception:
        short_sha = "unknown"

    # Config hash
    try:
        config_bytes = Path("config.toml").read_bytes()
        short_hash = hashlib.sha256(config_bytes).hexdigest()[:12]
    except Exception:
        short_hash = "unknown"

    # Device name
    device_name = cfg.audio.device_name or "(default)"

    # Host API + index string
    if result.device_index is None:
        hostapi_idx_str = "(default)"
    else:
        hostapi_idx_str = f"{result.host_api}[{result.device_index}]"

    # Status
    if result.ok:
        status = "OK"
    else:
        first_error_line = (result.error or "unknown error").splitlines()[0]
        status = f"FAIL: {first_error_line}"

    line1 = f"=== voice_commander | pid={os.getpid()} | git={short_sha} | config={short_hash} ==="
    line2 = (
        f"=== audio: name='{device_name}' -> {hostapi_idx_str}"
        f" @ {result.native_rate}Hz | {status} ==="
    )
    return [line1, line2]


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
    The web server and registry share a single :class:`threading.Lock` so
    metadata reloads never race with live tool-call routing.
    """

    def __init__(
        self,
        feedback: FeedbackSink,
        recorder: StreamingRecorder | None,
        transcriber: Transcriber,
        dispatcher: Dispatcher,
        verb_router: VerbRouter,
        *,
        registry: ToolRegistry | None = None,
        picker_session: PickerSession | None = None,
        picker_registry: BarePickerRegistry | None = None,
        dictation_session: DictationSession | None = None,
        dictation_ws_url: str = "",
        dictation_language: str = "en",
        dictation_idle_timeout_s: float = 30.0,
        elements_session: ElementsSession | None = None,
        mode_session: ModeSession | None = None,
        elements_max_elements: int = 200,
        elements_scan_timeout_s: float = 3.0,
        min_confidence: float = 0.30,
        min_word_count: int = 1,
        max_no_speech_prob: float = 0.6,
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
            ``_session_active`` — boolean state flag (mutated only on the pynput hotkey-listener
            thread, also reset by :meth:`shutdown` during teardown).
        """
        self._feedback = feedback
        self._recorder = recorder
        self._transcriber = transcriber
        self._dispatcher = dispatcher
        self._registry = registry
        self._min_confidence = min_confidence
        self._min_word_count = min_word_count
        self._max_no_speech_prob = max_no_speech_prob
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

        self._utt_q: queue.Queue[
            tuple[npt.NDArray[np.float32], int] | npt.NDArray[np.float32] | None | object
        ] = queue.Queue(maxsize=8)
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
        self._session_opened_by_dictation: bool = False
        # Audio-generation counter. Bumped on every state transition that
        # ends audio capture (scroll-lock close) or restarts it
        # (scroll-lock open). Each utterance is tagged with the
        # generation in which it was *enqueued*; the pipeline drops
        # utterances whose tag does not match the current generation.
        # This closes the race where a stale utterance is read only AFTER
        # transcribe() returns — by then its generation has been invalidated
        # and it is dropped anyway.
        self._audio_gen: int = 0
        self._verb_router = verb_router
        self._picker_session = picker_session
        self._picker_registry = picker_registry
        self._dictation_session = dictation_session
        self._dictation_ws_url = dictation_ws_url
        self._dictation_language = dictation_language
        self._dictation_idle_timeout_s = dictation_idle_timeout_s
        self._dictation_store = DictationStore(self._output_dir / "dictation")
        self._vocab_store = VocabStore(self._output_dir / "dictation" / "vocab.json")
        self._dictation_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="dictation",
        )
        # --- Elements mode (ADR 0087) ---
        self._elements_session = elements_session
        # --- Named modes (ADR 0100) ---
        self._mode_session = mode_session
        self._elements_max_elements = elements_max_elements
        self._elements_scan_timeout_s = elements_scan_timeout_s
        self._elements_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="elements",
        )
        if elements_session is not None and not uia_available():
            logger.warning(
                "Elements mode is configured but the 'uiautomation' library "
                "is unavailable — the entry word will be ignored."
            )
        # Set when Transcriber.load() completes successfully in the background thread.
        # Pipeline worker waits on this before calling transcribe().
        self._transcriber_ready: threading.Event = threading.Event()
        # Live config snapshot — set by build_streaming_daemon after construction;
        # updated in-place by _on_config_changed without requiring a restart.
        self._cfg: Config | None = None
        # Config file watcher — set by build_streaming_daemon; stopped in shutdown().
        self._config_watcher: Any = None
        # Modes hot-reload watcher — set by build_streaming_daemon; stopped in shutdown().
        self._modes_watcher: Any = None
        self._mru_pump: Any = None

    def _publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        if self._event_bus is not None:
            self._event_bus.publish(event_type, data)

    # ------------------------------------------------------------------
    # Config hot-reload
    # ------------------------------------------------------------------

    def _apply_config_diff(self, new_cfg: Config) -> None:
        """Apply the subset of config changes that can be hot-reloaded without restart.

        Called on the watchdog observer thread from :meth:`_on_config_changed`.

        Currently handled diffs
        -----------------------
        * ``audio.device_name`` — updates ``_recorder._device_name`` so the new
          device name is used on the next :meth:`open_session` call.  An
          already-open session is NOT closed — the change takes effect silently
          on the next session open.

        TODO: more diffs as needed (logging level, web port, etc.)
        """
        if self._cfg is None:
            return

        if new_cfg.audio.device_name != self._cfg.audio.device_name:
            logger.info(
                "config hot-reload: audio.device_name %r → %r (takes effect on next session open)",
                self._cfg.audio.device_name,
                new_cfg.audio.device_name,
            )
            if self._recorder is not None:
                # str assignment is atomic in CPython; the recorder reads
                # _device_name inside open_session() under _state_lock, so
                # there is no observable race.
                self._recorder._device_name = new_cfg.audio.device_name

    def _on_config_changed(self, path: Path) -> None:
        """Callback invoked by :class:`ConfigWatcher` when config.toml changes.

        Runs on the watchdog observer thread.  Parses the new config, diffs
        against the current snapshot, and applies supported hot-reload changes.
        Publishes a ``config_reloaded`` event on success so the sprite / UI can
        reflect the reload.
        """
        try:
            new_cfg = Config.load(path)
        except Exception:
            logger.exception("config hot-reload: failed to parse %s", path)
            return

        self._apply_config_diff(new_cfg)
        self._cfg = new_cfg
        self._publish("config_reloaded", {"path": str(path)})

    # ------------------------------------------------------------------
    # Session lifecycle helpers (ADR 0090)
    # ------------------------------------------------------------------

    def _open_voice_session(self) -> bool:
        """Open the audio pipeline and transition to session_active.

        Thread context: hotkey-listener thread only (called from on_scroll_lock
        and on_dictation_toggle, both of which run exclusively on the pynput
        listener thread).  Must not block beyond recorder.open_session().

        Returns True on success, False if the recorder failed (error already
        surfaced via feedback.on_error).  Does NOT touch _session_opened_by_dictation
        — that flag is set by the caller (on_dictation_toggle) when appropriate.
        on_scroll_lock never sets the flag.
        """
        if self._recorder is None:
            logger.warning("_open_voice_session: recorder not initialised; ignoring")
            return False
        self._audio_gen += 1
        try:
            self._recorder.open_session()
            self._session_active = True
            self._feedback.on_recording_start()
            self._publish("session_started")
            self._publish("unmuted")
            logger.info("Session opened")
            return True
        except Exception as e:
            self._session_active = False
            self._feedback.on_error("recorder.open_session", e)
            return False

    def _close_voice_session(self, cancel_dictation: bool = True) -> None:
        """Close the audio pipeline, drain the utterance queue, and reset state.

        Thread context: hotkey-listener thread (called directly from on_scroll_lock)
        OR _dictation_executor worker thread (submitted by _end_owned_session_if_needed).
        Idempotent: recorder.close_session() is a no-op when already IDLE.
        Always resets _session_opened_by_dictation to False.

        ``cancel_dictation`` controls whether an active DictationSession is
        cancelled on close. Pass ``False`` from ``_end_owned_session_if_needed``
        so that ``_finalize_dictation`` (queued immediately after on the same
        single-worker executor) can call ``session.finish()`` normally and return
        the stabilised transcript. Pass the default ``True`` from all other
        callers (Scroll Lock close, shutdown) where the session should be
        discarded immediately.

        IMPORTANT — executor-submission rule:
        When invoked from pipeline-thread code (the dictation-end paths in
        _process_utterance and _finalize_pending_dictation_end), this method MUST
        only ever be called by submitting _end_owned_session_if_needed to
        _dictation_executor — NEVER called directly on the pipeline thread.
        A direct call on the pipeline thread would run recorder.close_session(),
        which joins the vad-worker thread (up to 5 s timeout), stalling the
        pipeline thread and preventing it from draining _utt_q.  The vad-worker
        depends on the pipeline draining _utt_q to finish its flush and exit —
        calling close_session() directly from the pipeline thread creates a
        circular wait / deadlock.

        When called from the hotkey-listener thread (on_scroll_lock close path),
        this is safe: the hotkey-listener thread is not the pipeline thread, so
        the pipeline continues draining _utt_q and the vad-worker join completes
        promptly.

        Intentional divergences from verbatim on_scroll_lock extraction (see plan §3.2):
        - Adds `if self._recorder is None: return` guard (helper can be called independently).
        - Exception log message changed from "during scroll-lock close" to generic
          (helper is called from multiple thread contexts, not only scroll-lock).
        - Adds `self._session_opened_by_dictation = False` (new field, no equivalent in original).
        """
        if self._recorder is None:
            self._session_active = False
            self._session_opened_by_dictation = False
            return
        self._audio_gen += 1
        try:
            self._recorder.close_session()
        except Exception:
            logger.exception("close_session() failed")
        self._drain_utt_q()
        self._session_active = False
        self._session_opened_by_dictation = False
        if cancel_dictation:
            if self._dictation_session is not None and self._dictation_session.active:
                self._dictation_session.cancel()
        if self._elements_session is not None and self._elements_session.active:
            self._elements_session.cancel()
        if self._mode_session is not None and self._mode_session.active:
            self._mode_session.reset()
        self._feedback.on_recording_stop()
        self._publish("muted")
        self._publish("session_stopped")
        logger.info("Session closed")

    def _end_owned_session_if_needed(self) -> None:
        """Close the voice session if it was opened by Right Ctrl (ADR 0090).

        Contract (MUST be honoured by every caller):
        - MUST only ever be invoked by submitting it to _dictation_executor as a
          callable — NEVER called directly on the pipeline thread (vc-pipeline).
          A direct call on the pipeline thread would block on recorder.close_session()'s
          vad-worker join (up to 5 s timeout), stalling _utt_q drainage and creating
          a circular wait with the vad-worker thread.
        - Runs on the _dictation_executor worker thread (FIFO, single-worker).
        - Safe to call even when the session is already inactive (empty buffer /
          session already closed by a concurrent Scroll Lock press).
        - DictationSession.cancel() and recorder.close_session() are both idempotent;
          double-cancel / double-close races are benign.
        - When _session_opened_by_dictation is False (e.g. Scroll Lock session, or
          already closed by a concurrent hotkey press), this is a no-op.
        """
        if self._session_opened_by_dictation:
            self._close_voice_session(cancel_dictation=False)

    # ------------------------------------------------------------------
    # Hotkey callbacks
    # ------------------------------------------------------------------

    def on_scroll_lock(self) -> None:
        """Toggle the voice session on/off.

        Thread context: called exclusively on the **pynput hotkey-listener thread**.
        Must not block — delegates all heavy work to other threads via queues.
        pynput serialises key callbacks so concurrent invocations cannot happen.

        State transitions:

        * **Open → close**: calls ``_close_voice_session()``, which drains ``_utt_q``,
          sets ``_session_active = False``, resets ``_session_opened_by_dictation``,
          and publishes ``session_stopped``.
        * **Closed → open**: calls ``_open_voice_session()`` (spawns VAD worker),
          sets ``_session_active = True``, publishes ``session_started``.
          Never sets ``_session_opened_by_dictation`` — Scroll Lock sessions always
          leave the flag ``False``.

        Guard: no-op (with a warning log) if ``_recorder`` is ``None`` — i.e. the
        daemon was constructed but the recorder has not yet been wired in.
        """
        if self._recorder is None:
            logger.warning("on_scroll_lock called but recorder is not yet initialised; ignoring")
            return
        if self._session_active:
            self._close_voice_session()
        else:
            self._open_voice_session()

    def on_dictation_toggle(self) -> None:
        """Right-control callback: toggle dictation mode.

        When no voice session is open (``_session_active == False``):
        opens a self-contained session via ``_open_voice_session()``, sets
        ``_session_opened_by_dictation = True``, and starts dictation immediately.
        When dictation ends the session is auto-closed by ``_end_owned_session_if_needed``.

        When a session is already open (``_session_active == True``):
        press once to start dictation, press again to end it (an alternative to
        saying the end word). Behaviour is unchanged from ADR 0086.
        """
        if not self._session_active:
            # Right Ctrl with no open session → open one and enter dictation immediately.
            # (ADR 0090) The session is self-contained: when dictation ends, the
            # auto-close helper closes the recorder and resets _session_active.
            if not self._open_voice_session():
                return  # recorder failed; error already surfaced by the helper
            self._session_opened_by_dictation = True
            if self._dictation_session is None:
                return
            self._dictation_session.start(self._load_vocab())
            return
        if self._dictation_session is None:
            return
        if self._dictation_session.active:
            self._dictation_session.request_end()
            # Wake the pipeline thread if it is blocked in get(timeout=None).
            # Without this, the thread only re-evaluates pending_end at the
            # top of the NEXT iteration — which never comes if the user
            # stops speaking. The sentinel is consumed by the pipeline loop
            # via identity check; real utterances ahead of it are processed
            # first (FIFO). On queue.Full, log and do nothing: a full queue
            # means the pipeline is already non-idle and will re-evaluate
            # pending_end naturally. (ADR 0089)
            try:
                self._utt_q.put_nowait(_DICTATION_WAKE)
            except queue.Full:
                logger.warning(
                    "dictation: _utt_q full — sentinel not needed; pipeline already active"
                )
            logger.info("dictation: hotkey-end requested; pipeline will drain and finalize")
        else:
            self._dictation_session.start(self._load_vocab())

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
            # When a hotkey-end is pending we use a short timeout so the pipeline
            # can detect a drained queue and finalise without waiting indefinitely
            # for audio that will never arrive.  Non-dictation path is unchanged:
            # a plain blocking get() so no spurious CPU spin.
            pending = self._dictation_session is not None and self._dictation_session.pending_end
            try:
                if pending:
                    item = self._utt_q.get(timeout=_DICTATION_DRAIN_TIMEOUT_S)
                else:
                    item = self._utt_q.get(timeout=None)
            except queue.Empty:
                # Timed out while waiting for more audio during hotkey-end drain.
                # The queue is empty; finalise the dictation now.
                self._finalize_pending_dictation_end()
                continue

            if item is None:
                break
            # Skip the wake sentinel — its only job was to unblock get().
            # The top of the next iteration will see pending_end=True and
            # switch to the short-timeout drain. (ADR 0089)
            if item is _DICTATION_WAKE:
                continue
            # Items enqueued by _on_utterance are (audio, gen) tuples.
            # Tests inject bare ndarrays directly — treat those as gen=None
            # (skip the generation check) for backward compatibility.
            if isinstance(item, tuple):
                utterance, gen = item
            else:
                utterance, gen = item, None
            try:
                self._process_utterance(utterance, gen=gen)
            except MemoryError:
                raise
            except Exception as e:
                cat = _classify_error(e, where="daemon")
                logger.exception("unhandled exception in utterance processing (category=%s)", cat)
                self._feedback.on_error("pipeline", e)

    def _process_utterance(
        self,
        utterance: npt.NDArray[np.float32],
        *,
        gen: int | None = None,
    ) -> None:
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
        3. **confidence** — below ``_min_confidence`` → miss chime +
           ``plan_outcome`` (status=``miss``).
        4. **VerbRouter** — ``VerbRouter.route()`` returns ``None`` → miss chime + ``plan_outcome``.
        5. **dispatch** — ``Dispatcher.run_plan()`` executes the plan.

        Args:
            utterance: 1-D float32 ndarray at 16 kHz representing the complete
                utterance segment produced by the VAD gate.

        Does not raise: all exceptions are caught by :meth:`_pipeline_loop` and
        routed to ``feedback.on_error``.
        """
        # Async write for post-mortem debugging
        self._write_utterance_async(utterance)

        # Pre-transcribe generation check: cheap escape when scroll-lock
        # close has already invalidated this utterance. Saves the transcribe()
        # cost and avoids any side-effects.
        if gen is not None and gen != self._audio_gen:
            logger.debug(
                "Stale-gen drop (pre-transcribe): utt gen=%s, current=%s",
                gen,
                self._audio_gen,
            )
            return

        # Gate: wait for transcriber model to finish loading.  On timeout (30 s)
        # the utterance is dropped with a miss chime — the daemon stays up.
        if not self._transcriber_ready.wait(timeout=30):
            logger.warning("Transcriber not ready within 30 s; dropping utterance")
            self._feedback.on_miss("(transcriber not ready)", ())
            return

        with self._tracer.run("") as run:
            start_s = time.perf_counter()

            def _publish_picker_ok(transcript: str) -> None:
                """Terminal-status emit for picker open / cancel paths so the
                runs panel does not orphan the utterance."""
                self._publish(
                    "plan_outcome",
                    PlanOutcome(
                        transcript=transcript,
                        steps=(),
                        status="ok",
                        failed_step_index=None,
                        error_msg=None,
                        duration_ms=int((time.perf_counter() - start_s) * 1000),
                    ).to_event_dict(),
                )

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

            # Stale-generation guard: scroll-lock-close bumps _audio_gen.
            # Any utterance whose enqueue-time gen differs from the current
            # gen was captured under a now-invalid audio session and must be
            # dropped silently. This catches the race window where
            # transcribe() finishes BEFORE close_session() returns (which
            # blocks on the VAD worker join) — the utterance is already past
            # the enqueue check, but its generation has been invalidated.
            if gen is not None and gen != self._audio_gen:
                logger.debug(
                    "Stale-gen drop (post-transcribe): utt gen=%s, current=%s, text=%r",
                    gen,
                    self._audio_gen,
                    result.text,
                )
                return

            self._feedback.on_transcript(result.text, result.confidence)
            # Suppress the HUD `transcript` event during dictation (ADR 0096 D5).
            # Under server-side dictation there are no partials to display — the
            # HUD stays silent until paste.  The per-utterance LOG line below is
            # preserved for end-word detection debugging.  The `transcript` event
            # continues to fire in command mode (dictation_session not active).
            _dictating_now = self._dictation_session is not None and self._dictation_session.active
            if not _dictating_now:
                self._publish(
                    "transcript",
                    {"text": result.text, "confidence": result.confidence},
                )

            # Dictation sub-state (ADR 0086): while active, every utterance is
            # dictation content (buffered) or the end word — never a command.
            if self._dictation_session is not None and self._dictation_session.active:
                kind = self._dictation_session.handle_utterance(utterance, result.text)
                if kind == "end":
                    # Close-before-finalize (ADR 0090): submit the session close
                    # FIRST so recording stops promptly (≤5 s VAD-worker join)
                    # before _finalize_dictation joins the WS asyncio thread.
                    # _end_owned_session_if_needed is a no-op when
                    # _session_opened_by_dictation is False (Scroll Lock session).
                    # _finalize_dictation itself calls session.finish() — it must
                    # be submitted unconditionally (no audio handle to check).
                    self._dictation_executor.submit(self._end_owned_session_if_needed)
                    self._dictation_executor.submit(self._finalize_dictation)
                elif kind == "cancel":
                    # Spoken cancel: abort dictation (no transcript, no paste).
                    # cancel() closes the WS and publishes
                    # dictation.end {"reason": "cancel"}.
                    self._dictation_session.cancel()
                    self._dictation_executor.submit(self._end_owned_session_if_needed)
                # "buffered" → utterance streamed inside handle_utterance.
                run.set_status("ok")
                return

            # Elements mode (ADR 0087): while a scan is in flight or hints are
            # shown, every utterance belongs to elements mode — never a command.
            if self._elements_session is not None:
                state = self._elements_session.state
                if state is ElementsState.SCANNING:
                    run.set_status("ok")
                    return
                if state is ElementsState.HINTS_SHOWN:
                    element = self._elements_session.handle_utterance(result.text)
                    if element is not None:
                        self._elements_executor.submit(self._do_element_click, element)
                    run.set_status("ok")
                    return
                if _normalize_spoken(result.text) in ENTRY_WORDS:
                    self._elements_session.begin_scan()
                    self._feedback.on_recording_start()  # scan-start chime
                    self._elements_executor.submit(self._do_element_scan)
                    run.set_status("ok")
                    return

            # Picker sub-state (ADR 0083): if a bare-primitive picker is open,
            # the next utterance is a selection, not a new command.
            if self._picker_session is not None and self._picker_session.active:
                outcome = self._picker_session.handle_transcript(result.text)
                if outcome is None:
                    pass  # closed between check and call — fall through
                elif outcome.kind == "select" and outcome.plan is not None:
                    if self._registry is None:
                        logger.error("Registry not set — cannot run picker selection")
                        return
                    self._dispatcher.run_plan(result.text, outcome.plan, self._registry)
                    return
                elif outcome.kind == "cancel":
                    run.set_status("ok")
                    self._feedback.on_plan_complete(result.text, 0)
                    _publish_picker_ok(result.text)
                    return
                else:  # miss — out-of-range / non-number
                    run.set_status("miss")
                    self._feedback.on_miss(result.text, ())
                    _publish_miss(result.text)
                    return

            # Mode sub-state (named modes, ADR 0100): while a mode is active,
            # utterances route against that mode's catalog + primitives only;
            # the end phrase exits. A miss stays in the mode (chime, no route).
            if self._mode_session is not None and self._mode_session.active:
                mode_outcome = self._mode_session.handle_utterance(result.text)
                if mode_outcome.kind == "exit":
                    self._feedback.on_mode_exit()
                    run.set_status("ok")
                    return
                if mode_outcome.kind == "plan" and mode_outcome.plan is not None:
                    if self._registry is None:
                        logger.error(
                            "Registry not set — cannot run mode plan for '%s'", result.text
                        )
                        return
                    self._dispatcher.run_plan(result.text, mode_outcome.plan, self._registry)
                    return
                # "miss" — stay in mode, chime once.
                run.set_status("miss")
                self._feedback.on_miss(result.text, ())
                _publish_miss(result.text)
                return

            # Mode-trigger intercept (normal mode only): a trigger word enters a mode.
            if self._mode_session is not None:
                entered = self._mode_session.try_enter(result.text)
                if entered is not None:
                    self._feedback.on_mode_enter()
                    run.set_status("ok")
                    return

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

            # Gate: confidence  (emits plan_outcome status=miss — user-visible)
            if result.confidence < self._min_confidence:
                run.set_status("miss")
                self._feedback.on_miss(result.text, ())
                _publish_miss(result.text)
                return

            plan = self._verb_router.route(result.text)

            if plan is None:
                # No match in the command/workflow catalog. Chime once and stop —
                # no agentic retry, no env-seeded second call. The user can either
                # rephrase or add a command for the missing intent via the UI.
                run.set_status("miss")
                self._feedback.on_miss(result.text, ())
                _publish_miss(result.text)
                return
            if (
                self._picker_session is not None
                and self._picker_registry is not None
                and len(plan.steps) == 1
                and plan.steps[0].name == "__picker.open"
            ):
                verb = str(plan.steps[0].kwargs.get("verb", ""))
                provider = self._picker_registry.get(verb)
                if provider is None:
                    logger.warning("picker open requested for unknown verb %r", verb)
                    self._feedback.on_miss(result.text, ())
                    _publish_miss(result.text)
                    return
                items = provider()
                if not items:
                    logger.info("picker %r produced empty list", verb)
                    self._feedback.on_miss(result.text, ())
                    _publish_miss(result.text)
                    return
                self._picker_session.open(verb, items)
                run.set_status("ok")
                self._feedback.on_plan_complete(result.text, 0)
                _publish_picker_ok(result.text)
                return
            if len(plan.steps) == 1 and plan.steps[0].name == "__dictation.start":
                if self._dictation_session is not None:
                    self._dictation_session.start(self._load_vocab())
                run.set_status("ok")
                self._feedback.on_plan_complete(result.text, 0)
                _publish_picker_ok(result.text)
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

    def _finalize_pending_dictation_end(self) -> None:
        """Called on the pipeline thread when the hotkey-end drain window expires.

        Submits the close + finalize tasks to the dictation executor and returns
        immediately — all heavy work (WS teardown, asyncio-thread join,
        post-processing, paste) happens off-thread on ``_dictation_executor``.

        Close-before-finalize ordering (ADR 0090):
        1. submit ``_end_owned_session_if_needed`` UNCONDITIONALLY — covers the
           empty-session Ctrl-open → Ctrl-close case.
        2. submit ``_finalize_dictation`` — it calls ``session.finish()``, which
           is a no-op returning ``""`` when the session is already inactive
           (the "done"-word path won the race), so submitting it is always safe.
        """
        if self._dictation_session is None:
            return
        self._dictation_executor.submit(self._end_owned_session_if_needed)
        self._dictation_executor.submit(self._finalize_dictation)
        logger.info("dictation: hotkey-end drain complete — close + finalize submitted")

    def _load_vocab(self) -> "Vocabulary":
        """Hot-reload vocab.json — called once per dictation at session start."""
        return self._vocab_store.load()

    def _finalize_dictation(self) -> None:
        """Worker-thread finalize: take the stabilised transcript → post-process → paste.

        Runs on ``self._dictation_executor`` so the pipeline thread is never
        blocked by the WebSocket teardown / join. ``DictationSession.finish``
        joins the asyncio-loop thread and returns the raw stabilised
        transcript; this method applies corrections + commands, pastes, saves
        ``last.txt``, and publishes events.

        Failure handling (events + chimes match the batch path exactly,
        ADR 0092):

        - WebSocket connect failed / nothing transcribed → ``dictation.error
          {reason: "endpoint"}`` + miss chime.
        - ``encode_wav`` failed on a streamed chunk → ``dictation.error
          {reason: "encode"}`` + miss chime.
        - ``paste_via_clipboard`` failed → ``dictation.error
          {reason: "clipboard"}`` + miss chime.

        Corrections run before commands so a lightly-mistranscribed command
        phrase can be repaired into its canonical form before command matching
        (ADR 0088, carried over).
        """
        import time as _time

        from .dictation import clipboard
        from .dictation.postprocess import apply_commands, apply_corrections
        from .dictation.timing import build_timing_record

        if self._dictation_session is None:
            return

        # Snapshot the vocab the session started with (one consistent snapshot
        # per dictation — ADR 0092).
        vocab = self._dictation_session.vocab

        # finish() joins the asyncio thread and returns the raw transcript.
        text = self._dictation_session.finish()

        # encode_wav failure on a chunk is recorded on session.error == "encode".
        if self._dictation_session.error == "encode":
            logger.warning("dictation: a chunk failed to encode")
            self._publish("dictation.error", {"reason": "encode"})
            self._feedback.on_miss("(dictation: encode error)", ())
            return

        # Empty transcript: either the WS never connected, or the user said
        # nothing. error == "endpoint" disambiguates the failure case.
        if not text:
            if self._dictation_session.error == "endpoint":
                logger.warning("dictation: streaming endpoint failed")
                self._publish("dictation.error", {"reason": "endpoint"})
                self._feedback.on_miss("(dictation: endpoint error)", ())
            else:
                logger.info("dictation: empty transcript — nothing to paste")
            return

        # Post-process: corrections then commands (ADR 0088).
        _t0_pp = _time.monotonic()
        text = apply_corrections(text, vocab.corrections)
        text = apply_commands(text, vocab.commands)
        postprocess_ms = (_time.monotonic() - _t0_pp) * 1000.0

        self._dictation_store.save_text(text)

        _t0_paste = _time.monotonic()
        try:
            clipboard.paste_via_clipboard(text)
        except Exception:
            logger.exception("dictation: clipboard paste failed")
            self._publish("dictation.error", {"reason": "clipboard"})
            self._feedback.on_miss("(dictation: clipboard error)", ())
            return
        paste_ms = (_time.monotonic() - _t0_paste) * 1000.0

        # Record per-phase timing for the successful paste path (ADR 0101).
        # get_timings() is safe here — finish() above joined the asyncio thread.
        _ws_timings = self._dictation_session.get_timings()
        record = build_timing_record(
            text=text,
            server_timings=_ws_timings["server"],
            roundtrip_ms=_ws_timings["roundtrip_ms"],
            postprocess_ms=postprocess_ms,
            paste_ms=paste_ms,
        )
        self._dictation_store.save_timings(record)
        self._publish("dictation.timings", record)
        logger.info(
            "dictation timing: total %.0fms (rt %.0f post %.0f paste %.0f)",
            record["total_ms"],
            record["roundtrip_ms"] or 0.0,
            postprocess_ms,
            paste_ms,
        )

        self._publish("transcript", {"text": text, "confidence": 1.0})
        self._publish("dictation.result", {"text": text})
        logger.info("dictation: pasted %d chars", len(text))

    def _do_element_scan(self) -> None:
        """Worker-thread scan: enumerate the foreground window's elements."""
        if self._elements_session is None:
            return
        try:
            hwnd = desktop.foreground_window()
            if hwnd == 0:
                self._elements_session.fail()
                self._feedback.on_miss("(elements: no active window)", ())
                return
            monitor = desktop.monitor_rect(hwnd)
            elements = scanner.scan_window(
                hwnd,
                max_elements=self._elements_max_elements,
                timeout_s=self._elements_scan_timeout_s,
            )
            if not elements:
                self._elements_session.fail()
                self._feedback.on_miss("(elements: nothing clickable found)", ())
                return
            self._elements_session.show(elements, monitor)
        except Exception:
            logger.exception("elements scan failed")
            self._elements_session.fail()
            self._feedback.on_miss("(elements: scan error)", ())

    def _do_element_click(self, element: scanner.Element) -> None:
        """Worker-thread click: left-click the chosen element's center."""
        try:
            clicker.click_point(*element.center)
        except Exception:
            logger.exception("elements click failed")
            self._feedback.on_miss("(elements: click error)", ())

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
        # Snapshot _audio_gen at enqueue time. The pipeline worker uses this
        # to drop utterances whose generation has been invalidated by a
        # concurrent scroll-lock-close before they reach (or after
        # they pass) transcribe(). Reading an int is atomic in CPython.
        gen = self._audio_gen
        try:
            self._utt_q.put_nowait((utterance, gen))
        except queue.Full:
            logger.warning("utt_q full — dropping utterance (%d samples)", len(utterance))
            self._feedback.on_miss("(queue overflow)", ())

    def _tick_picker_session(self) -> None:
        if self._picker_session is not None:
            self._picker_session.tick()

    def _heartbeat_loop(self) -> None:
        while not self._shutdown.wait(1.0):
            self._publish("daemon_heartbeat")
            self._tick_picker_session()

    # ------------------------------------------------------------------
    # Run / shutdown
    # ------------------------------------------------------------------

    def run(self, hotkey_key: str, dictation_key: str = "") -> None:
        """Blocking entry point — warm up models, start all threads, block until shutdown.

        Thread context: **must be called from the main thread** so that the
        SIGINT handler can be installed.  Logs a warning and skips SIGINT
        registration if called from any other thread.  Blocks via
        :meth:`threading.Event.wait` with a 0.5 s poll interval (see
        ``docs/gotchas.md`` §9 — Windows Ctrl+C requires polling, not a plain
        blocking wait).

        Threads started:

        * ``vc-transcriber-load`` — background daemon thread that calls
          :meth:`Transcriber.load`; sets ``_transcriber_ready`` on success.
          On failure, logs and calls ``feedback.on_error("transcriber.load", ...)``
          without aborting the daemon.
        * ``vc-pipeline`` — runs :meth:`_pipeline_loop` (transcribe/gate/route/dispatch).
          Waits on ``_transcriber_ready`` before the first :meth:`transcribe` call.
        * ``vc-heartbeat`` — runs :meth:`_heartbeat_loop` at 1 Hz.

        Side effects:

        * Starts :attr:`_web_server` (if configured) before the hotkey listener
          so the UI is responsive as soon as the daemon is ready for input.
        * Starts :class:`HotkeyController` with the provided key bindings.
        * Transcriber model load happens in a background thread — the web UI
          and hotkey listener are available immediately.

        Args:
            hotkey_key: Key name for session toggle (e.g. ``"scroll_lock"``).
            dictation_key: Optional secondary key name (e.g. ``"ctrl_r"``) bound to
                :meth:`on_dictation_toggle`. Empty string disables the binding.

        Raises:
            Exception: Re-raises only if :meth:`HotkeyController.start` fails
                (after logging the error and calling ``feedback.on_error``).
                Transcriber load failures are non-fatal — the daemon stays up.
        """
        # Load transcriber model in the background so web UI + hotkey listener
        # come up immediately.  The pipeline worker blocks on _transcriber_ready
        # before its first transcribe() call; if load fails the daemon stays up.
        self._publish("warmup_start")

        def _load_transcriber() -> None:
            try:
                self._transcriber.load()
                self._transcriber_ready.set()
                self._publish("warmup_done")
                logger.info("Transcriber loaded successfully (background)")
            except Exception as e:
                logger.exception("Transcriber.load() failed in background; daemon stays up")
                self._feedback.on_error("transcriber.load", e)

        threading.Thread(
            target=_load_transcriber,
            name="vc-transcriber-load",
            daemon=True,
        ).start()

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
            if dictation_key:
                if dictation_key == hotkey_key:
                    logger.warning(
                        "dictation_key %r equals hotkey %r; dictation binding ignored",
                        dictation_key,
                        hotkey_key,
                    )
                else:
                    bindings[dictation_key] = self.on_dictation_toggle
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
        or the SIGINT handler).
        Multiple concurrent callers are safe — the first caller acquires
        ``_shutdown_lock``, sets the ``_shutdown`` event, and performs teardown;
        subsequent callers return immediately on the ``_shutdown.is_set()`` check.

        Locks touched: acquires ``_shutdown_lock`` to atomically check and set
        the ``_shutdown`` event.

        Teardown order:

        1. Stop the web server (no late UI requests land on a half-dead registry).
        2. Close any open recording session (calls ``recorder.close_session()`` if
           ``_session_active`` is still set).
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

        # Cancel active dictation directly — BEFORE executor shutdown (REV 3, ADR 0090 §5).
        # Placement: after session-close block and pipeline join, BEFORE
        # _dictation_executor.shutdown(wait=False).  Rationale: cancel() pushes the end
        # sentinel and joins the dictation WS asyncio-loop thread (bounded by
        # idle_timeout_s + margin; fast in practice). It does not touch the executor,
        # so calling it directly here is safe.  Publishing dictation.end BEFORE executor
        # teardown keeps event ordering clean.  executor.shutdown(wait=False) abandons
        # queued tasks — any _end_owned_session_if_needed already queued will not
        # execute — so the direct cancel call here is the only reliable path to clear
        # the dictating state and prevent the sprite from being stuck in the 'dictating'
        # visual state.
        if self._dictation_session is not None and self._dictation_session.active:
            self._dictation_session.cancel()

        # Shut down the WAV writer executor.
        self._wav_executor.shutdown(wait=False)
        self._dictation_executor.shutdown(wait=False)
        self._elements_executor.shutdown(wait=False)

        # Stop config file watcher.
        if self._config_watcher is not None:
            try:
                self._config_watcher.stop()
            except Exception:
                logger.exception("Error stopping config watcher")
            self._config_watcher = None

        # Stop modes hot-reload watcher.
        if self._modes_watcher is not None:
            try:
                self._modes_watcher.stop()
            except Exception:
                logger.exception("Error stopping modes watcher")
            self._modes_watcher = None

        # Stop MRU pump (ADR 0083 — picker framework).
        if self._mru_pump is not None:
            try:
                self._mru_pump.stop()
            except Exception:
                logger.exception("Error stopping MRU pump")
            self._mru_pump = None

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


def build_mode_session(cfg: Config, bus: EventBus) -> ModeSession | None:
    """Build a ModeSession from config (None when modes are disabled).

    Extracted as a module-level helper so integration tests can exercise mode
    wiring without constructing the full audio/transcription stack.
    """
    if not cfg.modes.enabled:
        return None
    registry = ModeRegistry(Path(cfg.modes.dir))
    registry.load_all()
    logger.info("modes: loaded %d mode(s) from %s", len(registry.all()), cfg.modes.dir)
    return ModeSession(bus, registry)


def build_streaming_daemon(cfg: Config, config_path: Path | None = None) -> StreamingDaemon:
    """Factory: wire all subsystems into a StreamingDaemon.

    Also wires the embedded web UI when enabled. The metadata store, registry,
    LLM router, and web app all share a single ``threading.Lock`` so hot reloads
    from the UI never race with the LLM router reading tool metadata on the
    pipeline thread.
    """
    # Torch eliminated by in-house onnxruntime VAD wrapper (voice_commander.vad_onnx).
    # Thread caps are handled by SessionOptions(inter/intra_op_num_threads=1).
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
    logger.info(
        "Transcription backend: local (model=%s device=%s)",
        cfg.transcription.model_size,
        cfg.transcription.device,
    )

    # Metadata store + reload lock shared between registry and web server.
    tools_dir = Path(__file__).resolve().parent / "tools"
    store = ToolMetadataStore(tools_dir)
    reload_lock = threading.Lock()

    registry = discover("voice_commander.tools", store=store)

    # Generate JSON schemas for all tools.
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

    # Wire the parameter resolver's threshold accessors to the live config
    # so focus/open read focus_fuzzy_threshold / open_fuzzy_threshold from TOML.
    param_resolver._set_config(cfg)

    event_bus = EventBus()

    # --- Bare-primitive picker (ADR 0083) ---
    from .picker.mru import MruTracker, Win32MruPump
    from .picker.registry import get_global_picker_registry, reset_global_picker_registry
    from .picker.session import PickerSession
    from .tools.focus_picker import FocusPickerSettings, register_focus_picker
    from .tools.tabs_picker import TabsPickerSettings, register_tabs_picker

    picker_session: PickerSession | None = None
    # Reset the global picker registry so build_streaming_daemon is idempotent —
    # repeated factory calls (test harness, hot-reload) must not collide on
    # duplicate `focus` registrations.
    reset_global_picker_registry()
    picker_registry = get_global_picker_registry()
    mru_tracker: MruTracker | None = None
    mru_pump: Win32MruPump | None = None

    if cfg.picker.enabled:
        mru_tracker = MruTracker(capacity=max(8, cfg.picker.focus.cap * 4))

        def _foreground_hwnd() -> int:
            try:
                import win32gui

                return int(win32gui.GetForegroundWindow() or 0)
            except Exception:
                return 0

        register_focus_picker(
            tracker=mru_tracker,
            settings=FocusPickerSettings(
                cap=cfg.picker.focus.cap,
                exclude_foreground=cfg.picker.focus.exclude_foreground,
                exclude_self=cfg.picker.focus.exclude_self,
            ),
            foreground_hwnd=_foreground_hwnd,
        )

        # Tabs picker — saying "tabs." opens a numbered modal of the
        # foreground Chromium browser's page tabs. Shares the foreground
        # accessor with focus so both pickers see the same active window.
        register_tabs_picker(
            foreground_hwnd=_foreground_hwnd,
            settings=TabsPickerSettings(cap=cfg.picker.tabs.cap),
        )

        picker_session = PickerSession(
            bus=event_bus,
            cancel_words=tuple(cfg.picker.cancel_words),
            timeout_sec=float(cfg.picker.timeout_sec),
        )

        mru_pump = Win32MruPump(tracker=mru_tracker)
        mru_pump.start()

    # --- Dictation mode (ADR 0086, raw-PCM streaming since ADR 0096) ---
    dictation_session = DictationSession(
        bus=event_bus,
        ws_url=cfg.dictation.ws_url,
        idle_timeout_s=float(cfg.dictation.idle_timeout_seconds),
        end_word=cfg.dictation.end_word,
        cancel_word=cfg.dictation.cancel_word,
        max_dictation_s=float(cfg.dictation.max_dictation_s),
    )

    # --- Elements mode (ADR 0087) ---
    elements_session = ElementsSession(
        bus=event_bus,
        hint_timeout_s=cfg.elements.hint_timeout_s,
    )

    # --- Named modes (ADR 0100) ---
    mode_session = build_mode_session(cfg, event_bus)

    # Hot-reload watcher for modes/*.toml — built and started when modes are
    # enabled so the registry stays fresh without a daemon restart.
    _modes_watcher: Any = None
    if mode_session is not None:
        _mode_registry = mode_session._registry

        def _on_modes_changed(_path: Path) -> None:
            try:
                _mode_registry.reload()
                event_bus.publish("modes_reloaded", {"count": len(_mode_registry.all())})
                logger.info("modes hot-reload: %d mode(s)", len(_mode_registry.all()))
            except Exception:
                logger.exception("modes hot-reload failed")

        _modes_watcher = ModesWatcher(Path(cfg.modes.dir), _on_modes_changed)
        _modes_watcher.start()

    # Backend keyboard recorder for the Builder UI's `press` combo capture.
    # Single instance, lazy listener (one record session at a time).
    from voice_commander.recorder import KeyRecorder

    key_recorder = KeyRecorder(event_bus)

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
            observability_store=_obs_store,
            observability_tracer=_obs_tracer,
            key_recorder=key_recorder,
        )
        web_server = WebServer(app, host=cfg.web.host, port=cfg.web.port)

    # Build daemon without a recorder first so _on_utterance is available,
    # then wire the recorder with the real callback.
    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=None,
        transcriber=transcriber,
        dispatcher=dispatcher,
        verb_router=VerbRouter(
            build_default_rules(),
            registry=registry,
            picker_registry=picker_registry if cfg.picker.enabled else None,
            chain_parser=ChainParser(
                registry=registry,
                verb_rules=build_default_rules(),
            ),
        ),
        registry=registry,
        picker_session=picker_session,
        picker_registry=picker_registry if cfg.picker.enabled else None,
        dictation_session=dictation_session,
        dictation_ws_url=cfg.dictation.ws_url,
        dictation_idle_timeout_s=float(cfg.dictation.idle_timeout_seconds),
        elements_session=elements_session,
        mode_session=mode_session,
        elements_max_elements=cfg.elements.max_elements,
        elements_scan_timeout_s=cfg.elements.scan_timeout_s,
        min_confidence=cfg.transcription.min_confidence,
        min_word_count=cfg.vad.gates.min_word_count,
        max_no_speech_prob=cfg.vad.gates.max_no_speech_prob,
        output_dir=cfg.audio.output_dir,
        web_server=web_server,
        event_bus=event_bus,
        tracer=_obs_tracer,
        store=_obs_store,
    )
    daemon._mru_pump = mru_pump
    daemon._modes_watcher = _modes_watcher
    daemon._recorder = StreamingRecorder(
        device=cfg.audio.device if cfg.audio.device >= 0 else None,
        channels=cfg.audio.channels,
        vad_gate=vad_gate,
        utterance_sink=daemon._on_utterance,
        device_name=cfg.audio.device_name,
    )

    # Audio self-test: open device for ~200 ms before hotkey becomes active.
    # On failure: log, emit banner with FAIL status, then exit with code 73.
    _st_result = daemon._recorder.self_test()
    _banner_lines = _provenance_banner(cfg, _st_result)
    for _line in _banner_lines:
        logger.info(_line)

    if not _st_result.ok:
        logger.error(
            "Audio self-test failed — device='%s' error=%r; exiting with code 73",
            cfg.audio.device_name or "(default)",
            _st_result.error,
        )
        sys.exit(73)

    # Store live config snapshot so _on_config_changed can diff against it.
    daemon._cfg = cfg

    # Wire config hot-reload watcher.  Resolves config_path relative to cwd
    # when not supplied explicitly (matches how __main__.py loads Config).
    _cfg_path = (config_path or Path("config.toml")).resolve()
    if _cfg_path.exists():
        from .config_watcher import ConfigWatcher

        daemon._config_watcher = ConfigWatcher(_cfg_path, daemon._on_config_changed)
        daemon._config_watcher.start()
        logger.info("config hot-reload: watching %s", _cfg_path)
    else:
        logger.warning(
            "config hot-reload: config file %s not found, watcher not started",
            _cfg_path,
        )

    return daemon
