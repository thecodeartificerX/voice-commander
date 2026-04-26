"""External memory-leak soak harness for Voice Commander.

Supports two modes:

  1. PID monitor (original mode):
     Samples the RSS of a running daemon at fixed intervals.
     Usage: uv run python tests/soak/run_soak.py --pid <PID> --hours 1

  2. Scenario (self-contained):
     Runs a synthetic workload (e.g. foreach_dag) in-process without a daemon.
     Usage: uv run python tests/soak/run_soak.py --scenario foreach_dag --duration 60

Flags:
    --pid       PID of the running daemon (required in PID monitor mode).
    --scenario  Scenario name: "foreach_dag". If set, --pid is ignored.
    --hours     Total run duration in PID monitor mode. Default 1.0.
    --duration  Total run duration in scenario mode (seconds). Default 60.
    --interval  Seconds between samples (PID monitor only). Default 30.
    --limit-mb  Max allowed RSS growth before FAIL. Default 100.
    --csv       Optional path to write a raw CSV trace of samples.

Exit codes:
    0 — SOAK OK (delta within limit / assertions passed)
    1 — FAIL (leak detected / assertions failed)
    2 — could not attach to process (PID monitor only)
"""

from __future__ import annotations

import argparse
import csv
import statistics
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


def scenario_foreach_dag(duration_s: int = 60) -> int:
    """Fire an 8-node DAG with one Foreach (5 iterations) repeatedly.

    Asserts p95 latency < 1500ms and RSS delta < 50MB.

    Returns 0 on success, 1 on failure.
    """
    import os

    from voice_commander.commands.graph import Edge, Graph, GraphInput, Node, PortRef
    from voice_commander.commands.graph_runtime import GraphRuntime
    from voice_commander.registry import ToolEntry, ToolRegistry

    # Build a foreach DAG:
    # input -> foreach -> press(ctrl+a) -> press(ctrl+c) -> wait(1ms)
    #       -> press(ctrl+v) -> press(escape) -> press(f5)
    g = Graph(
        name="soak_foreach",
        kind="command",
        description="",
        synonyms=(),
        inputs=(GraphInput("items", "str", True, ""),),
        llm_visible=False,
        strict=False,
        enabled=True,
        timeout_ms=5000,
        foreach_iteration_cap=50,
        nodes=(
            Node("in", "value.input", {}),
            Node("f1", "control.foreach", {}),
            Node("p1", "pipeline.press", {"combo": "ctrl+a"}),
            Node("p2", "pipeline.press", {"combo": "ctrl+c"}),
            Node("w1", "pipeline.wait", {"ms": 1}),
            Node("p3", "pipeline.press", {"combo": "ctrl+v"}),
            Node("p4", "pipeline.press", {"combo": "escape"}),
            Node("p5", "pipeline.press", {"combo": "f5"}),
        ),
        edges=(
            Edge(PortRef("in", "items"), PortRef("f1", "list")),
            Edge(PortRef("f1", "item"), PortRef("p1", "in")),
            Edge(PortRef("p1", "ok"), PortRef("p2", "in")),
            Edge(PortRef("p2", "ok"), PortRef("w1", "in")),
            Edge(PortRef("w1", "ok"), PortRef("p3", "in")),
            Edge(PortRef("p3", "ok"), PortRef("p4", "in")),
            Edge(PortRef("p4", "ok"), PortRef("p5", "in")),
        ),
    )

    # Build a mock registry with press and wait tools that do nothing
    registry = ToolRegistry()
    for tool_name in ("press", "wait"):
        registry.register(
            ToolEntry(
                name=tool_name,
                phrases=(),
                func=lambda **_: None,
                module="soak",
                docstring=None,
                enabled=True,
                llm_only=True,
                internal=True,
                origin="primitive",
                args_meta={},
            )
        )

    runtime = GraphRuntime(registry, graph_lookup=lambda _: None)

    latencies_ms = []
    proc = psutil.Process(os.getpid())
    start_rss = _sample_bytes(proc)

    start_time = time.time()
    deadline = start_time + duration_s
    iteration = 0

    print(
        f"foreach_dag soak: duration={duration_s}s graph=8 nodes, foreach cap=50"
    )

    while time.time() < deadline:
        t0 = time.perf_counter()
        outcome, _ = runtime.run(g, {"items": ["a", "b", "c", "d", "e"]})
        t1 = time.perf_counter()
        latencies_ms.append((t1 - t0) * 1000)
        iteration += 1

    end_rss = _sample_bytes(proc)
    delta_mb = (end_rss - start_rss) / 1e6

    if latencies_ms:
        p50 = statistics.median(latencies_ms)
        p95 = sorted(latencies_ms)[int(len(latencies_ms) * 0.95)]
        print(
            f"iterations={iteration} p50={p50:.1f}ms p95={p95:.1f}ms "
            f"delta_rss={delta_mb:+.2f}MB"
        )

        if p95 > 1500:
            print(f"FAIL: p95 latency {p95:.1f}ms > 1500ms")
            return 1
        if delta_mb > 50:
            print(f"FAIL: RSS grew {delta_mb:.1f}MB > 50MB")
            return 1

    print("SOAK OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Voice Commander RSS soak test.")
    parser.add_argument(
        "--pid", type=int, required=False,
        help="PID of running voice-commander daemon (required in PID monitor mode)"
    )
    parser.add_argument(
        "--scenario", choices=["foreach_dag"], default=None,
        help="Scenario name (self-contained test)"
    )
    parser.add_argument(
        "--hours", type=float, default=1.0, help="Duration in hours (PID monitor mode)"
    )
    parser.add_argument(
        "--duration", type=int, default=60, help="Duration in seconds (scenario mode)"
    )
    parser.add_argument(
        "--interval", type=float, default=30.0,
        help="Sample interval in seconds (PID monitor mode)"
    )
    parser.add_argument("--limit-mb", type=float, default=100.0)
    parser.add_argument("--csv", type=Path, default=None)
    args = parser.parse_args()

    # Scenario mode: run self-contained test
    if args.scenario is not None:
        if args.scenario == "foreach_dag":
            return scenario_foreach_dag(duration_s=args.duration)
        else:
            print(f"ERROR: unknown scenario '{args.scenario}'", file=sys.stderr)
            return 1

    # PID monitor mode: requires --pid
    if args.pid is None:
        parser.error("--pid is required when --scenario is not specified")

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
        f"limit={args.limit_mb:.0f}MB start_rss={start_rss / 1e6:.1f}MB"
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
                f"[{datetime.now():%H:%M:%S}] t={elapsed / 60:.1f}m "
                f"rss={rss / 1e6:.1f}MB delta={delta_mb:+.1f}MB"
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
        f"final={final_rss / 1e6:.1f}MB delta={delta_mb:+.1f}MB "
        f"peak_delta={peak_delta_mb:+.1f}MB"
    )
    if delta_mb > args.limit_mb:
        print(f"FAIL: RSS grew by {delta_mb:.1f}MB (limit {args.limit_mb:.0f}MB)")
        return 1
    print("SOAK OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
