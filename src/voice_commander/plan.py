"""Plan and ToolCall dataclasses for LLM router output."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolCall:
    """A single tool invocation within a plan."""

    name: str
    kwargs: dict[str, Any]


@dataclass(frozen=True)
class Plan:
    """Ordered sequence of tool calls produced by the LLM router."""

    steps: tuple[ToolCall, ...]
    raw_response: dict[str, Any]
