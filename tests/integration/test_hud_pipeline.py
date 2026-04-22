"""Integration test: plan_outcome SSE event → HUDPipeline → ChatLog entry.

Tests the full wiring: on_event() parses the event, enqueues the PlanOutcome,
worker() summarizes it and appends a ChatLogEntry, ChatLog.entries() reflects it.

Pyglet clock is mocked so schedule_once(fn, 0) fires synchronously — no event
loop required.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from voice_commander.plan import PlanOutcome, ToolCall
from voice_sprite.chat_log import ChatLog
from voice_sprite.summarizer import Summarizer
from voice_sprite.summary_rules import CHAIN_DETECTORS, RULES

if TYPE_CHECKING:
    from voice_sprite.__main__ import HUDPipeline


def _make_pipeline(chat_log: ChatLog) -> HUDPipeline:
    from voice_sprite.__main__ import HUDPipeline

    sm = MagicMock()
    sm.muted = False
    sm.on_event.return_value = None  # no state transition

    renderer = MagicMock()
    window = MagicMock()
    bubble = MagicMock()

    summarizer = Summarizer(
        rules=RULES,
        chain_detectors=CHAIN_DETECTORS,
        llm_client=None,
        llm_fallback_enabled=False,
    )

    return HUDPipeline(
        sm=sm,
        renderer=renderer,
        window=window,
        bubble=bubble,
        chat_log=chat_log,
        summarizer=summarizer,
    )


def _run_worker_with_mock_clock(pipeline) -> None:
    """Run worker in a thread with mocked pyglet.clock.schedule_once firing synchronously.

    Ordering contract: call this AFTER all pipeline.on_event() calls.
    stop() enqueues the sentinel AFTER any real work items so the worker
    processes all real items before terminating (FIFO queue ordering).
    """
    import pyglet.clock as _pclock

    def immediate(fn, delay):
        fn(0)

    # Sentinel enqueued here — after on_event() has already put work items.
    # The worker drains real items first (FIFO), then sees None and exits.
    pipeline.stop()

    with patch.object(_pclock, "schedule_once", side_effect=immediate):
        t = threading.Thread(target=pipeline.worker, daemon=True)
        t.start()
        t.join(timeout=5.0)
        assert not t.is_alive(), "worker did not terminate within timeout"


def test_plan_outcome_ok_appends_hud_entry():
    """plan_outcome(status=ok) → ChatLog.entries() has one entry with correct text/status."""
    chat_log = ChatLog(max_lines=5, hold_ms=10_000, fade_ms=1_000)
    pipeline = _make_pipeline(chat_log)

    outcome = PlanOutcome(
        transcript="minimize",
        steps=(ToolCall("minimize", {}),),
        status="ok",
        failed_step_index=None,
        error_msg=None,
        duration_ms=50,
    )
    event_data = outcome.to_event_dict()
    pipeline.on_event("plan_outcome", event_data)

    _run_worker_with_mock_clock(pipeline)

    entries = chat_log.entries()
    assert len(entries) == 1
    assert entries[0].status == "ok"
    assert entries[0].text == "minimized window"


def test_plan_outcome_miss_appends_no_match():
    """plan_outcome(status=miss) → 'no match' HUD entry."""
    chat_log = ChatLog(max_lines=5, hold_ms=10_000, fade_ms=1_000)
    pipeline = _make_pipeline(chat_log)

    outcome = PlanOutcome(
        transcript="garbled",
        steps=(),
        status="miss",
        failed_step_index=None,
        error_msg=None,
        duration_ms=10,
    )
    pipeline.on_event("plan_outcome", outcome.to_event_dict())

    _run_worker_with_mock_clock(pipeline)

    entries = chat_log.entries()
    assert len(entries) == 1
    assert entries[0].status == "miss"
    assert entries[0].text == "no match"


def test_malformed_plan_outcome_falls_back_to_raw_transcript():
    """Malformed event dict with empty transcript → no ChatLog entry appended.

    When from_event_dict() raises and raw_text is empty string, the fallback
    branch skips appending (guarded by ``if raw_text:``).
    """
    chat_log = ChatLog(max_lines=5, hold_ms=10_000, fade_ms=1_000)
    pipeline = _make_pipeline(chat_log)

    # Missing required 'transcript' key — from_event_dict will raise KeyError
    bad_data = {"status": "ok", "steps": [], "duration_ms": 0}
    pipeline.on_event("plan_outcome", bad_data)

    # No queue item — entry appended synchronously in on_event fallback
    entries = chat_log.entries()
    assert len(entries) == 0  # raw_text was empty string, so nothing appended


def test_malformed_plan_outcome_with_transcript_appends_error_entry():
    """Malformed event dict with non-empty transcript → error ChatLog entry appended.

    When from_event_dict() raises and raw_text is non-empty, the fallback
    branch appends the raw transcript as an error-status entry.
    """
    chat_log = ChatLog(max_lines=5, hold_ms=10_000, fade_ms=1_000)
    pipeline = _make_pipeline(chat_log)

    # Has 'transcript' but step dict missing 'name' key — from_event_dict raises KeyError
    bad_data = {
        "transcript": "open browser",
        "status": "ok",
        "steps": [{"kwargs": {}}],
        "duration_ms": 0,
    }
    pipeline.on_event("plan_outcome", bad_data)

    entries = chat_log.entries()
    assert len(entries) == 1
    assert entries[0].status == "error"
    assert entries[0].text == "open browser"


def test_tool_fired_shows_bubble():
    """tool_fired event → bubble.show() called with tool name."""
    chat_log = ChatLog(max_lines=5, hold_ms=10_000, fade_ms=1_000)
    pipeline = _make_pipeline(chat_log)
    pipeline._bubble = MagicMock()

    pipeline.on_event("tool_fired", {"name": "copy", "ts": 1.0})

    pipeline._bubble.show.assert_called_once_with("copy")
