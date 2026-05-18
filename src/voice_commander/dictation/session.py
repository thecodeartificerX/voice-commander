"""DictationSession — daemon sub-state that streams dictation to a WS server.

While dictation is active the daemon routes every VAD utterance here instead
of the VerbRouter. Each non-end utterance is encoded to a WAV chunk and pushed
onto a synchronous chunk queue; an asyncio-loop thread drains that queue and
streams the chunks to the ``/ws/transcribe`` server. The server's ``partial``
replies are folded through :class:`~voice_commander.dictation.local_agreement.LocalAgreement`
under a lock, accumulating the confirmed-word transcript.

Finalisation happens via one of three exit paths (lifecycle wiring unchanged
from ADR 0086/0089/0090 — only the session internals changed, ADR 0092):

(a) **End-word path** — the pipeline thread recognises the configured end word
    (default ``"done"``), calls :meth:`finish`, which pushes the end sentinel,
    joins the asyncio thread, finalises ``LocalAgreement``, and returns the
    stabilised transcript.

(b) **Hotkey-end path** — the hotkey thread calls :meth:`request_end`, which
    sets :attr:`pending_end` without deactivating the session. The pipeline
    thread later drains in-flight utterances and calls :meth:`finish`.

(c) **Spoken-cancel path** — :meth:`handle_utterance` recognises the configured
    cancel word and returns ``"cancel"``; the pipeline thread calls
    :meth:`cancel`, which closes the WebSocket and discards the transcript.

Threading: the confirmed-word accumulator + ``LocalAgreement`` are touched by
the asyncio-loop thread (via ``_on_partial``) and by :meth:`finish` (the
``_dictation_executor`` thread). A single lock (``_agreement_lock``)
serialises them — the ADR 0091 Task-9 pattern.

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
from .local_agreement import LocalAgreement
from .postprocess import build_prompt
from .store import encode_wav
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
    """Streams one dictation to the WebSocket server; owns the transport."""

    def __init__(
        self,
        bus: _BusLike,
        ws_url: str,
        language: str = "en",
        idle_timeout_s: float = 30.0,
        end_word: str = "done",
        cancel_word: str = "cancel",
    ) -> None:
        self._bus = bus
        self._ws_url = ws_url
        self._language = language
        self._idle_timeout_s = idle_timeout_s
        self._end_word = _normalize_spoken(end_word)
        self._lock = threading.Lock()
        self._active = False
        self._pending_end = threading.Event()

        # --- streaming transport state (recreated per start()) ---
        self._chunk_q: queue.Queue[bytes | None] | None = None
        self._loop_thread: threading.Thread | None = None
        self._agreement = LocalAgreement()
        self._agreement_lock = threading.Lock()
        self._confirmed: list[str] = []
        self._vocab: Vocabulary = Vocabulary()
        self.error: str | None = None

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
            self._active = True
            self._pending_end.clear()
        self._agreement = LocalAgreement()
        self._confirmed = []
        self._vocab = vocab
        self.error = None
        self._chunk_q = queue.Queue()
        prompt = build_prompt(vocab)
        self._loop_thread = threading.Thread(
            target=self._run_asyncio,
            args=(self._chunk_q, prompt),
            name="dictation-ws-loop",
            daemon=True,
        )
        self._loop_thread.start()
        self._bus.publish("dictation.start", {})
        logger.info("dictation: started (streaming)")

    @property
    def vocab(self) -> Vocabulary:
        """The start-time vocab snapshot — used by the daemon for post-processing."""
        return self._vocab

    def handle_utterance(
        self, audio: npt.NDArray[np.float32], text: str
    ) -> UtteranceKind:
        """Classify an utterance: ``"end"``, ``"cancel"``, or ``"buffered"``.

        * ``"end"`` — transcript is the end word (exact normalized match).
          NOT streamed; caller MUST call :meth:`finish`.
        * ``"cancel"`` — transcript is the cancel word (exact normalized match,
          when ``_cancel_word`` is not ``None``). NOT streamed; caller MUST
          call :meth:`cancel`.
        * ``"buffered"`` — audio is encoded to a WAV chunk and pushed onto the
          chunk queue (streamed immediately); session stays active. The name
          ``"buffered"`` is kept for wire-compatibility with the daemon's
          existing ``kind ==`` branches — no audio is actually buffered.

        A no-op returning ``"buffered"`` when inactive (lost race with
        finish/cancel).
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

        # encode_wav + queue push happen outside the lock — encoding is pure
        # CPU work and the queue is unbounded, so neither can deadlock.
        if chunk_q is None:
            return "buffered"
        try:
            wav = encode_wav(audio)
        except Exception:
            logger.exception("dictation: encode_wav failed for an utterance chunk")
            self.error = "encode"
            return "buffered"
        chunk_q.put(wav)
        return "buffered"

    def finish(self) -> str:
        """End dictation normally; return the raw stabilised transcript.

        Pushes the ``None`` end sentinel, joins the asyncio-loop thread (bounded
        by ``idle_timeout_s + _JOIN_MARGIN_S``), finalises ``LocalAgreement``,
        and returns the confirmed-word transcript. Returns ``""`` when the
        WebSocket never connected or produced nothing. Publishes
        ``dictation.end {reason: "done"}``. Idempotent — a no-op returning
        ``""`` when the session is already inactive (lost race).
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
        if loop_thread is not None:
            loop_thread.join(timeout=self._idle_timeout_s + _JOIN_MARGIN_S)
            if loop_thread.is_alive():
                logger.warning(
                    "dictation: ws-loop thread did not stop within %.0fs — "
                    "transcript may be incomplete",
                    self._idle_timeout_s + _JOIN_MARGIN_S,
                )

        with self._agreement_lock:
            self._confirmed.extend(self._agreement.finalize())
            text = " ".join(self._confirmed)

        self._bus.publish("dictation.end", {"reason": "done"})
        logger.info("dictation: finished (streaming) — %d chars", len(text))
        return text

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

        if chunk_q is not None:
            chunk_q.put(None)
        if loop_thread is not None:
            loop_thread.join(timeout=self._idle_timeout_s + _JOIN_MARGIN_S)
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
            await stream_transcribe(
                self._ws_url,
                self._language,
                async_q,
                self._on_partial,
                self._idle_timeout_s,
                prompt=prompt,
            )
        finally:
            bridge_task.cancel()

    def _on_partial(self, text: str) -> None:
        """Fold one server partial into the agreement. Runs on the loop thread.

        Holds ``_agreement_lock`` so it can never race :meth:`finish`'s
        ``finalize`` even if the loop-thread join times out.
        """
        with self._agreement_lock:
            self._confirmed.extend(self._agreement.commit(text))
