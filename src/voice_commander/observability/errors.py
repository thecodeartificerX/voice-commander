"""Error categorisation helper for the observability pipeline.

Four buckets:
- program  : tool function raised an unhandled exception during execution
- wiring   : graph structure/runtime error (missing kwarg, dangling port, bad branch, etc.)
- llm      : router returned malformed plan, unknown tool, or retry budget exceeded
- infra    : LM Studio unreachable, audio device gone, SQLite locked, etc.
"""

from __future__ import annotations

from typing import Literal

Category = Literal["program", "wiring", "llm", "infra"]


# Exceptions that classify as 'wiring' — imported lazily to avoid circular imports
_WIRING_TYPES: tuple[str, ...] = ("WiringError",)

# Exceptions that classify as 'llm'
_LLM_TYPES: tuple[str, ...] = ("LLMPlanError",)

# Network error type names (httpx + stdlib)
_INFRA_TYPE_NAMES: tuple[str, ...] = (
    "ConnectError",
    "ConnectTimeout",
    "ReadTimeout",
    "RemoteProtocolError",
    "ConnectionRefusedError",
    "ConnectionError",
    "TimeoutError",
    "PortAudioError",
    "OSError",
)


def classify(exc: BaseException, *, where: str = "") -> Category:  # noqa: ARG001
    """Return the error category for *exc*.

    *where* is a hint string (e.g. ``"graph_runtime"``, ``"dispatcher"``, ``"llm_router"``)
    used as a tiebreaker when the exception type alone is ambiguous.
    It is logged for debugging but does not currently change the classification.

    Classification priority:
    1. WiringError  → ``wiring``
    2. LLMPlanError → ``llm``
    3. Network/IO   → ``infra``
    4. default      → ``program``
    """
    type_name = type(exc).__name__

    if type_name in _WIRING_TYPES:
        return "wiring"

    if type_name in _LLM_TYPES:
        return "llm"

    # Walk the MRO for infra-ish types (covers httpx subclasses, etc.)
    mro_names = {t.__name__ for t in type(exc).__mro__}
    if mro_names & set(_INFRA_TYPE_NAMES):
        return "infra"

    return "program"
