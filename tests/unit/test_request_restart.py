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
    """request_restart must return at once so the HTTP handler can respond."""
    monkeypatch.setenv("VC_SUPERVISED", "1")
    with patch("voice_commander.commands.restart.os._exit", lambda code: None):
        request_restart(delay_s=5.0)  # would block 5s if implemented synchronously
