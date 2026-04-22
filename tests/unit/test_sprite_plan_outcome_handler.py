"""Unit tests for handle_plan_outcome (issue #25)."""

from __future__ import annotations

from unittest.mock import MagicMock

from voice_sprite.chat_log import ChatLog
from voice_sprite.plan_outcome_handler import handle_plan_outcome


def _chat_log() -> ChatLog:
    return ChatLog(max_lines=10, hold_ms=3000, fade_ms=1000)


def _valid_data(status: str = "ok", summary_steps: bool = True) -> dict:
    return {
        "transcript": "open spotify",
        "steps": [{"name": "open", "kwargs": {"target": "spotify"}}] if summary_steps else [],
        "status": status,
        "failed_step_index": None,
        "error_msg": None,
        "duration_ms": 120,
    }


# --- happy path ---


def test_happy_path_appends_entry():
    """Successful parse + non-empty summary → entry appears in chat_log."""
    summarizer = MagicMock()
    summarizer.summarize.return_value = "opened spotify"
    log = _chat_log()
    fixed_time = 42.0

    handle_plan_outcome(_valid_data(), summarizer, log, now_provider=lambda: fixed_time)

    entries = log.entries()
    assert len(entries) == 1
    assert entries[0].text == "opened spotify"
    assert entries[0].status == "ok"
    assert entries[0].born_at_s == fixed_time


# --- malformed dict ---


def test_malformed_dict_swallowed_with_warning(caplog):
    """KeyError during parse is swallowed; a warning is logged; nothing raises."""
    import logging

    summarizer = MagicMock()
    log = _chat_log()
    bad_data: dict = {"not_transcript": "x"}  # missing "transcript" key on parse

    with caplog.at_level(logging.WARNING, logger="voice_sprite.plan_outcome_handler"):
        handle_plan_outcome(bad_data, summarizer, log)  # must not raise

    summarizer.summarize.assert_not_called()
    assert any(
        "swallowed" in r.message.lower() or "parse" in r.message.lower()
        for r in caplog.records
    )


def test_malformed_dict_with_raw_text_appends_error_entry():
    """When parse fails AND transcript present, raw transcript appended as error."""
    summarizer = MagicMock()
    log = _chat_log()
    # "steps" contains dicts missing "name" key → KeyError inside from_event_dict
    bad_data = {"transcript": "copy that", "steps": [{"bad": "data"}]}

    handle_plan_outcome(bad_data, summarizer, log)

    entries = log.entries()
    assert len(entries) == 1
    assert entries[0].text == "copy that"
    assert entries[0].status == "error"


# --- empty summary ---


def test_empty_summary_does_not_append():
    """summarizer.summarize() returning '' must not append any entry."""
    summarizer = MagicMock()
    summarizer.summarize.return_value = ""
    log = _chat_log()

    handle_plan_outcome(_valid_data(), summarizer, log)

    assert log.entries() == []


# --- now_provider ---


def test_now_provider_controls_born_at_s():
    """born_at_s of appended entry equals now_provider() result, not wall time."""
    summarizer = MagicMock()
    summarizer.summarize.return_value = "did the thing"
    log = _chat_log()
    sentinel = 999.5

    handle_plan_outcome(_valid_data(), summarizer, log, now_provider=lambda: sentinel)

    assert log.entries()[0].born_at_s == sentinel


# --- summarizer exception ---


def test_summarizer_exception_falls_back_to_raw_text(caplog):
    """If summarizer.summarize() raises, raw transcript used as fallback."""
    import logging

    summarizer = MagicMock()
    summarizer.summarize.side_effect = RuntimeError("LM Studio timeout")
    log = _chat_log()

    with caplog.at_level(logging.ERROR, logger="voice_sprite.plan_outcome_handler"):
        handle_plan_outcome(_valid_data(), summarizer, log)

    entries = log.entries()
    assert len(entries) == 1
    assert entries[0].text == "open spotify"  # raw transcript from _valid_data
    assert entries[0].status == "ok"
    assert any("fallback" in r.message.lower() for r in caplog.records)
