"""Tests for the gating logic inside StreamingDaemon._process_utterance.

Gates (in order):
  1. word-count  — drop if fewer than min_word_count words
  2. no_speech_prob — drop if above max_no_speech_prob
  3. confidence  — on_miss if below min_confidence
  4. all-pass    — matcher.match → dispatcher.dispatch
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import numpy as np

from voice_commander.feedback import CapturingFeedbackSink
from voice_commander.transcriber import TranscriptionResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stub_heavy_imports() -> None:
    """Replace GPU-heavy / hardware modules before importing daemon."""
    for mod_name in ("silero_vad", "faster_whisper"):
        if mod_name not in sys.modules:
            sys.modules[mod_name] = MagicMock()


def _make_result(
    text: str = "copy",
    confidence: float = 0.9,
    no_speech_prob: float = 0.1,
) -> TranscriptionResult:
    return TranscriptionResult(
        text=text,
        language="en",
        duration_ms=500,
        confidence=confidence,
        no_speech_prob=no_speech_prob,
    )


def _make_daemon(
    transcription_result: TranscriptionResult,
    feedback: CapturingFeedbackSink | None = None,
    *,
    min_confidence: float = 0.3,
    min_word_count: int = 1,
    max_no_speech_prob: float = 0.6,
    tmp_path=None,
):
    _stub_heavy_imports()

    from voice_commander.daemon import StreamingDaemon

    fb = feedback or CapturingFeedbackSink()

    transcriber = MagicMock()
    transcriber.transcribe.return_value = transcription_result

    matcher = MagicMock()
    dispatcher = MagicMock()

    recorder = MagicMock()
    recorder.is_open = False

    output_dir = str(tmp_path) if tmp_path is not None else "outputs"

    daemon = StreamingDaemon(
        feedback=fb,
        recorder=recorder,
        transcriber=transcriber,
        matcher=matcher,
        dispatcher=dispatcher,
        min_confidence=min_confidence,
        min_word_count=min_word_count,
        max_no_speech_prob=max_no_speech_prob,
        output_dir=output_dir,
    )
    return daemon, transcriber, matcher, dispatcher, fb


_DUMMY_AUDIO = np.zeros(16000, dtype=np.float32)


# ---------------------------------------------------------------------------
# Gate 1: word-count
# ---------------------------------------------------------------------------


def test_word_count_gate_drops_empty(tmp_path):
    """Empty transcript (0 words) must not reach the matcher."""
    result = _make_result(text="", confidence=0.9, no_speech_prob=0.1)
    daemon, _, matcher, dispatcher, fb = _make_daemon(result, min_word_count=1, tmp_path=tmp_path)

    daemon._process_utterance(_DUMMY_AUDIO)

    matcher.match.assert_not_called()
    dispatcher.dispatch.assert_not_called()


def test_word_count_gate_passes_single_word(tmp_path):
    """A single-word transcript passes the word-count gate."""
    result = _make_result(text="copy", confidence=0.9, no_speech_prob=0.1)
    daemon, _, matcher, dispatcher, _ = _make_daemon(result, min_word_count=1, tmp_path=tmp_path)

    daemon._process_utterance(_DUMMY_AUDIO)

    matcher.match.assert_called_once()


# ---------------------------------------------------------------------------
# Gate 2: no_speech_prob
# ---------------------------------------------------------------------------


def test_no_speech_prob_gate_drops_high_prob(tmp_path):
    """no_speech_prob=0.8 > max 0.6 → transcript dropped before matcher."""
    result = _make_result(text="copy", confidence=0.9, no_speech_prob=0.8)
    daemon, _, matcher, dispatcher, _ = _make_daemon(
        result, max_no_speech_prob=0.6, tmp_path=tmp_path
    )

    daemon._process_utterance(_DUMMY_AUDIO)

    matcher.match.assert_not_called()
    dispatcher.dispatch.assert_not_called()


def test_no_speech_prob_gate_passes_low_prob(tmp_path):
    """no_speech_prob=0.3 < max 0.6 → passes to matcher."""
    result = _make_result(text="copy", confidence=0.9, no_speech_prob=0.3)
    daemon, _, matcher, dispatcher, _ = _make_daemon(
        result, max_no_speech_prob=0.6, tmp_path=tmp_path
    )

    daemon._process_utterance(_DUMMY_AUDIO)

    matcher.match.assert_called_once()


# ---------------------------------------------------------------------------
# Gate 3: confidence
# ---------------------------------------------------------------------------


def test_confidence_gate_triggers_miss(tmp_path):
    """Low confidence fires on_miss; matcher and dispatcher are not called."""
    fb = CapturingFeedbackSink()
    result = _make_result(text="copy", confidence=0.2, no_speech_prob=0.1)
    daemon, _, matcher, dispatcher, _ = _make_daemon(
        result, feedback=fb, min_confidence=0.3, tmp_path=tmp_path
    )

    daemon._process_utterance(_DUMMY_AUDIO)

    assert any(c[0] == "on_miss" for c in fb.calls), (
        "on_miss should be fired when confidence < min_confidence"
    )
    matcher.match.assert_not_called()
    dispatcher.dispatch.assert_not_called()


# ---------------------------------------------------------------------------
# All gates pass → dispatch
# ---------------------------------------------------------------------------


def test_all_gates_pass_calls_dispatch(tmp_path):
    """When confidence, no_speech_prob, and word_count are all acceptable,
    matcher.match and dispatcher.dispatch must both be called."""
    result = _make_result(text="copy", confidence=0.5, no_speech_prob=0.2)
    daemon, transcriber, matcher, dispatcher, fb = _make_daemon(
        result,
        min_confidence=0.3,
        min_word_count=1,
        max_no_speech_prob=0.6,
        tmp_path=tmp_path,
    )

    daemon._process_utterance(_DUMMY_AUDIO)

    transcriber.transcribe.assert_called_once()
    matcher.match.assert_called_once_with(result.text)
    dispatcher.dispatch.assert_called_once()

    # on_transcript must have been called (with text + confidence)
    assert any(c[0] == "on_transcript" for c in fb.calls)
    # No miss event
    assert not any(c[0] == "on_miss" for c in fb.calls)
