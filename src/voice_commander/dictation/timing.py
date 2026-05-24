"""Pure per-phase latency record builder for dictation timing observability (ADR 0101).

This module is intentionally side-effect-free (no I/O, no imports from the
rest of the daemon) so that :func:`build_timing_record` can be unit-tested
without any daemon infrastructure.

The record schema is locked by ADR 0101:

.. code-block:: python

    {
        "ts":             float,            # time.time() at record creation
        "chars":          int,              # len(text)
        "server":         dict,             # done-frame "timings" dict, may be {}
        "roundtrip_ms":   float | None,     # client: end-frame-sent -> done-frame-received
        "network_ms":     float | None,     # roundtrip_ms - server["server_total_ms"]
        "postprocess_ms": float,            # apply_corrections + apply_commands wall time
        "paste_ms":       float,            # clipboard.paste_via_clipboard wall time
        "total_ms":       float,            # (roundtrip_ms or 0) + postprocess_ms + paste_ms
    }
"""

from __future__ import annotations

import time
from typing import Any


def build_timing_record(
    *,
    text: str,
    server_timings: dict[str, float],
    roundtrip_ms: float | None,
    postprocess_ms: float,
    paste_ms: float,
) -> dict[str, Any]:
    """Build the locked timing record for one completed dictation (ADR 0101).

    Parameters
    ----------
    text:
        The final pasted transcript.  Only ``len(text)`` is stored.
    server_timings:
        The server's optional ``timings`` dict from the done frame
        (keys: ``transcribe_ms``, ``clean_ms``, ``format_ms``,
        ``server_total_ms``).  Pass ``{}`` when the server did not include
        timing data (older server) — the record will have ``"server": {}``
        and ``"network_ms": None``.
    roundtrip_ms:
        Client-measured wall time in milliseconds from end-frame-sent to
        done-frame-received.  ``None`` when no done frame arrived (failure
        paths do not call this function — only the successful-paste branch
        records timings, so in practice this will always be a float).
    postprocess_ms:
        Wall time in milliseconds for
        ``apply_corrections`` + ``apply_commands``.
    paste_ms:
        Wall time in milliseconds for ``clipboard.paste_via_clipboard``.

    Returns
    -------
    dict
        The locked timing record described in the module docstring.
    """
    server_total: float | None = server_timings.get("server_total_ms")
    if roundtrip_ms is not None and server_total is not None:
        network_ms: float | None = roundtrip_ms - server_total
    else:
        network_ms = None

    total_ms = (roundtrip_ms or 0.0) + postprocess_ms + paste_ms

    return {
        "ts": time.time(),
        "chars": len(text),
        "server": server_timings,
        "roundtrip_ms": roundtrip_ms,
        "network_ms": network_ms,
        "postprocess_ms": postprocess_ms,
        "paste_ms": paste_ms,
        "total_ms": total_ms,
    }
