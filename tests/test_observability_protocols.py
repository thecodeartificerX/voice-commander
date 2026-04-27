"""Verify StoreProtocol structural typing."""

from __future__ import annotations

from voice_commander.observability.protocols import StoreProtocol


class _Dummy:
    def write_run_start(self, *a, **k) -> None: ...
    def write_run_end(self, *a, **k) -> None: ...
    def write_span(self, *a, **k) -> None: ...
    def write_run_transcript_update(self, *a, **k) -> None: ...


def test_dummy_satisfies_protocol() -> None:
    assert isinstance(_Dummy(), StoreProtocol)


def test_noop_store_satisfies_protocol() -> None:
    from voice_commander.daemon import _NoopStore
    assert isinstance(_NoopStore(), StoreProtocol)


def test_real_store_satisfies_protocol() -> None:
    from voice_commander.observability.store import Store
    # Store has these methods, so it satisfies the protocol structurally
    assert issubclass(Store, StoreProtocol)


def test_incomplete_class_fails_protocol() -> None:
    """A class missing methods must NOT satisfy StoreProtocol."""

    class _Incomplete:
        def write_run_start(self, *a, **k) -> None: ...
        def write_run_end(self, *a, **k) -> None: ...

    assert not isinstance(_Incomplete(), StoreProtocol)
