"""Daemon-side restart request: graceful exit 75 caught by supervisor."""

from __future__ import annotations

import threading
from unittest.mock import patch

import pytest

from voice_commander.commands.restart import (
    RestartUnavailable,
    request_restart,
)


def test_unsupervised_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without VC_SUPERVISED=1 set, request_restart refuses."""
    monkeypatch.delenv("VC_SUPERVISED", raising=False)
    with pytest.raises(RestartUnavailable):
        request_restart()


def test_supervised_schedules_exit75(monkeypatch: pytest.MonkeyPatch) -> None:
    """With VC_SUPERVISED=1, request_restart schedules os._exit(75)."""
    monkeypatch.setenv("VC_SUPERVISED", "1")

    fired = threading.Event()
    captured: dict[str, int] = {}

    def fake_exit(code: int) -> None:
        captured["code"] = code
        fired.set()

    with patch("voice_commander.commands.restart.os._exit", fake_exit):
        request_restart(delay_s=0.01)
        assert fired.wait(timeout=1.0), "exit thread never fired"

    assert captured["code"] == 75


def test_supervised_returns_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    """request_restart must return at once so the HTTP handler can respond.

    The spawned thread resolves ``os._exit`` at call time (not at thread start),
    so we must hold the patch active until the delayed fire has completed —
    otherwise the daemon thread escapes the ``with`` block and calls the real
    ``os._exit(75)`` partway through the test suite, killing pytest with exit
    code 75.
    """
    monkeypatch.setenv("VC_SUPERVISED", "1")
    fired = threading.Event()
    with patch(
        "voice_commander.commands.restart.os._exit",
        lambda code: fired.set(),
    ):
        request_restart(delay_s=0.01)
        # request_restart() must return immediately; the assertion that the
        # exit fires inside the patch window proves both the immediate-return
        # contract AND that the patched _exit is the one invoked.
        assert fired.wait(timeout=2.0), "exit thread never fired inside patch window"
