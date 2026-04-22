"""plan_outcome SSE event handler — extracted for testability."""

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
    except (KeyError, ValueError, TypeError):
        logger.warning(
            "Failed to parse PlanOutcome — swallowed",
            exc_info=True,
        )
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
            status=outcome.status,  # type: ignore[arg-type]
            born_at_s=now_provider(),
        )
    )
