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
import textwrap
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
    # Allow brief settle time for OS-level reap.
    time.sleep(0.3)
    assert not _pid_alive(pid), f"sprite pid={pid} still alive"


@pytest.mark.integration
def test_supervisor_propagates_crash_code(tmp_path: Path) -> None:
    fixture = tmp_path / "codes.txt"
    fixture.write_text("9\n")
    sprite_pid_file = tmp_path / "sprite.pid"

    code = _run_supervisor_with_fakes(fixture, sprite_pid_file)
    assert code == 9


def _run_supervisor_with_fakes(fixture: Path, sprite_pid_file: Path) -> int:
    """Run the supervisor with monkey-patched daemon + sprite spawn.

    We write a small Python script to a temp file to avoid all quoting/escaping
    issues with backslash-heavy Windows paths in ``subprocess.run(..., -c ...)``.
    """
    # Write the bootstrap script to a temp file next to the fixture.
    script = fixture.parent / "_bootstrap.py"
    # Use forward-slashes in the embedded paths to avoid backslash escape issues.
    daemon_path = FAKE_DAEMON.as_posix()
    sprite_path = FAKE_SPRITE.as_posix()
    fixture_path = fixture.as_posix()
    sprite_pid_path = sprite_pid_file.as_posix()

    script.write_text(
        textwrap.dedent(f"""\
            import sys
            from pathlib import Path
            from unittest.mock import patch
            from voice_commander.supervisor import cli
            from voice_commander.supervisor import sprite as sprite_mod
            from voice_commander.supervisor.process import spawn
            from voice_commander.supervisor.cli import _DaemonChild

            DAEMON_ARGV = [sys.executable, r"{daemon_path}", r"{fixture_path}"]
            SPRITE_ARGV = [sys.executable, r"{sprite_path}", r"{sprite_pid_path}"]


            def fake_daemon():
                return _DaemonChild(
                    spawn(DAEMON_ARGV, name="fake-daemon", env_extra={{"VC_SUPERVISED": "1"}})
                )


            def fake_sprite_spawn(cls):
                return cls(handle=spawn(SPRITE_ARGV, name="fake-sprite"))


            with (
                patch.object(cli, "_spawn_daemon", fake_daemon),
                patch.object(sprite_mod.SpriteChild, "spawn", classmethod(fake_sprite_spawn)),
            ):
                sys.exit(cli.main([]))
        """),
        encoding="utf-8",
    )

    state_dir = fixture.parent / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["VC_STATE_DIR"] = str(state_dir)

    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
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
