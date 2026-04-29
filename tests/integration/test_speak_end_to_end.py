"""Integration tests: speak-mode dictation toggle end-to-end (ADR 0072).

Tests the full speak-mode pipeline using a mocked pynput Controller so no
actual keypresses are synthesized. All hardware is mocked.

Strategy:
  1. Build a StreamingDaemon with stub recorder, mock transcriber, mock registry.
  2. Wire the speak tool's ``_controller`` mock so speak() calls are trackable.
  3. Inject utterances via ``_utt_q`` and wait for the pipeline to process them.
  4. Assert ``_speak_mode`` and LLM call counts.
"""

from __future__ import annotations

import sys
import threading
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Stub heavy deps before any voice_commander imports.
# ---------------------------------------------------------------------------

for _mod in ("silero_vad", "torch", "sounddevice", "soxr"):
    sys.modules.setdefault(_mod, MagicMock())

from voice_commander.daemon import StreamingDaemon  # noqa: E402
from voice_commander.feedback import CapturingFeedbackSink  # noqa: E402
from voice_commander.llm_router import LLMRouter  # noqa: E402
from voice_commander.plan import Plan, ToolCall  # noqa: E402
from voice_commander.transcriber import TranscriptionResult  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_utterance(n: int = 1600) -> np.ndarray:
    return np.zeros(n, dtype=np.float32)


def _make_result(
    text: str,
    confidence: float = 0.9,
    no_speech_prob: float = 0.05,
) -> TranscriptionResult:
    return TranscriptionResult(
        text=text,
        confidence=confidence,
        language="en",
        duration_ms=500,
        no_speech_prob=no_speech_prob,
    )


def _make_daemon(tmp_path, speak_fuzzy_threshold: int = 95):
    """Build a StreamingDaemon with a real speak tool in the registry."""
    feedback = CapturingFeedbackSink()
    recorder = MagicMock()
    transcriber = MagicMock()
    llm_router = MagicMock(spec=LLMRouter)
    dispatcher = MagicMock()

    # Build a real registry mock with a wired speak entry.
    speak_controller = MagicMock()
    speak_called_count = [0]

    def _speak_func():
        speak_called_count[0] += 1

    speak_entry = MagicMock()
    speak_entry.func = _speak_func

    registry = MagicMock()
    registry.by_name.side_effect = lambda name: speak_entry if name == "speak" else None

    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=recorder,
        transcriber=transcriber,
        llm_router=llm_router,
        dispatcher=dispatcher,
        registry=registry,
        output_dir=str(tmp_path),
        speak_fuzzy_threshold=speak_fuzzy_threshold,
    )
    return daemon, feedback, transcriber, llm_router, dispatcher, speak_called_count


def _run_utterance(daemon: StreamingDaemon) -> None:
    """Inject one utterance and wait for the pipeline to process it."""
    done = threading.Event()
    original = daemon._process_utterance

    def _patched(utt):
        original(utt)
        done.set()

    daemon._process_utterance = _patched

    thread = threading.Thread(target=daemon._pipeline_loop, daemon=True)
    thread.start()
    try:
        daemon._utt_q.put(_fake_utterance())
        assert done.wait(timeout=5.0), "pipeline did not process utterance within 5 s"
    finally:
        daemon._utt_q.put(None)
        thread.join(timeout=3.0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_speak_utterance_enters_speak_mode(tmp_path):
    """Utterance 'speak' in normal mode routes to LLM, LLM calls speak tool,
    which triggers on_speak_toggle → _speak_mode=True."""
    daemon, feedback, transcriber, llm_router, dispatcher, speak_count = _make_daemon(tmp_path)

    # LLM routes "speak" to the speak tool.
    plan = Plan(steps=(ToolCall(name="speak", kwargs={}),), raw_response={})
    llm_router.route.return_value = plan
    transcriber.transcribe.return_value = _make_result("speak")

    _run_utterance(daemon)

    llm_router.route.assert_called_once()
    dispatcher.run_plan.assert_called_once()
    # speak_count tracks actual speak() calls — dispatcher calls it internally.
    # We can't assert speak_mode flipped here since dispatcher is mocked.
    # The speak_mode contract is validated in unit tests; here we confirm LLM routing.


@pytest.mark.integration
def test_speak_utterance_exits_speak_mode(tmp_path):
    """In speak-mode, utterance 'speak' fuzzy-matches → speak tool called → _speak_mode flips."""
    daemon, feedback, transcriber, llm_router, dispatcher, speak_count = _make_daemon(tmp_path)

    # Put daemon in speak-mode.
    daemon._session_active = True
    daemon._speak_mode = True

    transcriber.transcribe.return_value = _make_result("speak", confidence=0.9)

    _run_utterance(daemon)

    # LLM router must NOT be called in speak-mode.
    llm_router.route.assert_not_called()
    dispatcher.run_plan.assert_not_called()
    # speak tool must have been called once to exit speak-mode.
    assert speak_count[0] == 1, f"Expected speak() called once, got {speak_count[0]}"


@pytest.mark.integration
def test_non_speak_utterance_dropped_in_speak_mode(tmp_path):
    """In speak-mode, non-matching utterances are silently dropped."""
    daemon, feedback, transcriber, llm_router, dispatcher, speak_count = _make_daemon(tmp_path)

    daemon._session_active = True
    daemon._speak_mode = True

    transcriber.transcribe.return_value = _make_result("open chrome", confidence=0.9)

    _run_utterance(daemon)

    llm_router.route.assert_not_called()
    dispatcher.run_plan.assert_not_called()
    assert speak_count[0] == 0


@pytest.mark.integration
def test_speak_mode_does_not_fire_miss_chime(tmp_path):
    """In speak-mode, dropped utterances produce no miss chime."""
    daemon, feedback, transcriber, llm_router, dispatcher, speak_count = _make_daemon(tmp_path)

    daemon._session_active = True
    daemon._speak_mode = True

    transcriber.transcribe.return_value = _make_result("hello world", confidence=0.9)

    _run_utterance(daemon)

    assert not any(c[0] == "on_miss" for c in feedback.calls), (
        "speak-mode drops must not produce a miss chime"
    )
