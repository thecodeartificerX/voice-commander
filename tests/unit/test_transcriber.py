import pytest
from pathlib import Path
from voice_commander.transcriber import Transcriber, TranscriptionResult

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
