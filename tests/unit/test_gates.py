"""Tests for the gating logic inside StreamingDaemon._process_utterance.

Gates (in order):
  1. word-count  — drop if fewer than min_word_count words
  2. no_speech_prob — drop if above max_no_speech_prob
  3. confidence  — on_miss if below min_confidence
  4. all-pass    — llm_router.route → (None → on_miss | Plan → dispatcher.run_plan)
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import numpy as np

from voice_commander.feedback import CapturingFeedbackSink
from voice_commander.plan import Plan, ToolCall
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

    llm_router = MagicMock()
    # Default: router returns a plan so dispatch proceeds when all gates pass.
    _default_plan = Plan(
        steps=(ToolCall(name="press", kwargs={"combo": "ctrl+c"}),), raw_response={}
    )
    llm_router.route.return_value = _default_plan

    dispatcher = MagicMock()

    recorder = MagicMock()
    recorder.is_open = False

    from voice_commander.verb_router import VerbRouter
    verb_router = MagicMock(spec=VerbRouter)
    verb_router.route.return_value = _default_plan

    output_dir = str(tmp_path) if tmp_path is not None else "outputs"

    daemon = StreamingDaemon(
        feedback=fb,
        recorder=recorder,
        transcriber=transcriber,
        llm_router=llm_router,
        dispatcher=dispatcher,
        verb_router=verb_router,
        registry=MagicMock(),
        min_confidence=min_confidence,
        min_word_count=min_word_count,
        max_no_speech_prob=max_no_speech_prob,
        output_dir=output_dir,
    )
    return daemon, transcriber, llm_router, dispatcher, fb, verb_router


_DUMMY_AUDIO = np.zeros(16000, dtype=np.float32)


# ---------------------------------------------------------------------------
# Gate 1: word-count
# ---------------------------------------------------------------------------


def test_word_count_gate_drops_empty(tmp_path):
    """Empty transcript (0 words) must not reach any router."""
    result = _make_result(text="", confidence=0.9, no_speech_prob=0.1)
    daemon, _, llm_router, dispatcher, fb, verb_router = _make_daemon(
        result, min_word_count=1, tmp_path=tmp_path
    )

    daemon._process_utterance(_DUMMY_AUDIO)

    llm_router.route.assert_not_called()
    verb_router.route.assert_not_called()
    dispatcher.run_plan.assert_not_called()


def test_word_count_gate_passes_single_word(tmp_path):
    """A single-word transcript passes the word-count gate."""
    result = _make_result(text="copy", confidence=0.9, no_speech_prob=0.1)
    daemon, _, llm_router, dispatcher, _, verb_router = _make_daemon(
        result, min_word_count=1, tmp_path=tmp_path
    )

    daemon._process_utterance(_DUMMY_AUDIO)

    verb_router.route.assert_called_once()
    llm_router.route.assert_not_called()


# ---------------------------------------------------------------------------
# Gate 2: no_speech_prob
# ---------------------------------------------------------------------------


def test_no_speech_prob_gate_drops_high_prob(tmp_path):
    """no_speech_prob=0.8 > max 0.6 → transcript dropped before any router."""
    result = _make_result(text="copy", confidence=0.9, no_speech_prob=0.8)
    daemon, _, llm_router, dispatcher, _, verb_router = _make_daemon(
        result, max_no_speech_prob=0.6, tmp_path=tmp_path
    )

    daemon._process_utterance(_DUMMY_AUDIO)

    llm_router.route.assert_not_called()
    verb_router.route.assert_not_called()
    dispatcher.run_plan.assert_not_called()


def test_no_speech_prob_gate_passes_low_prob(tmp_path):
    """no_speech_prob=0.3 < max 0.6 → passes to verb router."""
    result = _make_result(text="copy", confidence=0.9, no_speech_prob=0.3)
    daemon, _, llm_router, dispatcher, _, verb_router = _make_daemon(
        result, max_no_speech_prob=0.6, tmp_path=tmp_path
    )

    daemon._process_utterance(_DUMMY_AUDIO)

    verb_router.route.assert_called_once()
    llm_router.route.assert_not_called()


# ---------------------------------------------------------------------------
# Gate 3: confidence
# ---------------------------------------------------------------------------


def test_confidence_gate_triggers_miss(tmp_path):
    """Low confidence fires on_miss; routers and dispatcher are not called."""
    fb = CapturingFeedbackSink()
    result = _make_result(text="copy", confidence=0.2, no_speech_prob=0.1)
    daemon, _, llm_router, dispatcher, _, verb_router = _make_daemon(
        result, feedback=fb, min_confidence=0.3, tmp_path=tmp_path
    )

    daemon._process_utterance(_DUMMY_AUDIO)

    assert any(c[0] == "on_miss" for c in fb.calls), (
        "on_miss should be fired when confidence < min_confidence"
    )
    llm_router.route.assert_not_called()
    verb_router.route.assert_not_called()
    dispatcher.run_plan.assert_not_called()


# ---------------------------------------------------------------------------
# LLM returns None → on_miss
# ---------------------------------------------------------------------------


def test_llm_returns_none_triggers_miss(tmp_path):
    """When llm_router.route() returns None in Merlin mode, the daemon pipeline
    fires on_miss and short-circuits before ever calling dispatcher.run_plan."""
    fb = CapturingFeedbackSink()
    result = _make_result(text="copy", confidence=0.9, no_speech_prob=0.1)

    _stub_heavy_imports()
    from voice_commander.daemon import StreamingDaemon

    llm_router = MagicMock()
    llm_router.route.return_value = None

    transcriber = MagicMock()
    transcriber.transcribe.return_value = result

    dispatcher = MagicMock()
    recorder = MagicMock()
    recorder.is_open = False

    from voice_commander.verb_router import VerbRouter
    verb_router = MagicMock(spec=VerbRouter)

    daemon = StreamingDaemon(
        feedback=fb,
        recorder=recorder,
        transcriber=transcriber,
        llm_router=llm_router,
        dispatcher=dispatcher,
        verb_router=verb_router,
        registry=MagicMock(),
        min_confidence=0.3,
        min_word_count=1,
        max_no_speech_prob=0.6,
        output_dir=str(tmp_path),
    )
    daemon._merlin_mode = True  # force LLM path

    daemon._process_utterance(_DUMMY_AUDIO)

    # LLM was consulted
    llm_router.route.assert_called_once_with("copy")
    verb_router.route.assert_not_called()
    # on_miss fired (by daemon pipeline)
    miss_calls = [c for c in fb.calls if c[0] == "on_miss"]
    assert len(miss_calls) == 1, (
        f"Expected exactly one on_miss event, got {len(miss_calls)}: {fb.calls}"
    )
    # Dispatcher was NOT reached
    dispatcher.run_plan.assert_not_called()


# ---------------------------------------------------------------------------
# All gates pass → dispatch
# ---------------------------------------------------------------------------


def test_all_gates_pass_calls_dispatch(tmp_path):
    """When confidence, no_speech_prob, and word_count are all acceptable,
    verb_router.route and dispatcher.run_plan must both be called."""
    result = _make_result(text="copy", confidence=0.5, no_speech_prob=0.2)
    daemon, transcriber, llm_router, dispatcher, fb, verb_router = _make_daemon(
        result,
        min_confidence=0.3,
        min_word_count=1,
        max_no_speech_prob=0.6,
        tmp_path=tmp_path,
    )

    daemon._process_utterance(_DUMMY_AUDIO)

    transcriber.transcribe.assert_called_once()
    verb_router.route.assert_called_once_with(result.text)
    llm_router.route.assert_not_called()
    dispatcher.run_plan.assert_called_once()

    # on_transcript must have been called (with text + confidence)
    assert any(c[0] == "on_transcript" for c in fb.calls)
    # No miss event
    assert not any(c[0] == "on_miss" for c in fb.calls)
