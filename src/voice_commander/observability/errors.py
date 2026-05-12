"""Error categorisation helper for the observability pipeline.

Three buckets:
- program  : tool function raised an unhandled exception during execution
- wiring   : graph structure/runtime error (missing kwarg, dangling port, bad branch, etc.)
- infra    : audio device gone, SQLite locked, network unreachable, etc.
"""

from __future__ import annotations

from enum import StrEnum  # requires Python 3.11+


class Category(StrEnum):
    """3-bucket runtime error taxonomy.

    Inherits from ``StrEnum`` so ``Category.PROGRAM == "program"`` is True and
    JSON / SQLite serialisation produces the bare lowercase strings — keeping
    the on-disk wire format unchanged.
    """

    PROGRAM = "program"
    WIRING = "wiring"
    INFRA = "infra"


# Exceptions that classify as 'wiring' — imported lazily to avoid circular imports
_WIRING_TYPES: tuple[str, ...] = ("WiringError",)

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
    ``"daemon"``) used as a tiebreaker. Specifically:
    raw ``OSError`` is treated as infra only when raised from the daemon
    audio path (``where == "daemon"``); elsewhere it is a program bug.

    Classification priority:
    1. WiringError    → ``wiring``
    2. httpx errors   → ``infra``  (any ``httpx.*`` exception class)
    3. stdlib net/audio types → ``infra``
    4. raw OSError from daemon audio path → ``infra``
    5. default        → ``program``
    """
    type_name = type(exc).__name__

    if type_name in _WIRING_TYPES:
        return Category.WIRING

    # Any class defined in the httpx package is an infra error — covers
    # ConnectError, ConnectTimeout, ReadTimeout, RemoteProtocolError, etc.
    # We check the module path instead of walking OSError's MRO so
    # ``FileNotFoundError`` / ``PermissionError`` stay classified as
    # program bugs.
    module = type(exc).__module__ or ""
    if module == "httpx" or module.startswith("httpx."):
        return Category.INFRA

    if type_name in _INFRA_STDLIB_TYPE_NAMES:
        return Category.INFRA

    # Raw OSError from the daemon's audio path is infra (PortAudio device
    # gone, mic unplugged). Anywhere else it's a program bug.
    if where == "daemon" and type(exc) is OSError:
        return Category.INFRA

    return Category.PROGRAM
