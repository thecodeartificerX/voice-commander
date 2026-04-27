"""`vc debug` and `vc tail` CLI subcommands."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from voice_commander.observability.store import Store


def _resolve_db(args: argparse.Namespace) -> Path:
    if getattr(args, "db", None):
        return Path(args.db)
    return Path("outputs/runs.db")


def _open_store(db_path: Path) -> Store:
    s = Store(db_path, keep_runs=10000, queue_max=64, daemon_pid=os.getpid())
    s.start()
    return s


def _format_tree(run: dict[str, Any], spans: list[dict[str, Any]]) -> str:
    lines = []
    started = time.strftime("%H:%M:%S", time.localtime(run["started_at"]))
    lines.append(
        f"─ run {run['run_id']} ({started}) {run['transcript']!r} ─ "
        f"{run['status']} {run['duration_ms']}ms"
    )
    children: dict[str | None, list[dict[str, Any]]] = {}
    for s in spans:
        children.setdefault(s["parent_span_id"], []).append(s)

    def _walk(parent_id: str | None, indent: int) -> None:
        for s in children.get(parent_id, []):
            err = f"  {s['error_msg']}" if s["error_msg"] else ""
            lines.append(
                f"{'  ' * indent}{s['type']}/{s['name']}  {s['duration_ms']}ms  {s['status']}{err}"
            )
            _walk(s["span_id"], indent + 1)

    _walk(None, 1)
    return "\n".join(lines)


def _cmd_last(args: argparse.Namespace) -> None:
    s = _open_store(_resolve_db(args))
    try:
        run = s.get_last_run()
        if run is None:
            print("no runs yet", file=sys.stderr)
            sys.exit(1)
        spans = s.get_spans(run["run_id"])
        if args.json:
            print(json.dumps({**run, "spans": spans}, indent=2, default=str))
        else:
            print(_format_tree(run, spans))
    finally:
        s.stop()


def _cmd_run(args: argparse.Namespace) -> None:
    s = _open_store(_resolve_db(args))
    try:
        run = s.get_run(args.run_id)
        if run is None:
            print(f"run {args.run_id} not found", file=sys.stderr)
            sys.exit(1)
        spans = s.get_spans(run["run_id"])
        if args.json:
            print(json.dumps({**run, "spans": spans}, indent=2, default=str))
        else:
            print(_format_tree(run, spans))
    finally:
        s.stop()


def _cmd_runs(args: argparse.Namespace) -> None:
    s = _open_store(_resolve_db(args))
    try:
        runs = s.list_runs(limit=args.limit, status=getattr(args, "status", None))
        for r in runs:
            dur = r["duration_ms"] or 0
            print(
                f"{r['run_id']}  {r['status']:6s}  {dur:5d}ms  {r['transcript']!r}"
            )
    finally:
        s.stop()


def _cmd_errors(args: argparse.Namespace) -> None:
    args.status = "error"
    if not hasattr(args, "limit") or args.limit is None:
        args.limit = 50
    _cmd_runs(args)


def _cmd_grep(args: argparse.Namespace) -> None:
    s = _open_store(_resolve_db(args))
    try:
        runs = s.list_runs(limit=200, transcript_like=args.text)
        for r in runs:
            print(f"{r['run_id']}  {r['status']:6s}  {r['transcript']!r}")
    finally:
        s.stop()


def _cmd_llm(args: argparse.Namespace) -> None:
    s = _open_store(_resolve_db(args))
    try:
        spans = s.get_spans(args.run_id)
        for sp in spans:
            if sp["type"] == "llm_call":
                print(json.dumps(sp, indent=2, default=str))
                return
        print("no llm_call span on this run", file=sys.stderr)
        sys.exit(1)
    finally:
        s.stop()


def _cmd_replay_llm(args: argparse.Namespace) -> None:
    try:
        import httpx
        base = getattr(args, "endpoint", "http://127.0.0.1:8765")
        r = httpx.post(f"{base}/api/runs/{args.run_id}/replay-llm")
        r.raise_for_status()
        print(json.dumps(r.json(), indent=2))
    except ImportError:
        print("httpx not installed; cannot make HTTP request", file=sys.stderr)
        sys.exit(1)


def _cmd_replay_full(args: argparse.Namespace) -> None:
    if not getattr(args, "yes", False):
        print(
            "refusing without --yes (full replay re-fires the plan into the foreground window)",
            file=sys.stderr,
        )
        sys.exit(2)
    try:
        import httpx
        base = getattr(args, "endpoint", "http://127.0.0.1:8765")
        r = httpx.post(
            f"{base}/api/runs/{args.run_id}/replay-full",
            headers={"X-Replay-Confirm": "yes"},
        )
        r.raise_for_status()
        print(json.dumps(r.json(), indent=2))
    except ImportError:
        print("httpx not installed", file=sys.stderr)
        sys.exit(1)


def build_debug_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vc debug")
    p.add_argument("--db", help="path to runs.db (default: outputs/runs.db)")
    sub = p.add_subparsers(dest="cmd", required=True)

    last_p = sub.add_parser("last", help="show last run")
    last_p.add_argument("--json", action="store_true")
    last_p.add_argument("--db", help="path to runs.db")
    last_p.set_defaults(func=_cmd_last)

    run_p = sub.add_parser("run", help="show one run")
    run_p.add_argument("run_id")
    run_p.add_argument("--json", action="store_true")
    run_p.add_argument("--db", help="path to runs.db")
    run_p.set_defaults(func=_cmd_run)

    runs_p = sub.add_parser("runs", help="list runs")
    runs_p.add_argument("--limit", type=int, default=20)
    runs_p.add_argument("--status")
    runs_p.add_argument("--db", help="path to runs.db")
    runs_p.set_defaults(func=_cmd_runs)

    errors_p = sub.add_parser("errors", help="recent errors")
    errors_p.add_argument("--limit", type=int, default=50)
    errors_p.add_argument("--db", help="path to runs.db")
    errors_p.set_defaults(func=_cmd_errors)

    grep_p = sub.add_parser("grep", help="search transcripts")
    grep_p.add_argument("text")
    grep_p.add_argument("--db", help="path to runs.db")
    grep_p.set_defaults(func=_cmd_grep)

    llm_p = sub.add_parser("llm", help="show llm_call span of a run")
    llm_p.add_argument("run_id")
    llm_p.add_argument("--db", help="path to runs.db")
    llm_p.set_defaults(func=_cmd_llm)

    rl_p = sub.add_parser("replay-llm", help="re-route transcript through current LLM")
    rl_p.add_argument("run_id")
    rl_p.add_argument("--endpoint", default="http://127.0.0.1:8765")
    rl_p.set_defaults(func=_cmd_replay_llm)

    rf_p = sub.add_parser("replay-full", help="REFIRE the plan (footgun)")
    rf_p.add_argument("run_id")
    rf_p.add_argument("--yes", action="store_true")
    rf_p.add_argument("--endpoint", default="http://127.0.0.1:8765")
    rf_p.set_defaults(func=_cmd_replay_full)

    return p


def run_debug(args: argparse.Namespace) -> None:
    args.func(args)


def build_tail_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vc tail")
    p.add_argument("--db")
    p.add_argument("--no-follow", action="store_true")
    p.add_argument("--since", type=float, default=None,
                   help="unix epoch seconds; only runs after this")
    p.add_argument("--status")
    return p


def run_tail(args: argparse.Namespace) -> None:
    s = _open_store(_resolve_db(args))
    try:
        seen: set[str] = set()

        def _emit_new() -> int:
            new = 0
            runs = s.list_runs(
                limit=20, status=args.status, since_ts=args.since,
            )
            runs.reverse()  # oldest first
            for r in runs:
                if r["run_id"] in seen:
                    continue
                seen.add(r["run_id"])
                spans = s.get_spans(r["run_id"])
                print(_format_tree(r, spans))
                print()
                new += 1
            return new

        _emit_new()
        if args.no_follow:
            return

        while True:
            time.sleep(1.0)
            _emit_new()
    except KeyboardInterrupt:
        pass
    finally:
        s.stop()
