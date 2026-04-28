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

# Stdlib network/audio types that *always* count as infra regardless of where
# they were raised. ``OSError`` is intentionally excluded — most filesystem
# errors (FileNotFoundError, PermissionError) inherit from OSError but are
# program bugs, not infrastructure failures. (B-M1)
_INFRA_STDLIB_TYPE_NAMES: frozenset[str] = frozenset(
    {
        "ConnectionRefusedError",
        "ConnectionResetError",
        "ConnectionAbortedError",
        "ConnectionError",
        "TimeoutError",
        "PortAudioError",
        "gaierror",  # socket.gaierror — DNS failure
    }
)


def classify(exc: BaseException, *, where: str = "") -> Category:
    """Return the error category for *exc*.

    *where* is a hint string (e.g. ``"graph_runtime"``, ``"dispatcher"``,
    ``"llm_router"``, ``"daemon"``) used as a tiebreaker. Specifically:
    raw ``OSError`` is treated as infra only when raised from the daemon
    audio path (``where == "daemon"``); elsewhere it is a program bug.

    Classification priority:
    1. WiringError    → ``wiring``
    2. LLMPlanError   → ``llm``
    3. httpx errors   → ``infra``  (any ``httpx.*`` exception class)
    4. stdlib net/audio types → ``infra``
    5. raw OSError from daemon audio path → ``infra``
    6. default        → ``program``
    """
    type_name = type(exc).__name__

    if type_name in _WIRING_TYPES:
        return "wiring"

    if type_name in _LLM_TYPES:
        return "llm"

    # Any class defined in the httpx package is an infra error — covers
    # ConnectError, ConnectTimeout, ReadTimeout, RemoteProtocolError, etc.
    # We check the module path instead of walking OSError's MRO so
    # ``FileNotFoundError`` / ``PermissionError`` stay classified as
    # program bugs.
    module = type(exc).__module__ or ""
    if module == "httpx" or module.startswith("httpx."):
        return "infra"

    if type_name in _INFRA_STDLIB_TYPE_NAMES:
        return "infra"

    # Raw OSError from the daemon's audio path is infra (PortAudio device
    # gone, mic unplugged). Anywhere else it's a program bug.
    if where == "daemon" and type(exc) is OSError:
        return "infra"

    return "program"
