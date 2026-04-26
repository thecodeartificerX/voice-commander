"""Cross-platform child-process helpers for the supervisor."""

from __future__ import annotations

import sys
import time

from voice_commander.supervisor.process import (
    ChildHandle,
    spawn,
    terminate,
)


def _python_oneliner(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_spawn_returns_handle_with_pid() -> None:
    handle = spawn(_python_oneliner("import time; time.sleep(2)"), name="sleeper")
    try:
        assert isinstance(handle, ChildHandle)
        assert handle.pid > 0
        assert handle.name == "sleeper"
    finally:
        terminate(handle, grace_s=2.0)


def test_spawn_inherits_env_plus_extras() -> None:
    handle = spawn(
        _python_oneliner(
            "import os, sys; sys.exit(0 if os.environ.get('VC_TEST_VAR') == 'hi' else 1)"
        ),
        env_extra={"VC_TEST_VAR": "hi"},
    )
    code = handle.wait()
    assert code == 0


def test_wait_returns_exit_code() -> None:
    handle = spawn(_python_oneliner("import sys; sys.exit(7)"))
    assert handle.wait() == 7


def test_terminate_kills_running_child() -> None:
    handle = spawn(_python_oneliner("import time; time.sleep(60)"))
    assert handle.poll() is None  # alive
    terminate(handle, grace_s=2.0)
    assert handle.poll() is not None  # dead


def test_terminate_force_kills_after_grace() -> None:
    """A child that ignores SIGTERM/CTRL_BREAK is force-killed after grace_s."""
    if sys.platform == "win32":
        # Child catches CTRL_BREAK and ignores it.
        code = (
            "import signal, time; signal.signal(signal.SIGBREAK, lambda *_: None); time.sleep(60)"
        )
    else:
        code = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
    handle = spawn(_python_oneliner(code))
    started = time.monotonic()
    terminate(handle, grace_s=0.5)
    elapsed = time.monotonic() - started
    assert handle.poll() is not None, "force-kill failed"
    assert elapsed < 5.0, f"force-kill took too long: {elapsed:.1f}s"
