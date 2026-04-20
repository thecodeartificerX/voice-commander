from unittest.mock import MagicMock

from voice_commander.daemon import Phase3Daemon
from voice_commander.dispatcher import Dispatcher
from voice_commander.feedback import CapturingFeedbackSink
from voice_commander.matcher import Matcher
from voice_commander.registry import ToolEntry, ToolRegistry
from voice_commander.transcriber import TranscriptionResult


def test_full_pipeline_transcript_to_tool_fire(tmp_path):
    fired: list[int] = []

    def copy():
        fired.append(1)

    registry = ToolRegistry()
    registry.register(
        ToolEntry(
            name="copy",
            phrases=("copy", "copy that"),
            func=copy,
            module="test",
            docstring=None,
        )
    )

    feedback = CapturingFeedbackSink()
    recorder = MagicMock()
    transcriber = MagicMock()
    transcriber.transcribe.return_value = TranscriptionResult(
        text="copy that", language="en", duration_ms=500, confidence=0.95
    )
    matcher = Matcher(registry, threshold=85.0)
    dispatcher = Dispatcher(feedback)

    d = Phase3Daemon(
        feedback=feedback,
        recorder=recorder,
        transcriber=transcriber,
        registry=registry,
        matcher=matcher,
        dispatcher=dispatcher,
    )
    d.start_worker()

    fake = tmp_path / "x.wav"
    fake.write_bytes(b"")
    d._queue.put(fake)
    d._queue.put(None)
    d._worker.join(timeout=3.0)

    assert fired == [1]
    kinds = [c[0] for c in feedback.calls]
    assert "on_transcript" in kinds
    assert "on_match" in kinds


def test_min_confidence_threshold_is_configurable(tmp_path):
    """Low-confidence transcripts below min_confidence must route to on_miss."""

    def copy():
        pass

    registry = ToolRegistry()
    registry.register(
        ToolEntry(
            name="copy",
            phrases=("copy",),
            func=copy,
            module="test",
            docstring=None,
        )
    )

    feedback = CapturingFeedbackSink()
    recorder = MagicMock()
    transcriber = MagicMock()
    transcriber.transcribe.return_value = TranscriptionResult(
        text="copy", language="en", duration_ms=500, confidence=0.50
    )
    matcher = Matcher(registry, threshold=85.0)
    dispatcher = Dispatcher(feedback)

    d = Phase3Daemon(
        feedback=feedback,
        recorder=recorder,
        transcriber=transcriber,
        registry=registry,
        matcher=matcher,
        dispatcher=dispatcher,
        min_confidence=0.90,
    )
    d.start_worker()
    fake = tmp_path / "x.wav"
    fake.write_bytes(b"")
    d._queue.put(fake)
    d._queue.put(None)
    d._worker.join(timeout=3.0)

    kinds = [c[0] for c in feedback.calls]
    assert "on_miss" in kinds, f"expected on_miss, got {kinds}"
    assert "on_match" not in kinds
