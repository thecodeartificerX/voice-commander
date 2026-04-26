# Supervisor Process Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Insert a long-lived Python supervisor between `start.ps1` and the voice-commander daemon. Supervisor owns the sprite (persistent across daemon restarts) and respawns the daemon on graceful exit code 75 from the web UI's Restart button.

**Architecture:** New `voice_commander.supervisor` package + `voice-commander-supervisor` console script. Daemon's existing `commands/restart.py:schedule_restart()` (detach-spawn-and-exit) is replaced by `request_restart()` (graceful exit 75). Supervisor's restart loop branches on the daemon's exit code: `0` stop, `75` respawn, anything else terminate sprite and exit with the same code (no auto-restart on crash). Sprite-spawn block is removed from `start.ps1`; supervisor owns it.

**Tech Stack:** Python 3.11, `subprocess.Popen`, `psutil` (already a dep) for tree-kill on Windows, FastAPI for the daemon's web layer, pytest for tests.

**Spec:** `docs/superpowers/specs/2026-04-25-supervisor-process-design.md`

---

## File Structure

**New files**

| Path | Purpose |
|------|---------|
| `src/voice_commander/supervisor/__init__.py` | Package init, exports `main`. |
| `src/voice_commander/supervisor/__main__.py` | `python -m voice_commander.supervisor` shim. |
| `src/voice_commander/supervisor/cli.py` | argparse, logging, single-instance lock, calls `loop.run`. |
| `src/voice_commander/supervisor/process.py` | Cross-platform spawn / wait / terminate helpers. |
| `src/voice_commander/supervisor/sprite.py` | `SpriteChild` — spawn + terminate. Best-effort. |
| `src/voice_commander/supervisor/loop.py` | The restart loop. Pure logic, no subprocess calls — takes injected child factories so tests can stub them. |
| `src/voice_commander/supervisor/exit_codes.py` | Named constants: `EXIT_CLEAN=0`, `EXIT_RESTART=75`. |
| `tests/unit/supervisor/__init__.py` | empty |
| `tests/unit/supervisor/test_loop.py` | Loop branches by injected exit-code sequences. |
| `tests/unit/supervisor/test_process.py` | Mocked subprocess: terminate flow, force-kill on grace timeout. |
| `tests/unit/supervisor/test_sprite.py` | SpriteChild handles spawn-failure as best-effort. |
| `tests/unit/supervisor/test_cli.py` | CLI arg parsing, lock acquisition errors. |
| `tests/unit/test_request_restart.py` | `request_restart` raises when unsupervised; exits 75 when supervised. |
| `tests/integration/test_supervisor_e2e.py` | Spawns supervisor against a stub daemon that exits with scripted codes. |
| `scripts/_fake_daemon.py` | Test stub — sleeps then exits with code from `argv[1]`. Used by integration test. |
| `scripts/_fake_sprite.py` | Test stub — sleeps until killed, writes pid to a file. Used by integration test. |
| `docs/decisions/0058-supervisor-process-owns-lifecycle.md` | New ADR. |

**Modified files**

| Path | Change |
|------|--------|
| `pyproject.toml` | Add `voice-commander-supervisor` console script. |
| `src/voice_commander/commands/restart.py` | Replace `schedule_restart` (detach-spawn) with `request_restart` (graceful exit 75) + `RestartUnavailable`. |
| `src/voice_commander/web/admin.py` | `/restart` route catches `RestartUnavailable` → 503; per-field "needs restart" set drives a banner; `config_save` returns banner text reflecting the saved fields. |
| `start.ps1` | Replace `Start-VoiceDaemon` with `Start-VoiceSupervisor`; delete sprite spawn/teardown block. |
| `.gitignore` | Add `outputs/.supervisor.lock`. |
| `docs/architecture.md` | Refresh process-tree diagram. |
| `docs/agents/technical-decisions.md` | Add row pointing to ADR 0058. |
| `README.md` | Note that Restart button requires the supervisor. |

---

## Phasing

Six phases. Each is a green-tested commit. Order is bottom-up: leaves first, then assembly, then wire-in, then UX.

1. **Phase 1** — Exit-code constants + `request_restart` rewrite (no supervisor yet; daemon stops working until Phase 6 — that's fine because we're on a feature branch).
2. **Phase 2** — `process.py` (spawn/wait/terminate helpers).
3. **Phase 3** — `sprite.py` and `loop.py` (pure logic, fully unit-tested).
4. **Phase 4** — `cli.py` + console-script wiring.
5. **Phase 5** — Web layer: `/restart` + per-field "needs restart" banner.
6. **Phase 6** — `start.ps1` swap, ADR, docs, manual validation.

---

# Phase 1 — Exit codes + `request_restart`

## Task 1: Exit-code constants

**Files:**
- Create: `src/voice_commander/supervisor/__init__.py`
- Create: `src/voice_commander/supervisor/exit_codes.py`

- [ ] **Step 1: Create the package**

`src/voice_commander/supervisor/__init__.py`:
```python
"""Long-lived parent process that owns the daemon + sprite lifecycle."""

from __future__ import annotations

from .cli import main

__all__ = ["main"]
```

(`cli` doesn't exist yet — that's fine, this file is committed alongside `cli.py` later. Skip this until Phase 4 if your editor flags it. To keep Phase 1 self-contained, instead create the package init with no imports for now and add the `from .cli import main` line in Phase 4.)

Use this minimal Phase-1 version:
```python
"""Long-lived parent process that owns the daemon + sprite lifecycle."""
```

- [ ] **Step 2: Create `exit_codes.py`**

`src/voice_commander/supervisor/exit_codes.py`:
```python
"""Exit codes the daemon emits for the supervisor to interpret.

The contract is intentionally tiny: zero for clean shutdown, ``EXIT_RESTART``
for "respawn me," anything else is a crash and the supervisor exits with the
same code.
"""

from __future__ import annotations

EXIT_CLEAN: int = 0
EXIT_RESTART: int = 75
```

- [ ] **Step 3: Commit**

```bash
git add src/voice_commander/supervisor/__init__.py src/voice_commander/supervisor/exit_codes.py
git commit -m "feat(supervisor): scaffold package + exit-code constants"
```

---

## Task 2: `request_restart` — TDD

**Files:**
- Test: `tests/unit/test_request_restart.py`
- Modify: `src/voice_commander/commands/restart.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/test_request_restart.py`:
```python
"""Daemon-side restart request: graceful exit 75 caught by supervisor."""

from __future__ import annotations

import os
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
```

- [ ] **Step 2: Run tests, expect failure**

```
uv run pytest tests/unit/test_request_restart.py -v
```
Expected: ImportError on `RestartUnavailable` / `request_restart`.

- [ ] **Step 3: Rewrite `commands/restart.py`**

`src/voice_commander/commands/restart.py` (replace entire file contents):
```python
"""Daemon-side restart helper.

Exiting the process with code ``EXIT_RESTART`` (75) signals the supervisor
to respawn us. We schedule the exit in a background thread so the calling
HTTP handler can return a clean response first.

If the daemon is running without a supervisor (``VC_SUPERVISED`` env var
unset), we refuse — exiting 75 with no parent to interpret it would just
kill the daemon with no replacement.
"""

from __future__ import annotations

import logging
import os
import threading
import time

from ..supervisor.exit_codes import EXIT_RESTART

logger = logging.getLogger(__name__)


class RestartUnavailable(RuntimeError):
    """Raised when the daemon is asked to restart but has no supervisor."""


def request_restart(delay_s: float = 0.5) -> None:
    """Schedule a graceful exit with code 75 so the supervisor respawns us.

    The delay gives the calling HTTP handler time to return a response to
    the browser before the current process tears down.

    Raises ``RestartUnavailable`` immediately if no supervisor is present.
    """
    if os.environ.get("VC_SUPERVISED") != "1":
        raise RestartUnavailable(
            "Restart requires the supervisor. Launch with start.ps1 or "
            "voice-commander-supervisor."
        )

    def _do() -> None:
        time.sleep(delay_s)
        logger.info("Exiting with code %d for supervisor restart", EXIT_RESTART)
        os._exit(EXIT_RESTART)

    threading.Thread(target=_do, daemon=True, name="daemon-restart").start()
```

- [ ] **Step 4: Run tests, expect pass**

```
uv run pytest tests/unit/test_request_restart.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Verify nothing else imports `schedule_restart`**

```
uv run python -c "import voice_commander.commands.restart as r; print(dir(r))"
```
Then grep:
```
git grep schedule_restart -- :!docs/
```
Expected: only the `web/admin.py` reference (we fix that in Phase 5).

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_request_restart.py src/voice_commander/commands/restart.py
git commit -m "feat(daemon): request_restart graceful exit 75 (RestartUnavailable when unsupervised)"
```

---

# Phase 2 — `process.py`

## Task 3: `ChildHandle` + `spawn` (TDD)

**Files:**
- Create: `src/voice_commander/supervisor/process.py`
- Test: `tests/unit/supervisor/__init__.py` (empty)
- Test: `tests/unit/supervisor/test_process.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/supervisor/__init__.py`:
```python
```

`tests/unit/supervisor/test_process.py`:
```python
"""Cross-platform child-process helpers for the supervisor."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

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
        _python_oneliner("import os, sys; sys.exit(0 if os.environ.get('VC_TEST_VAR') == 'hi' else 1)"),
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
```

- [ ] **Step 2: Run tests, expect failure**

```
uv run pytest tests/unit/supervisor/test_process.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement `process.py`**

`src/voice_commander/supervisor/process.py`:
```python
"""Cross-platform spawn / wait / terminate helpers.

Windows is the only target today, but POSIX paths are sketched so the door
isn't slammed shut. Children inherit the supervisor's stdout/stderr (so log
lines interleave naturally in the launching console). stdin is `DEVNULL`.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import signal
import subprocess
import sys
import time
from typing import Sequence

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class ChildHandle:
    """Opaque handle returned by :func:`spawn`."""

    name: str
    pid: int
    popen: subprocess.Popen[bytes]

    def poll(self) -> int | None:
        """Return the exit code if the child has exited, else ``None``."""
        return self.popen.poll()

    def wait(self) -> int:
        """Block until the child exits and return its exit code."""
        return self.popen.wait()


def spawn(
    argv: Sequence[str],
    *,
    name: str = "child",
    env_extra: dict[str, str] | None = None,
    cwd: str | os.PathLike[str] | None = None,
) -> ChildHandle:
    """Spawn *argv* as a managed child."""
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)

    creationflags = 0
    if sys.platform == "win32":
        # CREATE_NEW_PROCESS_GROUP — lets us send CTRL_BREAK_EVENT during
        # shutdown without affecting the supervisor's own console group.
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

    logger.info("Spawning %s: %s", name, " ".join(argv))
    popen = subprocess.Popen(  # noqa: S603 — argv built by caller
        list(argv),
        env=env,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    return ChildHandle(name=name, pid=popen.pid, popen=popen)


def terminate(handle: ChildHandle, *, grace_s: float) -> None:
    """Politely ask the child to exit; force-kill its process tree on timeout.

    On Windows: send ``CTRL_BREAK_EVENT`` (the child was spawned with
    ``CREATE_NEW_PROCESS_GROUP``), wait *grace_s*, then ``taskkill /T /F``
    if it's still alive. The ``/T`` walks the process tree so grandchildren
    (e.g. uv → python) die together.

    On POSIX: ``SIGTERM``, wait, ``SIGKILL``.
    """
    if handle.poll() is not None:
        logger.debug("terminate(%s): already exited (code=%s)", handle.name, handle.poll())
        return

    try:
        if sys.platform == "win32":
            os.kill(handle.pid, signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
        else:
            handle.popen.terminate()  # SIGTERM
    except (OSError, ProcessLookupError):
        logger.debug("terminate(%s): signal failed; child likely dead", handle.name)
        return

    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline:
        if handle.poll() is not None:
            logger.debug("terminate(%s): exited gracefully (code=%s)", handle.name, handle.poll())
            return
        time.sleep(0.05)

    logger.warning("terminate(%s): grace window elapsed; force-killing tree", handle.name)
    if sys.platform == "win32":
        subprocess.run(  # noqa: S603,S607
            ["taskkill", "/PID", str(handle.pid), "/T", "/F"],
            check=False,
            capture_output=True,
        )
    else:
        try:
            handle.popen.kill()
        except (OSError, ProcessLookupError):
            pass

    try:
        handle.popen.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        logger.error("terminate(%s): force-kill did not reap; orphan possible", handle.name)
```

- [ ] **Step 4: Run tests**

```
uv run pytest tests/unit/supervisor/test_process.py -v
```
Expected: 4 passed. (May take ~5 s due to real subprocess spawns.)

- [ ] **Step 5: Add a force-kill test (mocked, no real 60s wait)**

Append to `tests/unit/supervisor/test_process.py`:
```python
def test_terminate_force_kills_after_grace() -> None:
    """A child that ignores SIGTERM/CTRL_BREAK is force-killed after grace_s."""
    if sys.platform == "win32":
        # Child catches CTRL_BREAK and ignores it.
        code = (
            "import signal, time; "
            "signal.signal(signal.SIGBREAK, lambda *_: None); "
            "time.sleep(60)"
        )
    else:
        code = (
            "import signal, time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "time.sleep(60)"
        )
    handle = spawn(_python_oneliner(code))
    started = time.monotonic()
    terminate(handle, grace_s=0.5)
    elapsed = time.monotonic() - started
    assert handle.poll() is not None, "force-kill failed"
    assert elapsed < 5.0, f"force-kill took too long: {elapsed:.1f}s"
```

- [ ] **Step 6: Run tests**

```
uv run pytest tests/unit/supervisor/test_process.py -v
```
Expected: 5 passed.

- [ ] **Step 7: Commit**

```bash
git add tests/unit/supervisor/__init__.py tests/unit/supervisor/test_process.py src/voice_commander/supervisor/process.py
git commit -m "feat(supervisor): cross-platform spawn/wait/terminate helpers"
```

---

# Phase 3 — `sprite.py` + `loop.py`

## Task 4: `SpriteChild` (TDD)

**Files:**
- Create: `src/voice_commander/supervisor/sprite.py`
- Test: `tests/unit/supervisor/test_sprite.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/supervisor/test_sprite.py`:
```python
"""SpriteChild — best-effort spawn, never blocks supervisor on failure."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from voice_commander.supervisor.sprite import SpriteChild


def test_spawn_returns_instance() -> None:
    fake_handle = MagicMock(name="ChildHandle")
    with patch("voice_commander.supervisor.sprite.spawn", return_value=fake_handle) as mock_spawn:
        sprite = SpriteChild.spawn()
        assert sprite.handle is fake_handle
        mock_spawn.assert_called_once()


def test_spawn_swallows_failure(caplog: object) -> None:
    """If spawning the sprite fails, log a warning and return a no-op instance."""
    with patch(
        "voice_commander.supervisor.sprite.spawn",
        side_effect=FileNotFoundError("uv not on PATH"),
    ):
        with caplog.at_level(logging.WARNING):  # type: ignore[attr-defined]
            sprite = SpriteChild.spawn()
    assert sprite.handle is None
    assert any("sprite" in r.message.lower() for r in caplog.records)  # type: ignore[attr-defined]


def test_terminate_noop_when_handle_none() -> None:
    sprite = SpriteChild(handle=None)
    sprite.terminate(grace_s=1.0)  # must not raise


def test_terminate_calls_terminate_helper() -> None:
    fake_handle = MagicMock(name="ChildHandle")
    with patch("voice_commander.supervisor.sprite.terminate") as mock_term:
        sprite = SpriteChild(handle=fake_handle)
        sprite.terminate(grace_s=2.5)
        mock_term.assert_called_once_with(fake_handle, grace_s=2.5)
```

- [ ] **Step 2: Run tests, expect failure**

```
uv run pytest tests/unit/supervisor/test_sprite.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement `sprite.py`**

`src/voice_commander/supervisor/sprite.py`:
```python
"""Sprite child management.

The sprite is best-effort: if it fails to spawn, the supervisor logs a
warning and runs the daemon without a companion overlay. We do not
respawn the sprite when the daemon restarts — its SSE connection
auto-reconnects to the new daemon's web port.
"""

from __future__ import annotations

import dataclasses
import logging
import sys

from .process import ChildHandle, spawn, terminate

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class SpriteChild:
    """Wraps an optional ``ChildHandle`` for the sprite process."""

    handle: ChildHandle | None

    @classmethod
    def spawn(cls) -> "SpriteChild":
        """Spawn ``voice-sprite``. Returns an instance with ``handle=None`` on failure."""
        argv = [sys.executable, "-m", "voice_sprite"]
        try:
            handle = spawn(argv, name="voice-sprite")
        except (OSError, FileNotFoundError) as exc:
            logger.warning("Sprite failed to spawn (%s); continuing without it", exc)
            return cls(handle=None)
        return cls(handle=handle)

    def terminate(self, *, grace_s: float) -> None:
        if self.handle is None:
            return
        terminate(self.handle, grace_s=grace_s)
```

- [ ] **Step 4: Run tests**

```
uv run pytest tests/unit/supervisor/test_sprite.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/supervisor/test_sprite.py src/voice_commander/supervisor/sprite.py
git commit -m "feat(supervisor): SpriteChild best-effort spawn/terminate"
```

---

## Task 5: Restart loop (TDD)

**Files:**
- Create: `src/voice_commander/supervisor/loop.py`
- Test: `tests/unit/supervisor/test_loop.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/supervisor/test_loop.py`:
```python
"""Supervisor restart loop."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from voice_commander.supervisor.exit_codes import EXIT_CLEAN, EXIT_RESTART
from voice_commander.supervisor.loop import run


def _scripted_daemon_factory(exit_codes: list[int]) -> MagicMock:
    """Return a factory whose successive children exit with the given codes."""
    queue = list(exit_codes)
    factory = MagicMock(name="daemon_factory")

    def _make_child() -> MagicMock:
        child = MagicMock(name="DaemonChild")
        child.wait.return_value = queue.pop(0)
        return child

    factory.side_effect = _make_child
    return factory


def test_clean_exit_returns_zero() -> None:
    sprite = MagicMock(name="SpriteChild")
    daemon_factory = _scripted_daemon_factory([EXIT_CLEAN])
    code = run(sprite=sprite, daemon_factory=daemon_factory)
    assert code == 0
    assert daemon_factory.call_count == 1
    sprite.terminate.assert_called_once()


def test_restart_exit_respawns() -> None:
    sprite = MagicMock(name="SpriteChild")
    daemon_factory = _scripted_daemon_factory([EXIT_RESTART, EXIT_RESTART, EXIT_CLEAN])
    code = run(sprite=sprite, daemon_factory=daemon_factory)
    assert code == 0
    assert daemon_factory.call_count == 3
    sprite.terminate.assert_called_once()


def test_crash_exit_propagates_code() -> None:
    sprite = MagicMock(name="SpriteChild")
    daemon_factory = _scripted_daemon_factory([1])
    code = run(sprite=sprite, daemon_factory=daemon_factory)
    assert code == 1
    sprite.terminate.assert_called_once()


def test_sprite_terminated_even_on_keyboard_interrupt() -> None:
    sprite = MagicMock(name="SpriteChild")

    def _make_child() -> MagicMock:
        child = MagicMock()
        child.wait.side_effect = KeyboardInterrupt
        return child

    daemon_factory = MagicMock(side_effect=_make_child)
    with pytest.raises(KeyboardInterrupt):
        run(sprite=sprite, daemon_factory=daemon_factory)
    sprite.terminate.assert_called_once()


def test_keyboard_interrupt_terminates_running_daemon() -> None:
    """Ctrl+C must propagate to the daemon, not just the supervisor."""
    sprite = MagicMock(name="SpriteChild")
    daemon = MagicMock(name="DaemonChild")
    daemon.wait.side_effect = KeyboardInterrupt
    daemon_factory = MagicMock(return_value=daemon)
    with pytest.raises(KeyboardInterrupt):
        run(sprite=sprite, daemon_factory=daemon_factory)
    daemon.terminate.assert_called_once()
```

- [ ] **Step 2: Run tests, expect failure**

```
uv run pytest tests/unit/supervisor/test_loop.py -v
```
Expected: ImportError on `loop.run`.

- [ ] **Step 3: Implement `loop.py`**

`src/voice_commander/supervisor/loop.py`:
```python
"""Supervisor restart loop.

Pure logic, no subprocess calls. Tests inject ``sprite`` and
``daemon_factory`` mocks directly. The CLI wires real implementations
in ``cli.py``.
"""

from __future__ import annotations

import logging
from typing import Callable, Protocol

from .exit_codes import EXIT_CLEAN, EXIT_RESTART

logger = logging.getLogger(__name__)

SHUTDOWN_GRACE_S = 5.0


class _ChildProto(Protocol):
    def wait(self) -> int: ...
    def terminate(self, *, grace_s: float) -> None: ...


class _SpriteProto(Protocol):
    def terminate(self, *, grace_s: float) -> None: ...


def run(
    *,
    sprite: _SpriteProto,
    daemon_factory: Callable[[], _ChildProto],
) -> int:
    """Spawn the daemon in a loop, branching on its exit code.

    Returns the supervisor's own exit code:

    - 0 — daemon exited cleanly.
    - <code> — daemon crashed with that non-zero, non-75 code.

    Sprite is terminated once on the way out.
    """
    try:
        while True:
            daemon = daemon_factory()
            try:
                code = daemon.wait()
            except KeyboardInterrupt:
                logger.info("KeyboardInterrupt; terminating daemon")
                daemon.terminate(grace_s=SHUTDOWN_GRACE_S)
                raise

            if code == EXIT_RESTART:
                logger.info("Daemon requested restart (code=%d); respawning", code)
                continue
            if code == EXIT_CLEAN:
                logger.info("Daemon exited cleanly (code=%d)", code)
                return 0
            logger.error("Daemon crashed (code=%d); supervisor exiting", code)
            return code
    finally:
        sprite.terminate(grace_s=SHUTDOWN_GRACE_S)
```

- [ ] **Step 4: Run tests**

```
uv run pytest tests/unit/supervisor/test_loop.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/supervisor/test_loop.py src/voice_commander/supervisor/loop.py
git commit -m "feat(supervisor): restart loop with exit-code branching"
```

---

# Phase 4 — `cli.py` + console-script wiring

## Task 6: `cli.py` (TDD where practical)

**Files:**
- Create: `src/voice_commander/supervisor/cli.py`
- Create: `src/voice_commander/supervisor/__main__.py`
- Modify: `src/voice_commander/supervisor/__init__.py`
- Modify: `pyproject.toml`
- Test: `tests/unit/supervisor/test_cli.py`

- [ ] **Step 1: Write failing tests**

`tests/unit/supervisor/test_cli.py`:
```python
"""Supervisor CLI."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from voice_commander.single_instance import AlreadyRunning
from voice_commander.supervisor.cli import _build_parser, main


def test_parser_defaults() -> None:
    args = _build_parser().parse_args([])
    assert args.no_sprite is False


def test_parser_no_sprite() -> None:
    args = _build_parser().parse_args(["--no-sprite"])
    assert args.no_sprite is True


def test_main_returns_loop_exit_code(tmp_path: Path) -> None:
    """main() returns whatever loop.run returns."""
    with (
        patch("voice_commander.supervisor.cli.SingleInstanceLock"),
        patch("voice_commander.supervisor.cli.SpriteChild"),
        patch("voice_commander.supervisor.cli.run", return_value=42),
        patch("voice_commander.supervisor.cli._spawn_daemon"),
    ):
        rc = main(["--no-sprite"])
    assert rc == 42


def test_main_already_running_exits_one() -> None:
    fake_lock = patch(
        "voice_commander.supervisor.cli.SingleInstanceLock"
    ).start()
    fake_lock.return_value.acquire.side_effect = AlreadyRunning("pid=123")
    try:
        rc = main([])
        assert rc == 1
    finally:
        patch.stopall()
```

- [ ] **Step 2: Run tests, expect failure**

```
uv run pytest tests/unit/supervisor/test_cli.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement `cli.py`**

`src/voice_commander/supervisor/cli.py`:
```python
"""Supervisor entry point.

Owns:

* arg parsing (``--no-sprite``)
* logging configuration (separate file from the daemon)
* single-instance lock at ``outputs/.supervisor.lock``
* sprite spawn (best-effort) and daemon factory
* delegation to ``loop.run``
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..config import Config
from ..single_instance import AlreadyRunning, SingleInstanceLock
from .loop import run
from .process import ChildHandle, spawn, terminate
from .sprite import SpriteChild

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voice-commander-supervisor",
        description="Long-lived parent process for voice-commander.",
    )
    parser.add_argument(
        "--no-sprite",
        action="store_true",
        help="Skip the sprite companion (still spawns the daemon).",
    )
    return parser


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _configure_logging() -> None:
    cfg = Config.load(Path("config.toml"))
    level_name = os.environ.get("VC_LOG_LEVEL", cfg.logging.level).upper()
    level = getattr(logging, level_name, logging.INFO)

    log_path = Path(cfg.logging.file).with_name("supervisor.log")
    handler = RotatingFileHandler(
        log_path,
        maxBytes=cfg.logging.max_bytes,
        backupCount=cfg.logging.backup_count,
        encoding="utf-8",
    )
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(threadName)s %(name)s %(levelname)s: %(message)s",
        handlers=[handler, logging.StreamHandler()],
        force=True,
    )


# ---------------------------------------------------------------------------
# Daemon factory (production path)
# ---------------------------------------------------------------------------


class _DaemonChild:
    """Adapts ``ChildHandle`` to the loop's ``_ChildProto``."""

    def __init__(self, handle: ChildHandle) -> None:
        self._handle = handle

    def wait(self) -> int:
        return self._handle.wait()

    def terminate(self, *, grace_s: float) -> None:
        terminate(self._handle, grace_s=grace_s)


def _spawn_daemon() -> _DaemonChild:
    argv = [sys.executable, "-m", "voice_commander"]
    handle = spawn(argv, name="voice-commander", env_extra={"VC_SUPERVISED": "1"})
    return _DaemonChild(handle)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    _configure_logging()

    lock = SingleInstanceLock(Path("outputs/.supervisor.lock"))
    try:
        lock.acquire()
    except AlreadyRunning as exc:
        logger.error("Supervisor already running: %s", exc)
        return 1

    try:
        sprite = SpriteChild(handle=None) if args.no_sprite else SpriteChild.spawn()
        return run(sprite=sprite, daemon_factory=_spawn_daemon)
    except KeyboardInterrupt:
        logger.info("Ctrl+C received; shutdown complete")
        return 0
    finally:
        lock.release()
```

- [ ] **Step 4: Add `__main__.py` and update `__init__.py`**

`src/voice_commander/supervisor/__main__.py`:
```python
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
```

`src/voice_commander/supervisor/__init__.py` (replace contents):
```python
"""Long-lived parent process that owns the daemon + sprite lifecycle."""

from __future__ import annotations

from .cli import main

__all__ = ["main"]
```

- [ ] **Step 5: Add console script in `pyproject.toml`**

In `[project.scripts]`, add the third line:
```toml
[project.scripts]
voice-commander = "voice_commander.__main__:main"
voice-sprite = "voice_sprite.__main__:main"
voice-commander-supervisor = "voice_commander.supervisor:main"
```

- [ ] **Step 6: Resync uv**

```
uv sync
```
Expected: no errors; the new console script is registered.

- [ ] **Step 7: Run all supervisor unit tests**

```
uv run pytest tests/unit/supervisor/ -v
```
Expected: all green (≈ 14 tests).

- [ ] **Step 8: Smoke-test the entry point**

```
uv run voice-commander-supervisor --help
```
Expected: argparse help text containing `--no-sprite`.

- [ ] **Step 9: Commit**

```bash
git add src/voice_commander/supervisor/__main__.py src/voice_commander/supervisor/__init__.py src/voice_commander/supervisor/cli.py tests/unit/supervisor/test_cli.py pyproject.toml uv.lock
git commit -m "feat(supervisor): cli + console-script entry point"
```

---

## Task 7: Integration test against fake daemon/sprite

**Files:**
- Create: `scripts/_fake_daemon.py`
- Create: `scripts/_fake_sprite.py`
- Create: `tests/integration/test_supervisor_e2e.py`

- [ ] **Step 1: Write the fake daemon**

`scripts/_fake_daemon.py`:
```python
"""Test fake — sleeps briefly, then exits with the code from a fixture file.

Usage::

    python scripts/_fake_daemon.py <fixture-file>

The fixture file is read on each invocation; the first line is consumed and
written back. So a fixture of `75\n75\n0\n` produces three successive runs
that exit 75, 75, 0.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path


def main() -> int:
    fixture = Path(sys.argv[1])
    lines = fixture.read_text().splitlines()
    code = int(lines[0])
    fixture.write_text("\n".join(lines[1:]) + ("\n" if lines[1:] else ""))
    time.sleep(0.1)  # represent real work
    return code


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Write the fake sprite**

`scripts/_fake_sprite.py`:
```python
"""Test fake — writes its own pid then sleeps until killed."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def main() -> int:
    pid_path = Path(sys.argv[1])
    pid_path.write_text(str(os.getpid()))
    while True:
        time.sleep(0.5)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Write the integration test**

`tests/integration/test_supervisor_e2e.py`:
```python
"""Supervisor end-to-end: scripted exit codes drive restart loop.

We bypass the real daemon spawn by patching ``_spawn_daemon`` in the CLI
module with a factory that runs ``scripts/_fake_daemon.py``. Sprite is
likewise stubbed via ``scripts/_fake_sprite.py``. Both are real
subprocesses, so the test exercises the actual ``process.py`` helpers.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FAKE_DAEMON = REPO_ROOT / "scripts" / "_fake_daemon.py"
FAKE_SPRITE = REPO_ROOT / "scripts" / "_fake_sprite.py"


@pytest.mark.integration
def test_supervisor_respawns_then_exits_clean(tmp_path: Path) -> None:
    fixture = tmp_path / "codes.txt"
    fixture.write_text("75\n75\n0\n")
    sprite_pid_file = tmp_path / "sprite.pid"

    code = _run_supervisor_with_fakes(fixture, sprite_pid_file)
    assert code == 0
    # Fake-daemon consumed all 3 lines.
    assert fixture.read_text().strip() == ""
    # Sprite was terminated by supervisor on exit.
    pid = int(sprite_pid_file.read_text())
    assert not _pid_alive(pid), f"sprite pid={pid} still alive"


@pytest.mark.integration
def test_supervisor_propagates_crash_code(tmp_path: Path) -> None:
    fixture = tmp_path / "codes.txt"
    fixture.write_text("9\n")
    sprite_pid_file = tmp_path / "sprite.pid"

    code = _run_supervisor_with_fakes(fixture, sprite_pid_file)
    assert code == 9


def _run_supervisor_with_fakes(fixture: Path, sprite_pid_file: Path) -> int:
    """Run the supervisor with monkey-patched daemon + sprite spawn."""
    bootstrap = (
        "import sys, runpy; "
        "from unittest.mock import patch; "
        "from voice_commander.supervisor import cli; "
        "from voice_commander.supervisor import sprite as sprite_mod; "
        "from voice_commander.supervisor.process import spawn; "
        f"DAEMON_ARGV = [sys.executable, r'{FAKE_DAEMON}', r'{fixture}']; "
        f"SPRITE_ARGV = [sys.executable, r'{FAKE_SPRITE}', r'{sprite_pid_file}']; "
        "from voice_commander.supervisor.cli import _DaemonChild; "
        "def fake_daemon(): "
        "    return _DaemonChild(spawn(DAEMON_ARGV, name='fake-daemon', env_extra={'VC_SUPERVISED': '1'})); "
        "def fake_sprite_spawn(cls): "
        "    return cls(handle=spawn(SPRITE_ARGV, name='fake-sprite')); "
        "with patch.object(cli, '_spawn_daemon', fake_daemon), "
        "     patch.object(sprite_mod.SpriteChild, 'spawn', classmethod(fake_sprite_spawn)): "
        "    sys.exit(cli.main([]))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", bootstrap],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.returncode


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        h = kernel32.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        kernel32.CloseHandle(ctypes.c_void_p(h))
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
```

- [ ] **Step 4: Run integration tests**

```
uv run pytest tests/integration/test_supervisor_e2e.py -v -m integration
```
Expected: 2 passed (≈ 5 s).

If sprite-pid-alive check is flaky on Windows due to timing, increase the post-exit settle time inside the bootstrap or simply skip the alive check on the second test.

- [ ] **Step 5: Commit**

```bash
git add scripts/_fake_daemon.py scripts/_fake_sprite.py tests/integration/test_supervisor_e2e.py
git commit -m "test(supervisor): integration test with fake daemon + sprite"
```

---

# Phase 5 — Web layer wiring

## Task 8: `/restart` route handles `RestartUnavailable`

**Files:**
- Modify: `src/voice_commander/web/admin.py:377-383`
- Test: extend `tests/integration/test_admin_routes.py`

- [ ] **Step 1: Inspect existing test file**

```
uv run pytest tests/integration/test_admin_routes.py -v --collect-only
```
Note the existing fixtures so the new test reuses them.

- [ ] **Step 2: Write failing tests**

Add to `tests/integration/test_admin_routes.py` (append at end of file):
```python
import pytest


@pytest.mark.integration
def test_restart_returns_503_when_unsupervised(client, monkeypatch):
    monkeypatch.delenv("VC_SUPERVISED", raising=False)
    resp = client.post("/restart", headers={"HX-Request": "true"})
    assert resp.status_code == 503
    assert "supervisor" in resp.json()["error"].lower()


@pytest.mark.integration
def test_restart_returns_202_when_supervised(client, monkeypatch):
    monkeypatch.setenv("VC_SUPERVISED", "1")
    # Patch request_restart so the test process doesn't actually exit.
    from voice_commander.commands import restart as restart_mod
    monkeypatch.setattr(restart_mod, "request_restart", lambda delay_s=0.5: None)
    resp = client.post("/restart", headers={"HX-Request": "true"})
    assert resp.status_code == 202
    assert resp.json()["status"] == "restarting"
```

If `client` fixture name differs in this file, adapt to whatever it uses (look at the top of the file or `tests/integration/conftest.py`).

- [ ] **Step 3: Run tests, expect failure**

```
uv run pytest tests/integration/test_admin_routes.py::test_restart_returns_503_when_unsupervised tests/integration/test_admin_routes.py::test_restart_returns_202_when_supervised -v
```
Expected: 503 test fails (current handler always returns 200), 202 test fails (current code returns 200 not 202).

- [ ] **Step 4: Update `/restart` handler**

In `src/voice_commander/web/admin.py`, replace the existing `restart` handler (~line 377-382):
```python
    @app.post("/restart")
    async def restart() -> JSONResponse:
        from ..commands.restart import RestartUnavailable, request_restart

        try:
            request_restart()
        except RestartUnavailable as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        return JSONResponse({"status": "restarting"}, status_code=202)
```

- [ ] **Step 5: Run tests, expect pass**

```
uv run pytest tests/integration/test_admin_routes.py -v
```
Expected: all green (existing tests + new ones).

- [ ] **Step 6: Commit**

```bash
git add src/voice_commander/web/admin.py tests/integration/test_admin_routes.py
git commit -m "feat(web): /restart returns 503 RestartUnavailable when unsupervised"
```

---

## Task 9: Per-field "Restart required" banner

**Files:**
- Modify: `src/voice_commander/web/admin.py` (config_save handler ~line 340-375)

- [ ] **Step 1: Add the restart-required key set near the top of `admin.py`**

Insert after the existing imports, before `attach_admin_routes`:
```python
# Config keys whose changes only take effect after a daemon restart.
# All other keys hot-reload via the existing reload mechanisms.
_RESTART_REQUIRED_KEYS: frozenset[str] = frozenset({
    "audio.device",
    "audio.device_resample_rate",
    "transcription.model_size",
    "transcription.compute_type",
    "hotkey.key",
    "hotkey.mute_key",
    "web.port",
})
```

- [ ] **Step 2: Update `config_save` to detect restart-required changes**

Replace the body of `config_save` (the function around line 340) with:
```python
    @app.post("/config", response_class=HTMLResponse)
    async def config_save(
        request: Request,
        llm_endpoint_url: str = Form(default=""),
        llm_model_id: str = Form(default=""),
        llm_default_browser: str = Form(default=""),
        llm_timeout_ms: int = Form(default=1200),
        audio_device: int = Form(default=-1),
        transcription_model_size: str = Form(default="small.en"),
        transcription_min_confidence: float = Form(default=0.3),
    ) -> HTMLResponse:
        updates: dict[str, dict[str, Any]] = {
            "llm": {
                "endpoint_url": llm_endpoint_url,
                "model_id": llm_model_id,
                "default_browser": llm_default_browser,
                "timeout_ms": int(llm_timeout_ms),
            },
            "audio": {"device": int(audio_device)},
            "transcription": {
                "model_size": transcription_model_size,
                "min_confidence": float(transcription_min_confidence),
            },
        }
        try:
            update_user_config(config_path, updates)
        except ConfigWriteError as exc:
            return HTMLResponse(content=str(exc), status_code=400)
        _publish("config_saved", {"path": str(config_path)})

        # Detect restart-required keys among the saved fields.
        saved_keys = {
            f"{section}.{key}"
            for section, fields in updates.items()
            for key in fields
        }
        needs_restart = bool(saved_keys & _RESTART_REQUIRED_KEYS)

        if needs_restart:
            banner = (
                '<div class="text-amber-300 text-sm">'
                "Saved. Some changes require a restart. "
                '<button hx-post="/restart" hx-swap="none" '
                'class="underline hover:text-amber-200">Restart daemon</button>'
                " to apply."
                "</div>"
            )
        else:
            banner = (
                '<div class="text-green-400 text-sm">'
                "Saved. Changes applied immediately."
                "</div>"
            )
        return HTMLResponse(content=banner)
```

- [ ] **Step 3: Add a unit test**

Append to `tests/integration/test_admin_routes.py`:
```python
@pytest.mark.integration
def test_config_save_restart_required_banner(client):
    resp = client.post(
        "/config",
        headers={"HX-Request": "true"},
        data={
            "llm_endpoint_url": "http://x",
            "llm_model_id": "m",
            "llm_default_browser": "chrome",
            "llm_timeout_ms": 1200,
            "audio_device": 3,
            "transcription_model_size": "small.en",
            "transcription_min_confidence": 0.3,
        },
    )
    assert resp.status_code == 200
    assert "require a restart" in resp.text


@pytest.mark.integration
def test_config_save_hot_reload_banner(client):
    resp = client.post(
        "/config",
        headers={"HX-Request": "true"},
        data={
            "llm_endpoint_url": "http://x",
            "llm_model_id": "m",
            "llm_default_browser": "chrome",
            "llm_timeout_ms": 1200,
            "audio_device": -1,            # default sentinel — no change
            "transcription_model_size": "small.en",
            "transcription_min_confidence": 0.4,
        },
    )
    assert resp.status_code == 200
    assert "applied immediately" in resp.text
```

If the `audio_device=-1` default actually triggers a config write, adjust the test to a value that doesn't intersect `_RESTART_REQUIRED_KEYS`. The point of the test is the banner-string contract; refine the inputs in code review if needed.

- [ ] **Step 4: Run tests**

```
uv run pytest tests/integration/test_admin_routes.py -v
```
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/voice_commander/web/admin.py tests/integration/test_admin_routes.py
git commit -m "feat(web): config save shows restart-required banner for hot-cold fields"
```

---

# Phase 6 — `start.ps1`, ADR, docs, manual validation

## Task 10: Update `start.ps1`

**Files:**
- Modify: `start.ps1`

- [ ] **Step 1: Replace `Start-VoiceDaemon` and `Start-VoiceWithUI`**

In `start.ps1`, replace the existing `Start-VoiceDaemon` function (around line 434-446) and `Start-VoiceWithUI` function (around line 448-502) with:

```powershell
function Start-VoiceSupervisor {
    <#
    .SYNOPSIS
        Launch `uv run voice-commander-supervisor` and return its exit code.

    .DESCRIPTION
        The supervisor owns the daemon and sprite lifecycle. Stdin/stdout are
        not redirected so Ctrl+C reaches the supervisor (which propagates to
        its children). Returns the supervisor exit code as [int].
    #>
    $supArgs = @()
    if ($NoSprite) { $supArgs += '--no-sprite' }
    Write-Verbose "Launching supervisor: uv run voice-commander-supervisor $($supArgs -join ' ')"
    uv run voice-commander-supervisor @supArgs
    return $LASTEXITCODE
}

function Start-VoiceWithUI {
    <#
    .SYNOPSIS
        Set web UI env vars, optionally open browser, then launch the supervisor.

    .DESCRIPTION
        Applies VOICE_COMMANDER_WEB_DISABLED and VOICE_COMMANDER_WEB_PORT from
        the -NoUI and -UIPort parameters before calling Start-VoiceSupervisor.
        When the web UI is enabled and -NoOpenBrowser is not set, a background
        job opens the browser 1.5 s after this function is called.
        Sprite spawn/teardown is now handled by the Python supervisor.
    #>

    if ($NoUI) {
        $env:VOICE_COMMANDER_WEB_DISABLED = '1'
    }
    if ($UIPort -gt 0) {
        $env:VOICE_COMMANDER_WEB_PORT = $UIPort.ToString()
    }

    if (-not $NoUI -and -not $NoOpenBrowser) {
        $webPort = if ($UIPort -gt 0) { $UIPort } else { 8765 }
        $webUrl  = "http://127.0.0.1:$webPort"
        Write-Verbose "Scheduling browser open: $webUrl (after 1500 ms)"
        Start-Job -ScriptBlock {
            Start-Sleep -Milliseconds 1500
            Start-Process $using:webUrl
        } | Out-Null
    }

    return Start-VoiceSupervisor
}
```

- [ ] **Step 2: Bump phase string**

In `start.ps1`, change:
```powershell
$script:PhaseString = '  Phase 6: LLM-only routing (9 primitives, unconditional LLM)'
```
to:
```powershell
$script:PhaseString = '  Phase 7: supervisor process (daemon + sprite under one parent)'
```

- [ ] **Step 3: Smoke-test `start.ps1 -ListDevices` (no daemon launch)**

```
pwsh -File ./start.ps1 -ListDevices
```
Expected: device table prints, exit 0. (This path doesn't touch the new function.)

- [ ] **Step 4: Smoke-test `start.ps1` help / dry path**

```
pwsh -NoProfile -Command "& { . ./start.ps1; Get-Command Start-VoiceSupervisor }" -WhatIf
```
This is best-effort; the goal is just to confirm the function definition parses.

- [ ] **Step 5: Commit**

```bash
git add start.ps1
git commit -m "feat(launcher): start.ps1 calls supervisor; sprite spawn moved to Python"
```

---

## Task 11: ADR 0058

**Files:**
- Create: `docs/decisions/0058-supervisor-process-owns-lifecycle.md`

- [ ] **Step 1: Write the ADR**

`docs/decisions/0058-supervisor-process-owns-lifecycle.md`:
```markdown
# ADR 0058 — Supervisor process owns daemon + sprite lifecycle

**Date:** 2026-04-25
**Status:** Accepted
**Supersedes:** the in-daemon detach-spawn restart in `commands/restart.py`.

## Context

`start.ps1` launched the daemon and the sprite as siblings. The "Restart" button in the web UI worked by having the dying daemon spawn a detached replacement and `os._exit(0)`. From PowerShell's perspective this looked like a clean shutdown — PS killed the sprite, exited, and orphaned the freshly spawned daemon from any process supervisor.

We want a single long-lived parent that survives daemon restarts.

## Decision

Introduce a Python supervisor (`voice_commander.supervisor`, console script `voice-commander-supervisor`) that:

1. Acquires its own single-instance lock at `outputs/.supervisor.lock`.
2. Spawns the sprite once (best-effort) — sprite survives daemon restarts.
3. Spawns the daemon with `VC_SUPERVISED=1` set in its environment.
4. Branches on the daemon's exit code:
   * `0` (`EXIT_CLEAN`) — terminate sprite, exit 0.
   * `75` (`EXIT_RESTART`) — respawn daemon, sprite untouched.
   * any other — terminate sprite, exit with the same code (no auto-restart).

The daemon's `commands/restart.py:schedule_restart` (detach-spawn-and-exit) is replaced by `request_restart` (graceful exit 75). When `VC_SUPERVISED` is unset, `request_restart` raises `RestartUnavailable` and the `/restart` route returns 503.

`start.ps1` keeps the audio-device TUI but its terminal call swaps from `uv run voice-commander` to `uv run voice-commander-supervisor`. The sprite-spawn block in `Start-VoiceWithUI` is deleted; the supervisor owns it now.

Per-field config policy: cheap fields hot-reload as before. Expensive fields (`audio.device`, `audio.device_resample_rate`, `transcription.model_size`, `transcription.compute_type`, `hotkey.key`, `hotkey.mute_key`, `web.port`) are flagged in `_RESTART_REQUIRED_KEYS` and trigger a banner in the UI prompting the user to click Restart.

## Consequences

* The web UI's Restart button now requires the supervisor; running the daemon directly (`uv run voice-commander`) and clicking Restart returns 503 with a friendly message.
* No auto-restart on crash — supervisor surfaces the daemon's exit code and stops. Recovery is via `start.ps1`. We can revisit if real-world flakiness justifies it (separate ADR).
* Two locks (`.daemon.lock` and `.supervisor.lock`) coexist; both go in `outputs/` and are gitignored.
* Sprite reconnects its SSE stream when the daemon comes back; small change to its existing reconnect-with-backoff logic if not already present.

## Alternatives considered

* **Watchdog-only supervisor (web UI stays in daemon).** Picked. Smallest diff, web UI flickers briefly during restart but the sprite stays put.
* **Web UI in supervisor.** Rejected for v1 — splits `web/` away from daemon wiring; bigger refactor than warranted.
* **Localhost control socket between daemon and supervisor.** Rejected — magic exit codes are simpler and cover every use case we have today.
```

- [ ] **Step 2: Add row to technical-decisions index**

Append to `docs/agents/technical-decisions.md` at the end of the table (find the last row, add the new one underneath):
```markdown
| Daemon + sprite lifecycle owned by Python supervisor; daemon restarts via graceful exit 75 | [ADR 0058](../decisions/0058-supervisor-process-owns-lifecycle.md) |
```

(Adapt to the existing column shape if it differs — open the file and copy the most recent row's format.)

- [ ] **Step 3: Commit**

```bash
git add docs/decisions/0058-supervisor-process-owns-lifecycle.md docs/agents/technical-decisions.md
git commit -m "docs(adr): 0058 supervisor process owns daemon + sprite lifecycle"
```

---

## Task 12: Architecture diagram + README + .gitignore

**Files:**
- Modify: `docs/architecture.md` (process-tree diagram)
- Modify: `README.md` (Restart-button caveat)
- Modify: `.gitignore` (`outputs/.supervisor.lock`)

- [ ] **Step 1: Update architecture diagram**

Open `docs/architecture.md`. Find the existing process-tree section (search for "Five long-lived threads" or similar). Add a new sub-section above it titled "Process tree":
```markdown
## Process tree

```
start.ps1                         (audio-device TUI)
└── voice-commander-supervisor    (Python supervisor; long-lived parent)
    ├── voice-sprite              (subprocess; persistent across daemon restarts)
    └── voice-commander           (daemon; respawned on graceful exit 75)
```

The supervisor owns both children. The daemon's `/restart` web route triggers `os._exit(75)`; the supervisor's wait loop sees code 75 and respawns. Sprite is untouched, its SSE stream reconnects when the new daemon binds the web port.
```

- [ ] **Step 2: Update README**

In `README.md`, find the section that mentions launching (search for `start.ps1`). Add a sentence:
```markdown
> The **Restart daemon** button in the web UI requires `start.ps1` (or `voice-commander-supervisor` directly). Running `uv run voice-commander` standalone is supported but the Restart button will return 503 in that mode.
```

- [ ] **Step 3: Update `.gitignore`**

Verify `outputs/` is already ignored. If only specific files inside `outputs/` are ignored, add the supervisor lock:
```
outputs/.supervisor.lock
```

If `outputs/*` is ignored already (likely), skip this step.

- [ ] **Step 4: Update `CLAUDE.md` agent reference map (optional)**

If the agent reference map in `CLAUDE.md` references the old single-process model, update the architecture-at-a-glance ASCII diagram to add a `Supervisor` box. Defer if the diagram is generic enough.

- [ ] **Step 5: Commit**

```bash
git add docs/architecture.md README.md .gitignore
git commit -m "docs: process-tree diagram + restart-button caveat for supervisor"
```

---

## Task 13: Manual validation

This is the project's phase-gate per `CLAUDE.md`. Cannot be automated.

- [ ] **Step 1: Cold launch**

```
.\start.ps1
```
Expected:
* Audio TUI works.
* Supervisor banner / log line appears.
* Sprite shows on screen.
* Browser opens to `http://127.0.0.1:8765`.
* Daemon log + supervisor log both written.

- [ ] **Step 2: Click Restart in the web UI**

Click "Restart daemon" in the config screen.

Expected:
* Browser flips to "restarting…" toast (or 202 response visible in network tab).
* Daemon process pid changes (verify with `Get-Process voice-commander, voice-commander-supervisor, voice-sprite` before/after).
* Sprite pid is unchanged.
* Browser re-establishes SSE within ~5 s; sprite resumes mirroring daemon state.

- [ ] **Step 3: Edit a hot-reload field, save**

In the config screen, change `transcription.min_confidence` and click Save.

Expected: green banner "Saved. Changes applied immediately." Field takes effect on next utterance without a restart.

- [ ] **Step 4: Edit a restart-required field, save**

In the config screen, change `transcription.model_size` (e.g. small.en → tiny.en) and click Save.

Expected: amber banner "Saved. Some changes require a restart." with a Restart button. Click it; verify model reload happens.

- [ ] **Step 5: Crash simulation**

In a separate console:
```
Stop-Process -Name voice-commander -Force
```

Expected:
* Supervisor logs the crash exit code, terminates sprite, exits.
* PS prints the existing "exited with error" branch.
* No orphan processes (`Get-Process voice-commander, voice-commander-supervisor, voice-sprite` returns empty).

- [ ] **Step 6: Standalone-daemon Restart guard**

In a fresh shell:
```
uv run voice-commander
```
Open the web UI, click Restart.

Expected: 503 response with "Restart requires the supervisor."

- [ ] **Step 7: Ctrl+C cleanup**

`.\start.ps1`, then Ctrl+C in the PS console.

Expected:
* Supervisor logs "Ctrl+C received".
* Daemon and sprite terminated cleanly within `SHUTDOWN_GRACE_S`.
* No orphans.
* PS exit code is 0 (or 130 / -1073741510; supervisor maps Ctrl+C → 0 today).

- [ ] **Step 8: Run full test suite once more**

```
uv run pytest -v
```
Expected: all green; coverage hasn't dropped below the 80% gate.

- [ ] **Step 9: Final commit (manual validation log, optional)**

If there are any docs tweaks discovered during validation, fold them into a final commit.

---

## Self-Review (filled out)

**Spec coverage:**

| Spec section | Task |
|---|---|
| Decision 1 (web UI in daemon) | preserved by inaction; covered. |
| Decision 2 (exit codes) | Tasks 1, 2, 5. |
| Decision 3 (sprite as supervisor child) | Tasks 4, 6. |
| Decision 4 (hybrid PS + Python) | Tasks 6, 10. |
| Decision 5 (no auto-restart on crash) | Task 5 (tested in `test_crash_exit_propagates_code`). |
| Decision 6 (per-field config policy) | Task 9. |
| Decision 7 (`start.ps1` thin wrapper) | Task 10. |
| Architecture / process tree | Task 12. |
| Boot data flow | Tasks 6, 10. |
| Restart data flow | Tasks 2, 8. |
| Crash data flow | Task 5. |
| Ctrl+C data flow | Task 5 (`test_keyboard_interrupt_terminates_running_daemon`), Task 13 step 7. |
| Per-field config policy | Task 9. |
| Error handling table | Tasks 2, 4, 5, 6, 8. |
| Testing strategy | Tasks 2, 3, 4, 5, 6, 7, 8, 9, 13. |
| ADR | Task 11. |
| Migration / cleanup | Tasks 10, 11, 12. |

**Placeholder scan:** No "TBD"s, no "implement appropriately"s, every code step has full code, every test step has full test code.

**Type consistency:** `ChildHandle` → `_DaemonChild` → `_ChildProto`; the loop accepts the protocol, `cli._spawn_daemon` returns `_DaemonChild`, and `SpriteChild` matches `_SpriteProto` directly. `EXIT_CLEAN`/`EXIT_RESTART` referenced consistently. `request_restart` and `RestartUnavailable` match across the daemon, the test, and the `/restart` handler.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-25-supervisor-process.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks.
2. **Inline Execution** — execute in this session via executing-plans, batch with checkpoints.
