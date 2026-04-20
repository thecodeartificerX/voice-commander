from pathlib import Path
from unittest.mock import MagicMock

import pytest

from voice_commander.transcriber import Transcriber, TranscriptionResult

# ---------------------------------------------------------------------------
# Non-hardware tests — no GPU required; WhisperModel is never instantiated.
# ---------------------------------------------------------------------------


def test_unload_clears_model_when_loaded():
    t = Transcriber()
    # Bypass .load() entirely; inject a sentinel
    t._model = MagicMock()
    t.unload()
    assert t._model is None


def test_unload_is_idempotent_on_never_loaded():
    t = Transcriber()
    # Must not raise
    t.unload()
    t.unload()
    assert t._model is None


# ---------------------------------------------------------------------------
# Hardware tests — require CUDA + faster-whisper model weights.
# ---------------------------------------------------------------------------

FIX = Path("tests/fixtures/audio")


@pytest.fixture(scope="session")
def transcriber() -> Transcriber:
    t = Transcriber(model_size="small.en", device="cuda", compute_type="float16")
    t.load()
    return t


@pytest.mark.hardware
def test_transcribe_recorded_audio(transcriber):
    result = transcriber.transcribe(FIX / "recorded.wav")
    assert isinstance(result, TranscriptionResult)
    assert "going" in result.text.lower()
    assert "somewhere" in result.text.lower()
    assert result.language == "en"
    assert 0.0 <= result.confidence <= 1.0
    assert result.duration_ms >= 3000


@pytest.mark.hardware
def test_transcribe_silence_returns_low_confidence_or_empty(transcriber):
    result = transcriber.transcribe(FIX / "silence.wav")
    # Either empty text, or a hallucination with low confidence
    assert result.text.strip() == "" or result.confidence < 0.5
