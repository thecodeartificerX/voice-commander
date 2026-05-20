"""DictationSession — daemon sub-state that streams dictation to a WS server.

While dictation is active the daemon routes every VAD utterance here instead
of the VerbRouter. Audio accumulates in a :class:`~voice_commander.dictation.window.DictationWindow`
via a per-frame tap on the StreamingRecorder (ADR 0095); the growing window is
re-encoded and pushed onto the chunk queue every ``window_step_ms`` of new
audio. An asyncio-loop thread drains that queue and streams the window WAVs to
the ``/ws/transcribe`` server. The server's ``partial`` replies carry both
``text`` and a ``segments`` array that is folded through
:class:`~voice_commander.dictation.local_agreement.LocalAgreement` (LA-2) to
commit stable words and trim the audio window at segment boundaries.

Finalisation happens via one of three exit paths (lifecycle wiring unchanged
from ADR 0086/0089/0090 — only the session internals changed, ADR 0095):

(a) **End-word path** — the pipeline thread recognises the configured end word
    (default ``"done"``), calls :meth:`finish`, which clears the frame tap,
    flushes the window, pushes the end sentinel, joins the asyncio thread,
    reads the proxy's ``done`` frame transcript, and returns the LLM-cleaned
    text (or falls back to ``LocalAgreement`` output when no ``done`` frame
    arrived, ADR 0094).

(b) **Hotkey-end path** — the hotkey thread calls :meth:`request_end`, which
    sets :attr:`pending_end` without deactivating the session. The pipeline
    thread later drains in-flight utterances and calls :meth:`finish`.

(c) **Spoken-cancel path** — :meth:`handle_utterance` recognises the configured
    cancel word and returns ``"cancel"``; the pipeline thread calls
    :meth:`cancel`, which clears the frame tap, discards the window, closes the
    WebSocket and discards the transcript.

Threading: the confirmed-word accumulator + ``LocalAgreement`` are touched by
the asyncio-loop thread (via ``_on_partial``) and by :meth:`finish` (the
``_dictation_executor`` thread). A single lock (``_agreement_lock``)
serialises them — the ADR 0091 Task-9 pattern.

``_final_text`` is written by the asyncio-loop thread (via ``_async_main``)
and read by :meth:`finish` after ``loop_thread.join()``; the join provides
the happens-before edge so no lock is needed — same rationale as the existing
:attr:`error` comment.

A connect failure inside the asyncio thread is recorded on :attr:`error`;
:meth:`finish` then returns whatever ``LocalAgreement`` confirmed (``""`` when
nothing connected) and the daemon emits ``dictation.error`` when ``error`` is
set and the transcript is empty.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from typing import Any, Literal, Protocol

import numpy as np
import numpy.typing as npt

from ..verb_router import _normalize_spoken
from .bridge import pump
from .local_agreement import LocalAgreement, TimedWord
from .postprocess import build_prompt
from .vocab import Vocabulary
from .window import DictationWindow
from .ws_client import stream_transcribe

logger = logging.getLogger(__name__)


class _BusLike(Protocol):
    """The minimal EventBus surface DictationSession depends on.

    Structurally satisfied by :class:`~voice_commander.event_bus.EventBus`.
    """

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None: ...


UtteranceKind = Literal["buffered", "end", "cancel"]

# Extra seconds added to idle_timeout_s when bounding the asyncio-thread join,
# so finish() never blocks the _dictation_executor indefinitely.
_JOIN_MARGIN_S = 10.0


def _segments_to_timed_words(
    segments: list[dict], committed_offset_s: float
) -> list[TimedWord]:
    """Flatten server segments into absolute-stream-timed words (ADR 0095).

    Each segment's ``end`` is window-relative; ``committed_offset_s`` (from
    :class:`~voice_commander.dictation.window.DictationWindow`) shifts it to an
    absolute-stream timestamp. Every word of a segment inherits the segment's
    absolute end-time — faster-whisper segments are not word-timed, and a
    segment-end over-estimate is safe for trimming (it only drops already
    committed audio).
    """
    words: list[TimedWord] = []
    for seg in segments:
        end_s = float(seg.get("end", 0.0)) + committed_offset_s
        for token in str(seg.get("text", "")).split():
            words.append(TimedWord(text=token, end_s=end_s))
    return words


class DictationSession:
    """Streams one dictation to the WebSocket server; owns the transport."""

    def __init__(
        self,
        bus: _BusLike,
        ws_url: str,
        language: str = "en",
        idle_timeout_s: float = 30.0,
        end_word: str = "done",
        cancel_word: str = "cancel",
        window_step_ms: int = 1000,
        window_cap_ms: int = 25000,
    ) -> None:
        self._bus = bus
        self._ws_url = ws_url
        self._language = language
        self._idle_timeout_s = idle_timeout_s
        self._end_word = _normalize_spoken(end_word)
        self._lock = threading.Lock()
        self._active = False
        self._pending_end = threading.Event()
        self._window_step_ms = window_step_ms
        self._window_cap_ms = window_cap_ms
        self._recorder: Any | None = None
        self._window: DictationWindow | None = None

        # --- streaming transport state (recreated per start()) ---
        self._chunk_q: queue.Queue[bytes | None] | None = None
        self._loop_thread: threading.Thread | None = None
        self._agreement = LocalAgreement()
        self._agreement_lock = threading.Lock()
        self._confirmed: list[str] = []
        self._vocab: Vocabulary = Vocabulary()
        # Written by the asyncio-loop thread (_run_asyncio -> "endpoint"). No lock:
        # writers finish before finish() returns and the daemon reads this field;
        # CPython attribute assignment is GIL-atomic.
        self.error: str | None = None
        # Written by the asyncio-loop thread (_async_main -> stream_transcribe
        # return value); read by finish() after loop_thread.join() — the join
        # provides the happens-before edge; no lock needed (same pattern as
        # self.error above). None means no done frame arrived; str (possibly
        # empty) is the proxy's LLM-cleaned transcript (ADR 0094).
        self._final_text: str | None = None
        # Set to True when first seen, prevents log spam.
        self._warned_no_segments: bool = False

        # Validate cancel_word against end_word and emptiness. Cross-field
        # validation lives here (precedent: dictation_key == hotkey_key
        # warn-and-degrade in daemon.py).
        normalized_cancel = _normalize_spoken(cancel_word)
        if not normalized_cancel:
            logger.warning(
                "dictation: cancel_word %r is empty after normalization; "
                "spoken cancel disabled",
                cancel_word,
            )
            self._cancel_word: str | None = None
        elif normalized_cancel == self._end_word:
            logger.warning(
                "dictation: cancel_word %r collides with end_word %r; "
                "spoken cancel disabled to avoid ambiguity",
                cancel_word,
                end_word,
            )
            self._cancel_word = None
        else:
            self._cancel_word = normalized_cancel

    def set_recorder(self, recorder: Any) -> None:
        """Inject the StreamingRecorder for the per-frame audio tap."""
        self._recorder = recorder

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def pending_end(self) -> bool:
        """True when the hotkey-end path has requested finalisation.

        Set by :meth:`request_end`; cleared by :meth:`finish` and
        :meth:`cancel`. Safe to read from any thread without the lock —
        :meth:`threading.Event.is_set` is atomic in CPython.
        """
        return self._pending_end.is_set()

    def request_end(self) -> None:
        """Signal that the hotkey-end path wants to finalise dictation.

        Called from the hotkey thread. Does NOT deactivate the session — the
        pipeline thread polls :attr:`pending_end` and calls :meth:`finish`
        after draining in-flight utterances. A no-op when already inactive.
        """
        with self._lock:
            if not self._active:
                return
            self._pending_end.set()
        logger.debug("dictation: pending_end requested")

    def start(self, vocab: Vocabulary) -> None:
        """Enter dictation mode and open the streaming WebSocket transport.

        *vocab* is the daemon's start-time snapshot of ``vocab.json``. It is
        stored so :meth:`finish` applies corrections/commands against the same
        snapshot, and ``build_prompt(vocab)`` biases the decoder. The asyncio
        thread is spawned here; the WebSocket connect happens inside it, so a
        connect failure is reported asynchronously via :attr:`error`.
        """
        with self._lock:
            if self._active:
                return
            # All per-session state is reset under the lock and BEFORE
            # _active flips to True, so a concurrent handle_utterance can
            # never observe _active==True with a stale/None _chunk_q.
            self._agreement = LocalAgreement()
            self._confirmed = []
            self._vocab = vocab
            self.error = None
            self._final_text = None
            self._warned_no_segments = False
            self._chunk_q = queue.Queue()
            self._pending_end.clear()
            self._window = DictationWindow(
                window_step_ms=self._window_step_ms,
                window_cap_ms=self._window_cap_ms,
            )
            self._active = True
        prompt = build_prompt(vocab)
        self._loop_thread = threading.Thread(
            target=self._run_asyncio,
            args=(self._chunk_q, prompt),
            name="dictation-ws-loop",
            daemon=True,
        )
        self._loop_thread.start()
        if self._recorder is not None:
            self._recorder.set_frame_tap(self._on_frame)
        self._bus.publish("dictation.start", {})
        logger.info("dictation: started (streaming)")

    @property
    def vocab(self) -> Vocabulary:
        """The start-time vocab snapshot — used by the daemon for post-processing."""
        return self._vocab

    def _on_frame(self, frame: npt.NDArray[np.float32]) -> None:
        """Frame tap — append one 16 kHz frame to the window (VAD worker thread)."""
        window = self._window
        chunk_q = self._chunk_q
        if window is None or chunk_q is None:
            return
        wav = window.append(frame)
        if wav is not None:
            logger.info(
                "dictation[debug]: emit window WAV %d bytes, uncommitted=%.2fs, committed_offset=%.2fs",
                len(wav), window.uncommitted_seconds(), window.committed_offset_s,
            )
            chunk_q.put(wav)
        if window.cap_exceeded():
            forced = (
                window.committed_offset_s
                + window.uncommitted_seconds()
                - (self._window_cap_ms / 1000.0)
            )
            window.commit(forced)
            # ADR 0095 fix: cap-trim drops oldest audio that LA-2 may still be
            # tracking via _prev/_committed. Reset agreement state under the
            # lock — _on_partial (loop thread) is the only other writer.
            with self._agreement_lock:
                self._agreement = LocalAgreement()
            logger.debug("dictation: window cap reached — force-committed to %.2fs", forced)

    def handle_utterance(
        self, audio: npt.NDArray[np.float32], text: str
    ) -> UtteranceKind:
        """Classify an utterance: ``"end"``, ``"cancel"``, or ``"buffered"``.

        Streaming-window dictation (ADR 0095): VAD is endpointing-only. This
        method NO LONGER streams the utterance audio — the growing window is
        fed by the frame tap (:meth:`_on_frame`). It only classifies.
        """
        with self._lock:
            if not self._active:
                return "buffered"
            normalized = _normalize_spoken(text)
            if normalized == self._end_word:
                return "end"
            if self._cancel_word is not None and normalized == self._cancel_word:
                return "cancel"
        return "buffered"

    def finish(self) -> str:
        """End dictation normally; return the transcript.

        Clears the frame tap, flushes the window, pushes the ``None`` end
        sentinel, joins the asyncio-loop thread (bounded by
        ``idle_timeout_s + _JOIN_MARGIN_S``), and returns the transcript.

        **Primary source — proxy ``done`` frame (ADR 0094):** after the join,
        if ``stream_transcribe`` returned a ``done`` frame text (stored in
        ``_final_text``), that LLM-cleaned string is returned directly (may be
        empty when whisper heard nothing).

        **Fallback — ``LocalAgreement``:** when ``_final_text is None`` (no
        ``done`` frame arrived — timeout, connection closed, or error), the
        method falls back to the ``LocalAgreement``-stabilised transcript built
        from the accumulated ``partial`` frames.

        Returns ``""`` when the WebSocket never connected or produced nothing.
        Publishes ``dictation.end {reason: "done"}``. Idempotent — a no-op
        returning ``""`` when the session is already inactive (lost race).
        """
        with self._lock:
            if not self._active:
                return ""
            self._active = False
            self._pending_end.clear()
            chunk_q = self._chunk_q
            loop_thread = self._loop_thread
            self._chunk_q = None
            self._loop_thread = None

        # Clear the frame tap BEFORE flushing the window — ensures no new
        # frames are appended after we read the final WAV.
        if self._recorder is not None:
            self._recorder.set_frame_tap(None)
        window = self._window
        self._window = None
        if window is not None and chunk_q is not None:
            final_wav = window.flush()
            if final_wav is not None:
                chunk_q.put(final_wav)

        if chunk_q is not None:
            chunk_q.put(None)  # end sentinel — bridge.pump forwards it
        if loop_thread is not None:
            loop_thread.join(timeout=self._idle_timeout_s + _JOIN_MARGIN_S)
            if loop_thread.is_alive():
                logger.warning(
                    "dictation: ws-loop thread did not stop within %.0fs — "
                    "transcript may be incomplete",
                    self._idle_timeout_s + _JOIN_MARGIN_S,
                )

        # Always run LocalAgreement.finalize() so the fallback path has the
        # complete confirmed-word accumulator ready.
        with self._agreement_lock:
            self._confirmed.extend(self._agreement.finalize())
            fallback_text = " ".join(self._confirmed)

        # _final_text is set by the asyncio-loop thread (join above provides
        # the happens-before edge). Use the proxy's LLM-cleaned done.text when
        # available; fall back to LocalAgreement output otherwise (ADR 0094).
        if self._final_text is not None:
            text = self._final_text
            logger.debug("dictation: using proxy done.text (%d chars)", len(text))
        else:
            text = fallback_text
            logger.debug(
                "dictation: no done frame — falling back to LocalAgreement (%d chars)",
                len(text),
            )

        self._bus.publish("dictation.end", {"reason": "done"})
        logger.info("dictation: finished (streaming) — %d chars", len(text))
        return text

    def _build_raw_transcript(self) -> str:
        """Return the full committed raw transcript — called by stream_transcribe."""
        with self._agreement_lock:
            self._confirmed.extend(self._agreement.finalize())
            return " ".join(self._confirmed)

    def cancel(self) -> None:
        """Abort dictation; close the WebSocket and discard the transcript.

        Pushes the end sentinel so the asyncio thread unwinds, joins it with a
        bounded timeout, discards the confirmed words, and publishes
        ``dictation.end {reason: "cancel"}``. A no-op (no event) when the
        session was already inactive.
        """
        with self._lock:
            was_active = self._active
            self._active = False
            self._pending_end.clear()
            chunk_q = self._chunk_q
            loop_thread = self._loop_thread
            self._chunk_q = None
            self._loop_thread = None

        if self._recorder is not None:
            self._recorder.set_frame_tap(None)
        self._window = None

        if chunk_q is not None:
            chunk_q.put(None)
        if loop_thread is not None:
            loop_thread.join(timeout=self._idle_timeout_s + _JOIN_MARGIN_S)
            if loop_thread.is_alive():
                logger.warning(
                    "dictation: ws-loop thread did not stop within %.0fs "
                    "during cancel",
                    self._idle_timeout_s + _JOIN_MARGIN_S,
                )
        with self._agreement_lock:
            self._confirmed = []
            self._agreement = LocalAgreement()

        if was_active:
            self._bus.publish("dictation.end", {"reason": "cancel"})
            logger.info("dictation: cancelled")

    # --- asyncio-loop thread internals ---

    def _run_asyncio(self, chunk_q: "queue.Queue[bytes | None]", prompt: str) -> None:
        """Entry point for the asyncio-loop thread (one per dictation)."""
        try:
            asyncio.run(self._async_main(chunk_q, prompt))
        except OSError as exc:
            logger.error("dictation: WebSocket connection failed — %s", exc)
            self.error = "endpoint"
        except Exception:  # noqa: BLE001 - surface any pipeline failure
            logger.exception("dictation: streaming pipeline error")
            self.error = "endpoint"

    async def _async_main(
        self, chunk_q: "queue.Queue[bytes | None]", prompt: str
    ) -> None:
        async_q: asyncio.Queue[bytes | None] = asyncio.Queue()
        bridge_task = asyncio.create_task(pump(chunk_q, async_q))
        try:
            # Capture stream_transcribe's return: the proxy's LLM-cleaned
            # done.text (str, possibly empty) or None when no done frame
            # arrived. Written here; read by finish() after loop_thread.join()
            # — the join is the happens-before edge (same pattern as self.error).
            self._final_text = await stream_transcribe(
                self._ws_url,
                self._language,
                async_q,
                self._on_partial,
                self._idle_timeout_s,
                prompt=prompt,
                raw_transcript_fn=self._build_raw_transcript,
            )
        finally:
            # Cancel the bridge pump. It may be blocked in
            # run_in_executor(sync_q.get); the CancelledError is scheduled but
            # cannot interrupt the executor thread — the None sentinel pushed
            # by finish()/cancel() before their join() is what actually
            # unblocks it. asyncio.run() cleanup (Python >= 3.11) then drains
            # the cancelled task and shuts down the default executor before
            # returning to _run_asyncio.
            bridge_task.cancel()

    def _on_partial(self, text: str, segments: list[dict]) -> None:
        """Fold one server partial into LocalAgreement-2. Runs on the loop thread."""
        logger.info(
            "dictation[debug]: partial recv text=%r segments=%d",
            text, len(segments),
        )
        window = self._window
        offset = window.committed_offset_s if window is not None else 0.0
        with self._agreement_lock:
            if segments:
                timed = _segments_to_timed_words(segments, offset)
                committed, end_s = self._agreement.commit(timed)
                if committed:
                    self._confirmed.extend(committed)
                if end_s is not None and window is not None:
                    window.commit(end_s)
                    # ADR 0095 fix: after a trim, the post-trim window is a
                    # fresh growing-window sub-session; the server's next
                    # hypothesis no longer starts with the committed prefix,
                    # so LA-2's index math (hypothesis[already:agreed]) becomes
                    # invalid. Reset agreement state. self._confirmed keeps the
                    # global committed transcript across resets.
                    self._agreement = LocalAgreement()
            else:
                if not self._warned_no_segments:
                    logger.warning(
                        "dictation: server omitted 'segments' — trimming falls "
                        "back to window_cap_ms time policy"
                    )
                    self._warned_no_segments = True
                timed = [
                    TimedWord(text=w, end_s=offset)
                    for w in text.split()
                ]
                committed, _ = self._agreement.commit(timed)
                if committed:
                    self._confirmed.extend(committed)
            # HUD shows all globally committed words — self._confirmed is the
            # session-level accumulator across LA-2 resets (post-trim), so it
            # is always the complete picture (ADR 0095 fix).
            hud_text = " ".join(self._confirmed)
        self._bus.publish("transcript", {"text": hud_text, "confidence": 1.0})
