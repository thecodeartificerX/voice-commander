"""Structural protocols for the observability subsystem."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StoreProtocol(Protocol):
    """Minimal write interface satisfied by both Store and _NoopStore."""

    def write_run_start(self, *_a: Any, **_k: Any) -> None: ...
    def write_run_end(self, *_a: Any, **_k: Any) -> None: ...
    def write_span(self, *_a: Any, **_k: Any) -> None: ...
    def write_run_transcript_update(self, *_a: Any, **_k: Any) -> None: ...
