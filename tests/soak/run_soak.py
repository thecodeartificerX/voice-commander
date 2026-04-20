"""External memory-leak soak harness for Voice Commander.

Samples the RSS of a running `voice-commander` daemon at a fixed interval
for a bounded duration, then asserts that the growth stays under a
configurable ceiling.

Does NOT touch the daemon's source. Run the daemon separately (via
`uv run voice-commander` or `start.ps1`), pass its PID in, and drive
it manually by speaking commands through Scroll Lock while the soak
ticks. Idle-only runs are also valuable — they catch background-thread
leaks even when no commands fire.

Usage:
    # Find the daemon PID (Windows):
    #   tasklist | findstr voice-commander
    # Then:
    uv run python tests/soak/run_soak.py --pid <PID> --hours 1

Flags:
    --pid       PID of the running daemon (required).
    --hours     Total run duration. Default 1.0.
    --interval  Seconds between samples. Default 30.
    --limit-mb  Max allowed RSS growth before FAIL. Default 100.
    --csv       Optional path to write a raw CSV trace of samples.

Exit codes:
    0 — SOAK OK (delta within limit)
    1 — FAIL (leak detected)
    2 — could not attach to process
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path

import psutil


def _sample_bytes(proc: psutil.Process) -> int:
    """Return a stable per-process memory number.

    Windows `memory_info().rss` reports WorkingSet, which the kernel trims
    aggressively on an idle process — leading to spurious 100+ MB "drops"
    that look like crashes but are just page-outs. `memory_full_info().uss`
    (unique set size, i.e. private + committed) is stable and is what we
    actually care about for leak detection. Falls back to RSS where USS is
    unavailable (rare; some OSes / permission-limited cases).
    """
    try:
        return proc.memory_full_info().uss
    except (psutil.AccessDenied, AttributeError):
        return proc.memory_info().rss


def main() -> int:
    parser = argparse.ArgumentParser(description="Voice Commander RSS soak test.")
    parser.add_argument("--pid", type=int, required=True, help="PID of running voice-commander daemon")
    parser.add_argument("--hours", type=float, default=1.0)
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--limit-mb", type=float, default=100.0)
    parser.add_argument("--csv", type=Path, default=None)
    args = parser.parse_args()

    try:
        proc = psutil.Process(args.pid)
        proc_create_time = proc.create_time()
        _sample_bytes(proc)
    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
        print(f"ERROR: cannot attach to PID {args.pid}: {e}", file=sys.stderr)
        return 2

    start_rss = _sample_bytes(proc)
    start_time = time.time()
    deadline = start_time + args.hours * 3600
    peak_rss = start_rss
    samples: list[tuple[float, int]] = [(0.0, start_rss)]

    print(
        f"soak start: pid={args.pid} hours={args.hours} interval={args.interval}s "
        f"limit={args.limit_mb:.0f}MB start_rss={start_rss/1e6:.1f}MB"
    )

    try:
        while time.time() < deadline:
            time.sleep(args.interval)
            try:
                if proc.create_time() != proc_create_time:
                    print("ERROR: PID was reused by a different process mid-soak", file=sys.stderr)
                    return 2
                rss = _sample_bytes(proc)
            except psutil.NoSuchProcess:
                print("ERROR: daemon exited mid-soak", file=sys.stderr)
                return 2
            peak_rss = max(peak_rss, rss)
            elapsed = time.time() - start_time
            delta_mb = (rss - start_rss) / 1e6
            samples.append((elapsed, rss))
            print(
                f"[{datetime.now():%H:%M:%S}] t={elapsed/60:.1f}m "
                f"rss={rss/1e6:.1f}MB delta={delta_mb:+.1f}MB"
            )
    except KeyboardInterrupt:
        print("\nsoak interrupted by user; reporting partial result")

    final_rss = samples[-1][1]
    delta_mb = (final_rss - start_rss) / 1e6
    peak_delta_mb = (peak_rss - start_rss) / 1e6

    if args.csv is not None:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["elapsed_sec", "rss_bytes"])
            w.writerows(samples)
        print(f"trace written to {args.csv}")

    print(
        f"soak done: samples={len(samples)} "
        f"final={final_rss/1e6:.1f}MB delta={delta_mb:+.1f}MB "
        f"peak_delta={peak_delta_mb:+.1f}MB"
    )
    if delta_mb > args.limit_mb:
        print(f"FAIL: RSS grew by {delta_mb:.1f}MB (limit {args.limit_mb:.0f}MB)")
        return 1
    print("SOAK OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
