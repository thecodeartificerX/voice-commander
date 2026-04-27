from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

from voice_commander.daemon import StreamingDaemon
from voice_commander.event_bus import EventBus
from voice_commander.feedback import CapturingFeedbackSink
from voice_commander.observability import Store, Tracer
from voice_commander.transcriber import TranscriptionResult


def _drain_store(store: Store) -> None:
    while not store._q.empty():
        time.sleep(0.01)
    time.sleep(0.05)


def test_process_utterance_emits_run_with_transcribe_and_llm_spans(tmp_path: Path):
    bus = EventBus()
    store = Store(tmp_path / "runs.db", keep_runs=10, queue_max=128, daemon_pid=42)
    store.start()
    tracer = Tracer(store=store, bus=bus, enabled=True)

    transcriber = MagicMock()
    transcriber.transcribe.return_value = TranscriptionResult(
        text="hello world", confidence=0.95, no_speech_prob=0.01,
        language="en", duration_ms=500,
    )
    llm_router = MagicMock()
    llm_router.route.return_value = None  # no plan → miss
    feedback = CapturingFeedbackSink()
    dispatcher = MagicMock()

    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=None,
        transcriber=transcriber,
        llm_router=llm_router,
        dispatcher=dispatcher,
        registry=None,
        min_confidence=0.0,
        min_word_count=1,
        max_no_speech_prob=1.0,
        output_dir=str(tmp_path / "out"),
        web_server=None,
        event_bus=bus,
        tracer=tracer,
        store=store,
    )

    daemon._process_utterance(np.zeros(16000, dtype=np.float32))
    _drain_store(store)

    runs = store.list_runs(limit=10)
    assert len(runs) == 1, f"Expected 1 run, got {len(runs)}"
    spans = store.get_spans(runs[0]["run_id"])
    types = {s["type"] for s in spans}
    assert "run" in types, f"Expected 'run' span, got {types}"
    assert "transcribe" in types, f"Expected 'transcribe' span, got {types}"

    store.stop()
