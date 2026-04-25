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

Per-field config policy: cheap fields hot-reload as before. Expensive fields (`audio.device`, `transcription.model_size`) trigger an amber "Some changes require a restart" banner in the UI prompting the user to click Restart. Detection uses a pre/post `Config.load()` snapshot diff for the keys the form actually submits today.

## Consequences

* The web UI's Restart button now requires the supervisor; running the daemon directly (`uv run voice-commander`) and clicking Restart returns 503 with a friendly message.
* No auto-restart on crash — supervisor surfaces the daemon's exit code and stops. Recovery is via `start.ps1`. We can revisit if real-world flakiness justifies it (separate ADR).
* Two locks (`.daemon.lock` and `.supervisor.lock`) coexist; both go in `outputs/` and are gitignored (the existing `outputs/*` pattern covers both).
* Sprite reconnects its SSE stream when the daemon comes back; the existing reconnect-with-backoff logic carries this load.

## Alternatives considered

* **Watchdog-only supervisor (web UI stays in daemon).** Picked. Smallest diff, web UI flickers briefly during restart but the sprite stays put.
* **Web UI in supervisor.** Rejected for v1 — splits `web/` away from daemon wiring; bigger refactor than warranted.
* **Localhost control socket between daemon and supervisor.** Rejected — magic exit codes are simpler and cover every use case we have today.
