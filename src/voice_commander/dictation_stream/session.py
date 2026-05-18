"""Streaming dictation session orchestrator.

Wires the pieces: ``MicCapture`` (device) -> raw queue -> chunker worker thread
-> chunk queue -> asyncio bridge -> ``stream_transcribe`` -> ``LocalAgreement``
-> ``TextSink``. One ``StreamSession`` is one dictation: ``start`` opens the
mic and spawns the worker + event-loop threads, ``stop`` tears them down,
finalizes ``LocalAgreement`` and pastes the transcript.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from typing import Callable

from .bridge import pump
from .capture import MicCapture
from .chunker import Chunker
from .config import StreamDictationConfig
from .local_agreement import LocalAgreement
from .sink import TextSink
from .ws_client import stream_transcribe

logger = logging.getLogger(__name__)


class StreamSession:
    """One streaming-dictation session."""

    def __init__(
        self,
        config: StreamDictationConfig,
        *,
        capture: object | None = None,
        chunker_factory: Callable[[int], Chunker] | None = None,
        raw_q: "queue.Queue | None" = None,
    ) -> None:
        self._config = config
        self._raw_q: queue.Queue = raw_q if raw_q is not None else queue.Queue(maxsize=256)
        self._chunk_q: queue.Queue = queue.Queue()
        self._capture = capture if capture is not None else MicCapture(self._raw_q)
        self._chunker_factory = chunker_factory or self._default_chunker
        self._agreement = LocalAgreement()
        self._sink = TextSink()
        self._worker: threading.Thread | None = None
        self._loop_thread: threading.Thread | None = None
        self._running = False

    def _default_chunker(self, native_rate: int) -> Chunker:
        return Chunker(
            native_rate,
            vad_threshold=self._config.vad_threshold,
            min_silence_ms=self._config.min_silence_duration_ms,
            speech_pad_ms=self._config.speech_pad_ms,
            max_chunk_seconds=self._config.max_chunk_seconds,
            min_chunk_seconds=self._config.min_chunk_seconds,
        )

    def start(self) -> None:
        """Open the mic and start the chunker + event-loop threads."""
        if self._running:
            return
        self._capture.start()
        chunker = self._chunker_factory(self._capture.native_rate)
        self._running = True
        self._worker = threading.Thread(
            target=self._chunker_loop, args=(chunker,),
            name="dict-stream-chunker", daemon=True,
        )
        self._worker.start()
        self._loop_thread = threading.Thread(
            target=self._run_asyncio, name="dict-stream-loop", daemon=True,
        )
        self._loop_thread.start()
        logger.info("StreamSession: started")

    def stop(self) -> str:
        """Stop capture, drain, finalize LocalAgreement, paste. Returns text."""
        if not self._running:
            return ""
        self._running = False
        self._capture.stop()  # pushes the None sentinel onto raw_q
        if self._worker is not None:
            self._worker.join(timeout=10.0)
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=self._config.idle_timeout_seconds + 10.0)
        self._sink.accumulate(self._agreement.finalize())
        pasted = self._sink.flush()
        logger.info("StreamSession: stopped")
        return pasted

    def _chunker_loop(self, chunker: Chunker) -> None:
        """Drain raw audio, chunk it, feed chunk_q. Ends on the None sentinel."""
        while True:
            raw = self._raw_q.get()
            if raw is None:
                for chunk in chunker.flush():
                    self._chunk_q.put(chunk)
                self._chunk_q.put(None)
                return
            for chunk in chunker.process_raw(raw):
                self._chunk_q.put(chunk)

    def _run_asyncio(self) -> None:
        try:
            asyncio.run(self._async_main())
        except OSError as exc:
            logger.error("StreamSession: WebSocket connection failed — %s", exc)
        except Exception:  # noqa: BLE001 - surface any pipeline failure
            logger.exception("StreamSession: pipeline error")

    async def _async_main(self) -> None:
        async_q: asyncio.Queue = asyncio.Queue()
        bridge_task = asyncio.create_task(pump(self._chunk_q, async_q))
        try:
            await stream_transcribe(
                self._config.ws_url,
                self._config.language,
                async_q,
                self._on_partial,
                float(self._config.idle_timeout_seconds),
            )
        finally:
            bridge_task.cancel()

    def _on_partial(self, text: str) -> None:
        """Runs on the loop thread; stop() joins that thread before reading."""
        self._sink.accumulate(self._agreement.commit(text))
