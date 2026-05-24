"""DictationSession — daemon sub-state that streams dictation to a WS server.

While dictation is active the daemon routes every VAD utterance here instead
of the VerbRouter.  For each non-end, non-cancel utterance, the raw 16 kHz
mono float32 PCM bytes are enqueued on the chunk queue; the asyncio-loop
thread drains that queue and sends each chunk as a binary WebSocket frame.

The server (ADR 0096) accumulates the raw bytes, calls Whisper once on the
final audio, LLM-cleans the result, and returns a
``{"type":"done","text":...}`` frame.  No client-side transcript assembly —
no LocalAgreement, no DictationWindow, no frame tap.

Finalisation happens via one of three exit paths (lifecycle wiring unchanged
from ADR 0086/0089/0090):

(a) **End-word path** — the pipeline thread recognises the configured end word
    (default ``"done"``), calls :meth:`finish`, which pushes the end sentinel,
    joins the asyncio thread, and returns the server's ``done.text`` (or
    ``""`` if no done frame arrived).

(b) **Hotkey-end path** — the hotkey thread calls :meth:`request_end`, which
    sets :attr:`pending_end` without deactivating the session.  The pipeline
    thread later drains in-flight utterances and calls :meth:`finish`.

(c) **Spoken-cancel path** — :meth:`handle_utterance` recognises the
    configured cancel word and returns ``"cancel"``; the pipeline thread calls
    :meth:`cancel`, which closes the WebSocket and discards the transcript.

Threading: ``_final_text``, ``_final_timings``, and ``_roundtrip_ms`` are
written by the asyncio-loop thread (via ``_async_main``) and read by
:meth:`finish` / :meth:`get_timings` after ``loop_thread.join()``; the join
provides the happens-before edge so no lock is needed.

A connect failure inside the asyncio thread is recorded on :attr:`error`;
:meth:`finish` then returns ``""`` and the daemon emits ``dictation.error``
when ``error`` is set.

Per-phase timing observability (ADR 0101): call :meth:`get_timings` after
:meth:`finish` to retrieve server-reported latencies and client roundtrip_ms.
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
from .vocab import Vocabulary
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


class DictationSession:
    """Streams one dictation to the WebSocket server; owns the transport.

    Audio path (ADR 0096 D6): :meth:`handle_utterance` converts each
    non-classification utterance's audio ndarray to raw float32 bytes and
    places them on the chunk queue.  The asyncio thread forwards them as
    binary WebSocket frames.  The server accumulates the bytes and decodes
    once on receipt of the ``{"type":"end"}`` frame.
    """

    def __init__(
        self,
        bus: _BusLike,
        ws_url: str,
        idle_timeout_s: float = 30.0,
        end_word: str = "done",
        cancel_word: str = "cancel",
        max_dictation_s: float = 300.0,
    ) -> None:
        self._bus = bus
        self._ws_url = ws_url
        self._idle_timeout_s = idle_timeout_s
        self._max_dictation_s = max_dictation_s
        self._end_word = _normalize_spoken(end_word)
        self._lock = threading.Lock()
        self._active = False
        self._pending_end = threading.Event()

        # --- streaming transport state (recreated per start()) ---
        self._chunk_q: queue.Queue[bytes | None] | None = None
        self._loop_thread: threading.Thread | None = None
        self._vocab: Vocabulary = Vocabulary()
        # Written by the asyncio-loop thread (_run_asyncio -> "endpoint"). No lock:
        # writers finish before finish() returns and the daemon reads this field;
        # CPython attribute assignment is GIL-atomic.
        self.error: str | None = None
        # Written by the asyncio-loop thread (_async_main -> stream_transcribe
        # return value); read by finish() after loop_thread.join() — the join
        # provides the happens-before edge; no lock needed.  None means no done
        # frame arrived; str (possibly empty) is the server's transcript (ADR 0094).
        self._final_text: str | None = None
        # Per-phase timing fields from stream_transcribe (ADR 0101).
        # Written by the asyncio-loop thread; read after loop_thread.join().
        self._final_timings: dict[str, float] | None = None
        self._roundtrip_ms: float | None = None

        # Validate cancel_word against end_word and emptiness.
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

        Called from the hotkey thread.  Does NOT deactivate the session — the
        pipeline thread polls :attr:`pending_end` and calls :meth:`finish`
        after draining in-flight utterances.  A no-op when already inactive.
        """
        with self._lock:
            if not self._active:
                return
            self._pending_end.set()
        logger.debug("dictation: pending_end requested")

    def start(self, vocab: Vocabulary) -> None:
        """Enter dictation mode and open the streaming WebSocket transport.

        *vocab* is the daemon's start-time snapshot of ``vocab.json``; it is
        stored so :meth:`vocab` exposes it to the daemon for post-processing.
        The asyncio thread is spawned here; the WebSocket connect happens
        inside it, so a connect failure is reported asynchronously via
        :attr:`error`.
        """
        with self._lock:
            if self._active:
                return
            # All per-session state is reset under the lock and BEFORE
            # _active flips to True, so a concurrent handle_utterance can
            # never observe _active==True with a stale/None _chunk_q.
            self._vocab = vocab
            self.error = None
            self._final_text = None
            self._final_timings = None
            self._roundtrip_ms = None
            self._chunk_q = queue.Queue()
            self._pending_end.clear()
            self._active = True
        self._loop_thread = threading.Thread(
            target=self._run_asyncio,
            args=(self._chunk_q,),
            name="dictation-ws-loop",
            daemon=True,
        )
        self._loop_thread.start()
        self._bus.publish("dictation.start", {})
        logger.info("dictation: started (raw-PCM streaming)")

    @property
    def vocab(self) -> Vocabulary:
        """The start-time vocab snapshot — used by the daemon for post-processing."""
        return self._vocab

    def handle_utterance(
        self, audio: npt.NDArray[np.float32], text: str
    ) -> UtteranceKind:
        """Classify an utterance and (for buffered) enqueue its PCM bytes.

        Returns ``"end"`` when *text* matches the end word, ``"cancel"`` when
        it matches the cancel word, or ``"buffered"`` for all other content.
        For buffered utterances the raw float32 PCM bytes are pushed onto the
        chunk queue so the asyncio thread forwards them as binary WS frames
        (ADR 0096 D6).

        Parameters
        ----------
        audio:
            16 kHz mono float32 ndarray from the VAD pipeline.
        text:
            Whisper transcript of this utterance.
        """
        with self._lock:
            if not self._active:
                return "buffered"
            normalized = _normalize_spoken(text)
            if normalized == self._end_word:
                return "end"
            if self._cancel_word is not None and normalized == self._cancel_word:
                return "cancel"
            chunk_q = self._chunk_q
        # Active, non-end, non-cancel: stream the PCM bytes.
        if chunk_q is not None:
            chunk_q.put(audio.astype(np.float32).tobytes())
        return "buffered"

    def finish(self) -> str:
        """End dictation normally; return the transcript.

        Pushes the ``None`` end sentinel onto the chunk queue, joins the
        asyncio-loop thread (bounded by ``idle_timeout_s + _JOIN_MARGIN_S``),
        and returns the server's ``done.text`` (str, possibly ``""``).

        Returns ``""`` when the WebSocket never connected or the server
        returned no done frame (timeout, error, or connection closed).
        Publishes ``dictation.end {reason: "done"}``.  Idempotent — a no-op
        returning ``""`` when the session is already inactive.
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

        if chunk_q is not None:
            chunk_q.put(None)  # end sentinel — bridge.pump forwards it

        # Signal the sprite that audio has been captured and the daemon is now
        # waiting on the server's Whisper + LLM round-trip (ADR 0096 D5).
        # Published AFTER the end sentinel is queued (so recording stops
        # promptly) and BEFORE loop_thread.join() (so the sprite shows the
        # processing badge during the server wait window, not after it).
        # Cancel path does NOT publish this event — it calls cancel() instead
        # of finish(), and the cancel badge is the only feedback on that path.
        self._bus.publish("dictation.processing", {})

        if loop_thread is not None:
            loop_thread.join(timeout=self._idle_timeout_s + _JOIN_MARGIN_S)
            if loop_thread.is_alive():
                logger.warning(
                    "dictation: ws-loop thread did not stop within %.0fs — "
                    "transcript may be incomplete",
                    self._idle_timeout_s + _JOIN_MARGIN_S,
                )

        # _final_text is set by the asyncio-loop thread (join above provides
        # the happens-before edge).
        if self._final_text is not None:
            text = self._final_text
            logger.debug("dictation: using server done.text (%d chars)", len(text))
        else:
            text = ""
            logger.debug("dictation: no done frame received — returning empty string")

        self._bus.publish("dictation.end", {"reason": "done"})
        logger.info("dictation: finished (raw-PCM streaming) — %d chars", len(text))
        return text

    def get_timings(self) -> dict:
        """Return per-phase timing data captured by the asyncio-loop thread (ADR 0101).

        Must be called AFTER :meth:`finish` — the ``loop_thread.join()`` inside
        ``finish()`` is the happens-before barrier that guarantees
        ``_final_timings`` and ``_roundtrip_ms`` are visible to the caller
        (same pattern as ``_final_text``).

        Returns
        -------
        dict
            ``{"server": dict[str, float], "roundtrip_ms": float | None}``

            ``server`` is the server's optional ``timings`` dict from the done
            frame (keys: ``transcribe_ms``, ``clean_ms``, ``format_ms``,
            ``server_total_ms``); empty dict ``{}`` when the server did not
            include timing data (older servers or no done frame).

            ``roundtrip_ms`` is the client-side wall time in milliseconds from
            end-frame-sent to done-frame-received, or ``None`` when no done
            frame arrived.
        """
        return {
            "server": self._final_timings or {},
            "roundtrip_ms": self._roundtrip_ms,
        }

    def cancel(self) -> None:
        """Abort dictation; close the WebSocket and discard the transcript.

        Pushes the end sentinel so the asyncio thread unwinds, joins it with
        a bounded timeout, and publishes ``dictation.end {reason: "cancel"}``.
        A no-op (no event) when the session was already inactive.
        """
        with self._lock:
            was_active = self._active
            self._active = False
            self._pending_end.clear()
            chunk_q = self._chunk_q
            loop_thread = self._loop_thread
            self._chunk_q = None
            self._loop_thread = None

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

        if was_active:
            self._bus.publish("dictation.end", {"reason": "cancel"})
            logger.info("dictation: cancelled")

    # --- asyncio-loop thread internals ---

    def _run_asyncio(self, chunk_q: "queue.Queue[bytes | None]") -> None:
        """Entry point for the asyncio-loop thread (one per dictation)."""
        try:
            asyncio.run(self._async_main(chunk_q))
        except OSError as exc:
            logger.error("dictation: WebSocket connection failed — %s", exc)
            self.error = "endpoint"
        except Exception:  # noqa: BLE001 - surface any pipeline failure
            logger.exception("dictation: streaming pipeline error")
            self.error = "endpoint"

    async def _async_main(self, chunk_q: "queue.Queue[bytes | None]") -> None:
        async_q: asyncio.Queue[bytes | None] = asyncio.Queue()
        bridge_task = asyncio.create_task(pump(chunk_q, async_q))
        try:
            # Capture stream_transcribe's TranscribeResult (or None on failure).
            # Written here; read by finish() / get_timings() after
            # loop_thread.join() — the join is the happens-before edge (same
            # pattern as self.error).
            result = await stream_transcribe(
                self._ws_url,
                async_q,
                cap_timeout_s=self._max_dictation_s,
            )
            if result is not None:
                self._final_text = result.text
                self._final_timings = result.server_timings
                self._roundtrip_ms = result.roundtrip_ms
        finally:
            # Cancel the bridge pump.  asyncio.run() cleanup (Python >= 3.11)
            # drains the cancelled task and shuts down the default executor
            # before returning to _run_asyncio.
            bridge_task.cancel()
