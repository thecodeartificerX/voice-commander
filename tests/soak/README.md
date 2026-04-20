# Soak Test

External RSS-growth monitor. Attaches to a running Voice Commander daemon via PID, samples memory every N seconds, asserts delta stays under a ceiling.

## Why external

Leaks caught here without touching production code. Daemon runs normally via `uv run voice-commander` (or `start.ps1`); the soak script reads its memory from outside. No `VOICE_COMMANDER_SOAK` flag, no file-watcher inside daemon, no test-only code paths in production.

## Running

1. Start the daemon in its own terminal:
   ```pwsh
   uv run voice-commander
   ```

2. Find the PID (pick the one with non-trivial memory):
   ```pwsh
   tasklist | findstr voice-commander
   ```

3. Run the soak in a second terminal:
   ```pwsh
   uv run python tests/soak/run_soak.py --pid <PID> --hours 1
   ```

4. While it runs, drive the daemon normally — speak commands, let it sit idle, mix both. The soak script samples RSS every 30 s by default and prints deltas live.

5. On completion:
   - Exit 0 + `SOAK OK` → pass.
   - Exit 1 + `FAIL` → leak detected, see CSV trace.
   - Exit 2 → process gone or unreachable.

## Flags

| Flag | Default | Purpose |
|---|---|---|
| `--pid` | required | PID of running daemon |
| `--hours` | `1.0` | Total run duration |
| `--interval` | `30.0` | Seconds between samples |
| `--limit-mb` | `100.0` | Fail threshold for RSS growth |
| `--csv` | none | Optional trace file for post-mortem |

## Interpreting results

- **Steady-state RSS** after warm-up (~5 min) should stay flat within ±20 MB. Short upward drift after each command is expected (CUDA caching), should plateau.
- **Linear growth** over hours = leak. Most likely culprits: unbounded log buffer, stale WAV retention, thread-local accumulation, pynput listener state.
- **Peak RSS** matters for crash surface; delta matters for leak surface.

## 24-hour run

For the Phase 5 GATE, do one 24 h idle + occasional-use pass:

```pwsh
uv run python tests/soak/run_soak.py --pid <PID> --hours 24 --csv outputs/soak-24h.csv
```

Expected: `SOAK OK`, delta ≤ 100 MB.
