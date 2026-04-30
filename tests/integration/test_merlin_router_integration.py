"""End-to-end integration tests for Merlin-gated verb routing."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import numpy as np

# Stub heavy deps before voice_commander imports.
for _mod in ("silero_vad", "torch", "sounddevice", "soxr"):
    sys.modules.setdefault(_mod, MagicMock())

from voice_commander.daemon import StreamingDaemon
from voice_commander.feedback import NullFeedbackSink
from voice_commander.plan import Plan, ToolCall
from voice_commander.transcriber import TranscriptionResult
from voice_commander.verb_router import VerbRouter, build_default_rules


_DUMMY_AUDIO = np.zeros(16000, dtype=np.float32)


def _fake_result(
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


def _build_test_daemon(tmp_path):
    """Construct a StreamingDaemon with mocked subsystems but a real VerbRouter."""
    verb_router = VerbRouter(build_default_rules())
    llm_router = MagicMock()
    dispatcher = MagicMock()
    feedback = NullFeedbackSink()
    transcriber = MagicMock()
    recorder = MagicMock()
    recorder.is_open = False

    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=recorder,
        transcriber=transcriber,
        llm_router=llm_router,
        dispatcher=dispatcher,
        verb_router=verb_router,
        registry=MagicMock(),
        min_confidence=0.30,
        min_word_count=1,
        max_no_speech_prob=0.6,
        output_dir=str(tmp_path / "outputs"),
    )
    return daemon, llm_router, verb_router, dispatcher, transcriber


def test_merlin_session_routes_copy_then_llm_then_copy_again(tmp_path):
    daemon, llm_router, _, dispatcher, transcriber = _build_test_daemon(tmp_path)

    # 1. Normal mode: "copy" → verb router
    transcriber.transcribe.return_value = _fake_result("copy", confidence=0.95)
    daemon._process_utterance(_DUMMY_AUDIO)
    assert daemon._merlin_mode is False
    assert dispatcher.run_plan.call_count == 1
    plan_arg = dispatcher.run_plan.call_args[0][1]
    assert plan_arg.raw_response["router"] == "verb"

    # 2. Toggle into Merlin mode
    transcriber.transcribe.return_value = _fake_result("Merlin", confidence=0.95)
    daemon._process_utterance(_DUMMY_AUDIO)
    assert daemon._merlin_mode is True
    assert dispatcher.run_plan.call_count == 1  # no new plan

    # 3. Merlin mode: "open spotify" → LLM router
    llm_router.route.return_value = Plan(
        steps=(ToolCall("open", {"target": "spotify"}),),
        raw_response={"router": "llm"},
    )
    transcriber.transcribe.return_value = _fake_result("open spotify", confidence=0.95)
    daemon._process_utterance(_DUMMY_AUDIO)
    assert daemon._merlin_mode is True
    llm_router.route.assert_called_once_with("open spotify")
    assert dispatcher.run_plan.call_count == 2
    plan_arg = dispatcher.run_plan.call_args[0][1]
    assert plan_arg.raw_response["router"] == "llm"

    # 4. Toggle out of Merlin mode
    transcriber.transcribe.return_value = _fake_result("merlin", confidence=0.95)
    daemon._process_utterance(_DUMMY_AUDIO)
    assert daemon._merlin_mode is False
    assert dispatcher.run_plan.call_count == 2  # no new plan

    # 5. Normal mode again: "copy" → verb router
    transcriber.transcribe.return_value = _fake_result("copy", confidence=0.95)
    daemon._process_utterance(_DUMMY_AUDIO)
    assert daemon._merlin_mode is False
    assert dispatcher.run_plan.call_count == 3
    plan_arg = dispatcher.run_plan.call_args[0][1]
    assert plan_arg.raw_response["router"] == "verb"


def test_merlin_toggle_with_extra_punctuation(tmp_path):
    """."Merlin!" (with punctuation) still toggles."""
    daemon, _, _, dispatcher, transcriber = _build_test_daemon(tmp_path)

    transcriber.transcribe.return_value = _fake_result("Merlin!", confidence=0.95)
    daemon._process_utterance(_DUMMY_AUDIO)
    assert daemon._merlin_mode is True
    assert dispatcher.run_plan.call_count == 0


def test_verb_router_miss_does_not_fall_back_to_llm(tmp_path):
    """In normal mode, an unknown transcript is a miss — LLM is NOT consulted."""
    daemon, llm_router, _, dispatcher, transcriber = _build_test_daemon(tmp_path)

    transcriber.transcribe.return_value = _fake_result("gobbledygook", confidence=0.95)
    daemon._process_utterance(_DUMMY_AUDIO)

    assert daemon._merlin_mode is False
    llm_router.route.assert_not_called()
    dispatcher.run_plan.assert_not_called()


def test_session_close_resets_merlin_mode(tmp_path):
    """Closing the session via on_scroll_lock clears Merlin mode."""
    daemon, _, _, _, _ = _build_test_daemon(tmp_path)
    daemon._session_active = True
    daemon._merlin_mode = True
    daemon._recorder = MagicMock()
    daemon._recorder.is_open = True

    daemon.on_scroll_lock()

    assert daemon._merlin_mode is False
    assert daemon._session_active is False
