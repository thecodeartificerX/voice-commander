"""plan_outcome and ask_user SSE event handlers — extracted for testability."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from voice_commander.plan import PlanOutcome

from .chat_log import ChatLog, ChatLogEntry
from .summarizer import Summarizer

logger = logging.getLogger(__name__)


def handle_plan_outcome(
    data: dict[str, Any],
    summarizer: Summarizer,
    chat_log: ChatLog,
    now_provider: Callable[[], float] = time.monotonic,
) -> None:
    """Process a ``plan_outcome`` SSE event dict into a HUD ChatLog entry.

    Parsing failures are swallowed with a warning; the raw transcript is
    appended as an error entry when present.  An empty summary suppresses
    the append entirely.  If the summarizer itself raises, the raw transcript
    is used as a last-resort fallback (mirrors the old worker-thread behaviour).
    """
    raw_text: str = data.get("transcript", "")
    try:
        outcome = PlanOutcome.from_event_dict(data)
    except (KeyError, ValueError, TypeError, AttributeError):
        logger.exception("Failed to parse PlanOutcome — falling back to raw transcript")
        if raw_text:
            chat_log.append(
                ChatLogEntry(
                    text=raw_text,
                    status="error",
                    born_at_s=now_provider(),
                )
            )
        return

    try:
        summary = summarizer.summarize(outcome)
    except Exception:
        logger.exception("Summarizer failed — using raw transcript as fallback")
        summary = raw_text

    if not summary:
        return

    chat_log.append(
        ChatLogEntry(
            text=summary,
            status=outcome.status,
            born_at_s=now_provider(),
        )
    )


def handle_ask_user(
    data: dict[str, Any],
    chat_log: ChatLog,
    now_provider: Callable[[], float] = time.monotonic,
) -> None:
    """Process an ``ask_user`` SSE event dict into a HUD ChatLog entry.

    Renders the question and any options as a single chat-log entry with
    status ``"ok"`` so the user sees the clarification prompt in the overlay.
    """
    question = data.get("question", "")
    options = data.get("options", "")

    if not question:
        logger.warning("ask_user event with empty question; ignoring")
        return

    text = question
    if options:
        text = f"{question}\n{options}"

    chat_log.append(
        ChatLogEntry(
            text=text,
            status="ok",
            born_at_s=now_provider(),
        )
    )
