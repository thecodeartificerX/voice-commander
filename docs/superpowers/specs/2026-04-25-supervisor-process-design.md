# Supervisor Process — Design Spec

**Date:** 2026-04-25
**Status:** Draft → ready for plan
**Replaces:** the in-daemon `commands/restart.py` self-respawn pattern.

## Why

Today the voice-commander daemon owns the web UI, the voice pipeline, the EventBus, and (transitively, via `commands/restart.py`) its own restart logic. `start.ps1` launches the daemon as a foreground child, spawns the sprite as a sibling, and exits when the daemon exits.

The "restart" button in the UI works by having the dying daemon detach-spawn its replacement and `os._exit(0)`. From `start.ps1`'s perspective this looks like a clean shutdown: PowerShell tears down the sprite tree and exits, leaving the freshly spawned daemon orphaned from any process supervisor and running with no sprite companion.

The user wants a single long-lived parent that:

1. Owns the daemon as a managed child and respawns it on demand.
2. Owns the sprite as a managed child that survives daemon restarts.
3. Cleans up everything on Ctrl+C / window close.
4. Surfaces crashes honestly instead of looping on flaky models.

## Decisions (locked during brainstorming)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Where does the web UI live? | **In the daemon** (status quo). Supervisor is a thin watchdog. |
| 2 | How does the daemon ask the supervisor to restart it? | **Magic exit codes.** `0` = stop, `75` = restart, anything else = crash. |
| 3 | Sprite ownership? | **Supervisor's child.** Survives daemon restarts. |
| 4 | Supervisor language? | **Hybrid.** `start.ps1` keeps the audio-device TUI; supervisor itself is a Python module + `voice-commander-supervisor` console script. |
| 5 | Crash policy? | **No auto-restart on crash.** Only restart on explicit code 75. Crash = supervisor logs, kills sprite, exits with the daemon's code. |
| 6 | Config-change semantics? | **Per-field.** Cheap fields (prompt template, tool metadata, command/workflow CRUD) hot-reload as today. Expensive fields (audio device, whisper model, hotkey, `device_resample_rate`) flag a "Restart required" badge in the UI; user clicks the existing Restart button to apply. |
| 7 | `start.ps1` shape? | **Thin wrapper.** Audio-device TUI unchanged. Final call swaps `uv run voice-commander` → `uv run voice-commander-supervisor`. The sprite-spawn block in `Start-VoiceWithUI` is deleted (supervisor owns it). The browser-open job stays. |

## Architecture

```
start.ps1  (audio-device TUI, unchanged except final call)
    │
    └── uv run voice-commander-supervisor          ◀── new long-lived parent
              │
              ├── voice-sprite        (subprocess, persistent across restarts)
              │
              └── voice-commander     (subprocess, daemon; respawned on code 75)
                       │
                       └── (web UI, voice pipeline, EventBus — unchanged)
```

Five processes total at runtime:

1. **PowerShell** (`start.ps1`) — runs the audio-device TUI, then `exec`s into the supervisor (or `uv run`s it and waits). PS may exit after launch; alternatively it stays in the foreground so its console prints supervisor output. We pick "stays in the foreground" because that matches today's UX (Ctrl+C in the PS window stops everything).
2. **Supervisor** (Python) — long-lived parent. Restart loop + sprite lifecycle.
3. **Sprite** (Python, `voice-sprite`) — persistent child. SSE-reconnects when the daemon comes back.
4. **Daemon** (Python, `voice-commander`) — replaced on every restart.
5. **(daemon-internal threads)** — unchanged: PortAudio callback, VAD worker, pipeline worker, hotkey listener, web server, heartbeat.

### Components

#### `voice_commander.supervisor` (new package)

```
src/voice_commander/supervisor/
    __init__.py        # exports `main`
    __main__.py        # `python -m voice_commander.supervisor`
    cli.py             # arg parsing, logging setup, single-instance lock, calls into loop
    loop.py            # the restart loop (the heart)
    process.py         # cross-platform spawn / wait / kill helpers
    sprite.py          # spawn + kill the sprite child
```

**`loop.py` — restart loop sketch**

```python
EXIT_RESTART = 75      # daemon → supervisor: "respawn me"
EXIT_CLEAN = 0
SHUTDOWN_GRACE_S = 5.0

def run(ctx: SupervisorContext) -> int:
    sprite = SpriteChild.spawn(ctx)             # one shot, persistent
    try:
        while True:
            daemon = DaemonChild.spawn(ctx)
            code = daemon.wait()                # blocks until child exits
            if code == EXIT_RESTART:
                logger.info("Daemon requested restart; respawning")
                continue
            if code == EXIT_CLEAN:
                logger.info("Daemon exited cleanly; supervisor stopping")
                return 0
            logger.error("Daemon crashed (code=%d); supervisor stopping", code)
            return code
    finally:
        sprite.terminate(grace_s=SHUTDOWN_GRACE_S)
```

**`process.py` — spawn / kill helpers**

- `spawn(argv, *, env=None, name="...") -> ChildHandle`
  Calls `subprocess.Popen` with no detachment (we *want* to be the parent), `stdin=DEVNULL`, inherited stdout/stderr (so daemon logs interleave with supervisor in the PS console).
- `wait(handle) -> int` — blocks; returns exit code. On `KeyboardInterrupt` re-raises after sending Ctrl+C to the child.
- `terminate(handle, grace_s) -> None`
  - Posix: `SIGTERM`, wait `grace_s`, `SIGKILL`.
  - Windows: send `CTRL_BREAK_EVENT` to the process group (children spawned with `CREATE_NEW_PROCESS_GROUP`); wait `grace_s`; if still alive, `taskkill /T /F /PID <pid>` (the same recipe `start.ps1` already uses for the sprite).

**`sprite.py`**

Same shape as today's `Start-VoiceWithUI` sprite block, ported to Python. `uv run voice-sprite` (or `sys.executable -m voice_sprite` — pick whichever survives uv's PATH manipulation; benchmark in implementation).

#### Console-script entry

```toml
# pyproject.toml — new line under [project.scripts]
voice-commander-supervisor = "voice_commander.supervisor:main"
```

#### `voice_commander.__main__` changes

The daemon's `main()` already exits cleanly on `KeyboardInterrupt`. Changes:

1. Accept a `--from-supervisor` flag (purely informational, used to log lineage and to skip the friendly "are you sure you want to run the daemon directly?" check we may add later).
2. Replace `commands/restart.py:schedule_restart()` with `commands/restart.py:request_restart()` which **does not detach-spawn**. It just:
   - schedules `os._exit(75)` after a 0.5 s delay (same as today, so the HTTP response goes out first).
   - logs "Restart requested by web UI; exiting with code 75 for supervisor".
3. The "are we running under a supervisor?" check is `os.environ.get("VC_SUPERVISED") == "1"`. The supervisor sets this env var on its daemon child. If the user runs `uv run voice-commander` directly (no supervisor) and clicks Restart in the UI, we fall back to a `Restart unavailable — run via voice-commander-supervisor` HTTP error instead of blindly exiting 75 with no parent to catch it.

#### `voice_commander.commands.restart` rewrite

```python
def request_restart(delay_s: float = 0.5) -> None:
    """Schedule a graceful exit with code 75 so the supervisor respawns us."""
    if os.environ.get("VC_SUPERVISED") != "1":
        raise RestartUnavailable(
            "Restart requires the supervisor (run start.ps1 or "
            "voice-commander-supervisor)."
        )

    def _do() -> None:
        time.sleep(delay_s)
        logger.info("Exiting with code 75 for supervisor restart")
        os._exit(75)

    threading.Thread(target=_do, daemon=True, name="daemon-restart").start()
```

The detached-Popen path (`_spawn_new`) is deleted. ADR 0058 records the migration.

#### `web/admin.py` — `/restart` endpoint

Catches `RestartUnavailable` and returns a 503 with a friendly message. No other change.

#### `start.ps1` — minimal diff

- `Start-VoiceWithUI`:
  - **Delete** the sprite-spawn block (`if (-not $NoSprite -and -not $NoUI) { ... Start-Process ... }` and the matching `finally` taskkill).
  - **Replace** `Start-VoiceDaemon` (which calls `uv run voice-commander`) with `Start-VoiceSupervisor` (which calls `uv run voice-commander-supervisor`).
  - The browser-open job stays — supervisor's daemon child binds the same port.
  - `-NoSprite` flag survives as `--no-sprite` passed through to the supervisor.
- New helper:
  ```powershell
  function Start-VoiceSupervisor {
      $args = @()
      if ($NoSprite) { $args += '--no-sprite' }
      uv run voice-commander-supervisor @args
      return $LASTEXITCODE
  }
  ```
- Phase string bumped to "Phase 7: supervisor process".

## Data flow

### Boot

1. User runs `.\start.ps1` (or `start.bat`).
2. PS picks/persists audio device, then exec's `uv run voice-commander-supervisor`.
3. Supervisor acquires `outputs/.supervisor.lock`. If already held → friendly error, exit 1.
4. Supervisor spawns sprite child.
5. Supervisor enters the restart loop and spawns the daemon child with `VC_SUPERVISED=1`.
6. Daemon goes through its existing init (registry → web server → recorder → hotkey).
7. Browser-open job in `start.ps1` opens `http://127.0.0.1:8765` after 1.5 s.

### Restart from UI

1. User clicks **Restart daemon** in the web UI.
2. Browser POSTs `/restart`.
3. `web/admin.py:restart` → `commands/restart.py:request_restart()`.
4. Daemon returns 200, schedules `os._exit(75)` 0.5 s later, browser flips to "restarting…" banner.
5. Daemon exits 75. Supervisor's `wait()` returns 75 → loop continues, spawns fresh daemon.
6. Sprite is untouched. Its SSE connection drops when the daemon's port goes silent; sprite reconnects on a backoff once the new daemon is listening.
7. Browser polls `/healthz` (existing endpoint) until 200, then reloads the page (or the SSE-disconnect toast clears itself).

### Crash

1. Daemon exits with anything other than `0` or `75`.
2. Supervisor logs the code, terminates the sprite, exits with the daemon's code.
3. PS prints "Voice Commander exited with error (code N)" using its existing branch — no PS changes needed for crash handling.
4. User reads the crash log, fixes the issue, restarts via `start.ps1`.

### Ctrl+C in the PS console

1. Ctrl+C → `KeyboardInterrupt` reaches the supervisor (it's the foreground process under PS).
2. Supervisor's restart loop catches it → propagates to daemon (CTRL_BREAK_EVENT), waits up to `SHUTDOWN_GRACE_S`, falls through to `taskkill /T /F` if needed.
3. Sprite terminated in the `finally` block.
4. Supervisor exits 0 (or 130 if we choose to mirror the conventional Ctrl+C code — pick during implementation).

## Per-field config policy

Hot-reload (already wired today):

- `prompt.template` (already hot-reloads via `LLMRouter.reload_template()`).
- Tool metadata (`tools/*.toml`).
- Commands and workflows (`commands.json`, `workflows.json`).

Restart-required (new badge in UI):

- `audio.device`
- `audio.device_resample_rate`
- `transcriber.model_size`
- `transcriber.compute_type`
- `hotkey.key`
- `hotkey.mute_key`
- `web.port`

The "needs restart" set is hard-coded as a Python `frozenset[str]` in `web/admin.py` keyed on the dotted config path. The config-edit form compares the saved set vs the form submission and renders a banner: *"Some changes require a daemon restart. Click Restart daemon to apply."*

(This is the smallest possible UI change — no new endpoint, no diffing service, no client-side state. Just a banner that shows up after a save touched a flagged key.)

## Error handling

| Failure | Where caught | Behaviour |
|---------|--------------|-----------|
| Daemon exits 75, supervisor not present (`VC_SUPERVISED` unset) | `commands/restart.py` | Raise `RestartUnavailable`, surface 503 in the UI. |
| Sprite fails to spawn | `supervisor.sprite.SpriteChild.spawn` | Log warning, continue without sprite. (Sprite is best-effort; daemon should still run.) |
| Daemon fails to spawn | `supervisor.loop.run` | Log error, terminate sprite, exit 1. |
| Supervisor lock held | `supervisor.cli.main` | Print friendly error: "voice-commander-supervisor is already running (pid=N)". Exit 1. |
| Ctrl+C during daemon shutdown | `supervisor.process.terminate` | Force-kill after grace window. |
| Daemon hangs on shutdown (graceful exit > grace_s) | `supervisor.process.terminate` | `taskkill /T /F`. Log a warning. |

## Testing strategy

### Unit tests (pure Python, fast)

- `tests/unit/supervisor/test_loop.py`
  - Stubs `DaemonChild` and `SpriteChild` with a fake whose `wait()` returns a queued list of exit codes; asserts the loop respawns on 75, exits on 0, exits on crash, terminates sprite in `finally`.
  - Parametrize: `[(0,)], [(75, 0)], [(75, 75, 0)], [(1,)], [(75, -1)]`.
- `tests/unit/supervisor/test_process.py`
  - Pure-mock test of `terminate()` flow: subprocess that ignores SIGTERM is force-killed after grace window.
- `tests/unit/test_request_restart.py`
  - `VC_SUPERVISED=1` → schedules thread, exits 75 (mock `os._exit`).
  - `VC_SUPERVISED` unset → raises `RestartUnavailable`.

### Integration tests

- `tests/integration/test_supervisor_e2e.py`
  - Spawns `voice-commander-supervisor` against a `--fake-daemon` mode that sleeps + exits with a code from a fixture file. Asserts:
    - Code 75 once → respawn.
    - Code 0 → exits cleanly.
    - Code 1 → exits with 1 and sprite stub was killed.
  - Uses a sprite stub (`scripts/sprite-stub.py`) that just logs to a file so we can assert it survived a restart.
- `tests/integration/test_restart_e2e.py` (extends the existing web smoke test)
  - Hit `/restart`, assert daemon process pid changes, web port comes back up within 10 s, sprite pid unchanged.

### Manual validation (per the project's phase-gate rule)

Documented in the implementation plan. Includes:

- Real daemon, click Restart in the UI: sprite stays on screen, browser auto-recovers within ~5 s.
- Edit `audio.device` in the UI → "Restart required" banner appears → click Restart → new device is in use without a fresh `start.ps1`.
- Kill the daemon process from Task Manager → supervisor logs the crash and exits with the right code (no infinite restart loop).
- Ctrl+C in the PS window → daemon, sprite, supervisor all exit; no orphan processes (verified with `Get-Process voice-commander, voice-sprite, python`).

## ADR

A new ADR `0058-supervisor-process-owns-lifecycle.md` records:

- The exit-code contract.
- The per-field config policy.
- The decision to deprecate detach-spawn restart.

## Out of scope (deliberately)

- **Auto-restart on crash.** Decision 5 is "no". A future ADR can revisit if real-world flakiness justifies it.
- **Cross-platform supervisor.** Windows is the only target today. The process module sketches POSIX paths so the door isn't slammed shut, but POSIX isn't tested.
- **A control socket / second port for the supervisor.** Magic exit codes are sufficient for v1.
- **A tray icon / systray UI for the supervisor.** Out of scope; the `pystray` optional extra already exists for a future tray feature.
- **Hot-reloading the supervisor itself.** Supervisor restarts require Ctrl+C + relaunch.

## Migration / cleanup

When this lands:

1. `commands/restart.py` loses `_spawn_new`; gains `request_restart`.
2. `start.ps1`'s `Start-VoiceDaemon` becomes `Start-VoiceSupervisor`; the sprite-spawn/teardown block disappears.
3. New `outputs/.supervisor.lock` joins `outputs/.daemon.lock` in `.gitignore` (verify).
4. README's "Restart" section gets a one-liner: "The Restart daemon button works only when launched via `start.ps1` (which runs the supervisor)."
5. `docs/architecture.md` gets a process-tree diagram refresh.
6. `docs/agents/technical-decisions.md` gets a row pointing to ADR 0058.

## Open questions for implementation

(Resolve at code time, not now.)

- Whether `uv run voice-sprite` adds enough startup latency vs `sys.executable -m voice_sprite` to matter. Pick whichever is cleaner.
- Whether to use Python's stdlib `subprocess.Popen` directly or wrap in `psutil.Process` (already a dep). `psutil` makes child-tree kill on Windows nicer.
- The exact exit code on Ctrl+C: 0 (clean), 130 (SIGINT convention), or `-1073741510` (Windows STATUS_CONTROL_C_EXIT). PS already special-cases the latter two.
