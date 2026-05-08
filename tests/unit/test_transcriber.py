import io
from pathlib import Path
from unittest.mock import MagicMock

# Eagerly import faster_whisper at collection time. Transcriber.load() now
# imports it lazily (so remote-mode daemons skip the heavy native deps), but
# test_gates._stub_heavy_imports installs a MagicMock under sys.modules
# ['faster_whisper'] when the real module is absent. If test_gates runs
# before this hardware fixture, the stub wins and Transcriber.load() returns
# a mocked WhisperModel — breaking the @hardware tests downstream. Loading
# the real module here keeps the stub guard a no-op for the rest of the
# session.
import faster_whisper  # noqa: F401
import httpx
import numpy as np
import pytest
import soundfile

from voice_commander.transcriber import (
    Transcriber,
    TranscriptionResult,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_loaded_transcriber() -> tuple[Transcriber, MagicMock]:
    """Return a Transcriber with a mock model already injected (bypasses load())."""
    t = Transcriber()
    mock_model = MagicMock()
    t._model = mock_model
    return t, mock_model


def _make_fake_segment(text: str = "copy", avg_logprob: float = -0.1, no_speech_prob: float = 0.05):
    seg = MagicMock()
    seg.text = text
    seg.avg_logprob = avg_logprob
    seg.no_speech_prob = no_speech_prob
    return seg


def _make_fake_info(language: str = "en", duration: float = 0.5):
    info = MagicMock()
    info.language = language
    info.duration = duration
    return info


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
# ndarray input validation tests (no GPU required)
# ---------------------------------------------------------------------------


def test_transcribe_raises_when_model_not_loaded():
    """transcribe() must raise RuntimeError if load() was never called."""
    t = Transcriber()
    audio = np.zeros(1600, dtype=np.float32)
    with pytest.raises(RuntimeError, match="load\\(\\)"):
        t.transcribe(audio)


def test_transcribe_rejects_2d_ndarray():
    """transcribe() must raise ValueError for a 2-D ndarray input."""
    t, _ = _make_loaded_transcriber()
    bad_input = np.zeros((512, 1), dtype=np.float32)
    with pytest.raises(ValueError, match="1-D"):
        t.transcribe(bad_input)


def test_transcribe_rejects_int16_ndarray():
    """transcribe() must raise ValueError when ndarray dtype is not float32."""
    t, _ = _make_loaded_transcriber()
    bad_input = np.zeros(1600, dtype=np.int16)
    with pytest.raises(ValueError, match="float32"):
        t.transcribe(bad_input)


def test_transcribe_accepts_1d_float32_ndarray():
    """transcribe() must accept a 1-D float32 ndarray without raising."""
    t, mock_model = _make_loaded_transcriber()

    seg = _make_fake_segment("copy")
    info = _make_fake_info()
    mock_model.transcribe.return_value = (iter([seg]), info)

    audio = np.zeros(1600, dtype=np.float32)
    result = t.transcribe(audio)

    assert isinstance(result, TranscriptionResult)
    assert result.text == "copy"


# ---------------------------------------------------------------------------
# ndarray transcription path — vad_filter=False verification
# ---------------------------------------------------------------------------


def test_transcribe_ndarray_uses_vad_filter_false():
    """When source is an ndarray, transcribe() must call model.transcribe with
    vad_filter=False (silero-vad already ran upstream; no double-filtering)."""
    t, mock_model = _make_loaded_transcriber()

    seg = _make_fake_segment("new tab")
    info = _make_fake_info()
    mock_model.transcribe.return_value = (iter([seg]), info)

    audio = np.zeros(3200, dtype=np.float32)
    t.transcribe(audio)

    # Inspect the keyword arguments passed to model.transcribe.
    assert mock_model.transcribe.call_count == 1
    _, kwargs = mock_model.transcribe.call_args
    assert kwargs.get("vad_filter") is False, (
        f"Expected vad_filter=False for ndarray input, got {kwargs.get('vad_filter')!r}"
    )


def test_transcribe_ndarray_passes_array_directly_to_model():
    """The ndarray itself (not a file path) must be forwarded to model.transcribe."""
    t, mock_model = _make_loaded_transcriber()

    seg = _make_fake_segment("paste")
    info = _make_fake_info()
    mock_model.transcribe.return_value = (iter([seg]), info)

    audio = np.ones(1600, dtype=np.float32) * 0.5
    t.transcribe(audio)

    args, kwargs = mock_model.transcribe.call_args
    # First positional arg (or 'audio' kwarg) must be the ndarray, not a string.
    audio_arg = args[0] if args else kwargs.get("audio") or kwargs.get("input")
    assert isinstance(audio_arg, np.ndarray), (
        f"Expected ndarray forwarded to model.transcribe, got {type(audio_arg).__name__}"
    )
    np.testing.assert_array_equal(audio_arg, audio)


def test_transcribe_path_uses_vad_filter_true():
    """When source is a Path, transcribe() must use vad_filter=True."""
    t, mock_model = _make_loaded_transcriber()

    seg = _make_fake_segment("copy")
    info = _make_fake_info()
    mock_model.transcribe.return_value = (iter([seg]), info)

    t.transcribe(Path("some/audio.wav"))

    _, kwargs = mock_model.transcribe.call_args
    assert kwargs.get("vad_filter") is True, (
        f"Expected vad_filter=True for Path input, got {kwargs.get('vad_filter')!r}"
    )


def test_transcribe_ndarray_result_fields_populated():
    """TranscriptionResult returned from ndarray path has all fields populated."""
    t, mock_model = _make_loaded_transcriber()

    seg = _make_fake_segment(text="select all", avg_logprob=-0.05, no_speech_prob=0.02)
    info = _make_fake_info(language="en", duration=1.5)
    mock_model.transcribe.return_value = (iter([seg]), info)

    audio = np.zeros(24000, dtype=np.float32)
    result = t.transcribe(audio)

    assert result.text == "select all"
    assert result.language == "en"
    assert result.duration_ms == 1500
    assert 0.0 <= result.confidence <= 1.0
    assert result.no_speech_prob == pytest.approx(0.02)


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

# RemoteTranscriber tests removed in P2.4; cleanup deferred to P5.6
