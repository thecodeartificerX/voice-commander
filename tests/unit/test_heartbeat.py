"""Tests for StreamingDaemon._heartbeat_loop — publishes daemon_heartbeat
at the configured cadence until the shutdown event is set.

The loop is:

    while not self._shutdown.wait(1.0):
        self._publish("daemon_heartbeat")

which is exercised via __new__ bypass + mocked _shutdown.wait side_effect
so neither a real sleep nor a real EventBus is needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from voice_commander.daemon import StreamingDaemon


def _make_daemon_shell(event_bus: MagicMock, shutdown: MagicMock) -> StreamingDaemon:
    """Build a StreamingDaemon instance without invoking __init__.

    _heartbeat_loop touches self._shutdown, self._event_bus (via
    self._publish), and self._picker_session (via self._tick_picker_session).
    """
    d = StreamingDaemon.__new__(StreamingDaemon)
    d._shutdown = shutdown
    d._event_bus = event_bus
    d._picker_session = None
    return d


def test_heartbeat_publishes_each_tick_until_shutdown() -> None:
    """wait() returns False three times then True — expect three publishes."""
    bus = MagicMock()
    shutdown = MagicMock()
    shutdown.wait.side_effect = [False, False, False, True]

    daemon = _make_daemon_shell(bus, shutdown)
    daemon._heartbeat_loop()

    assert bus.publish.call_count == 3
    for call in bus.publish.call_args_list:
        assert call.args[0] == "daemon_heartbeat"
        assert call.args[1] is None


def test_heartbeat_polls_wait_with_one_second_interval() -> None:
    """Cadence: every call to wait() uses the 1.0 s interval."""
    bus = MagicMock()
    shutdown = MagicMock()
    shutdown.wait.side_effect = [False, False, True]

    daemon = _make_daemon_shell(bus, shutdown)
    daemon._heartbeat_loop()

    assert shutdown.wait.call_count == 3
    for call in shutdown.wait.call_args_list:
        assert call.args == (1.0,)


def test_heartbeat_exits_immediately_when_shutdown_already_set() -> None:
    """First wait() returns True (shutdown set before loop entry) — no publish."""
    bus = MagicMock()
    shutdown = MagicMock()
    shutdown.wait.return_value = True

    daemon = _make_daemon_shell(bus, shutdown)
    daemon._heartbeat_loop()

    bus.publish.assert_not_called()
    assert shutdown.wait.call_count == 1


def test_heartbeat_no_event_bus_is_noop() -> None:
    """_publish tolerates event_bus=None; heartbeat loop must not raise."""
    shutdown = MagicMock()
    shutdown.wait.side_effect = [False, False, True]

    daemon = _make_daemon_shell(event_bus=None, shutdown=shutdown)  # type: ignore[arg-type]
    daemon._heartbeat_loop()

    assert shutdown.wait.call_count == 3
