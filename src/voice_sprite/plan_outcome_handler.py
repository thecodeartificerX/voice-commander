"""plan_outcome and tool_fired SSE event handlers — primitives-only HUD.

The HUD now mirrors the live tool stream: each ``tool_fired`` event pushes the
raw tool name to the chat log; ``plan_outcome`` with ``status="miss"`` pushes
``"no match"``. Errors render as ``"<tool_name> failed"``. Successful runs are
already represented by the per-step ``tool_fired`` lines, so we suppress the
trailing ``ok`` outcome to avoid double-rendering.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from voice_commander.plan import PlanOutcome

from .chat_log import ChatLog, ChatLogEntry

logger = logging.getLogger(__name__)


def handle_plan_outcome(
    data: dict[str, Any],
    chat_log: ChatLog,
    now_provider: Callable[[], float] = time.monotonic,
) -> None:
    """Append a HUD line for a `plan_outcome` SSE payload.

    * ``status="miss"`` -> ``"no match"``.
    * ``status="error"`` -> ``"<failed_tool> failed"`` (or ``"command failed"``
      when no failed-step index is set).
    * ``status="ok"`` -> no append (the per-step ``tool_fired`` events have
      already populated the HUD).
    """
    try:
        outcome = PlanOutcome.from_event_dict(data)
    except (KeyError, ValueError, TypeError, AttributeError):
        logger.exception("Failed to parse PlanOutcome — skipping HUD append")
        return

    if outcome.status == "ok":
        return

    if outcome.status == "miss":
        text = "no match"
    else:
        idx = outcome.failed_step_index
        if idx is not None and 0 <= idx < len(outcome.steps):
            text = f"{outcome.steps[idx].name} failed"
        else:
            text = "command failed"

    chat_log.append(
        ChatLogEntry(
            text=text,
            status=outcome.status,
            born_at_s=now_provider(),
        )
    )


def handle_tool_fired(
    data: dict[str, Any],
    chat_log: ChatLog,
    now_provider: Callable[[], float] = time.monotonic,
) -> None:
    """Append the raw tool name on every `tool_fired` SSE event."""
    name = data.get("name")
    if not name:
        return
    chat_log.append(
        ChatLogEntry(
            text=str(name),
            status="ok",
            born_at_s=now_provider(),
        )
    )


def handle_transcript(
    data: dict[str, Any],
    chat_log: ChatLog,
    now_provider: Callable[[], float] = time.monotonic,
) -> None:
    """Append the recognised transcript so the user can see what was heard
    before any routing / execution decision lands."""
    text = data.get("text")
    if not text:
        return
    chat_log.append(
        ChatLogEntry(
            text=f"“{str(text).strip()}”",
            status="info",
            born_at_s=now_provider(),
        )
    )
