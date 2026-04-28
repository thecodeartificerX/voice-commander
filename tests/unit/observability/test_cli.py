from __future__ import annotations

import json
from pathlib import Path

from voice_commander.observability.cli import (
    build_debug_parser,
    build_tail_parser,
    run_debug,
    run_tail,
)
from voice_commander.observability.store import (
    RunRecord,
    RunUpdate,
    SpanRecord,
    Store,
)


def _seed(tmp_path: Path) -> None:
    """Write a known run into the DB, then stop the Store so CLI can open it."""
    s = Store(tmp_path / "runs.db", keep_runs=10, queue_max=64, daemon_pid=1)
    s.start()
    s.write_run_start(RunRecord("aaa", 1000.0, "open chrome", 1))
    s.write_span(
        SpanRecord(
            span_id="root",
            run_id="aaa",
            parent_span_id=None,
            type="run",
            name="run",
            started_at=1000.0,
            ended_at=1000.5,
            duration_ms=500,
            status="ok",
        )
    )
    s.write_run_end(RunUpdate("aaa", 1000.5, "ok", None, 500))
    s.flush()
    s.stop()  # Release WAL lock before CLI opens its own Store on the same file


def test_vc_debug_last_json(tmp_path, capsys):
    _seed(tmp_path)
    parser = build_debug_parser()
    args = parser.parse_args(["last", "--json", "--db", str(tmp_path / "runs.db")])
    run_debug(args)
    out = capsys.readouterr().out
    body = json.loads(out)
    assert body["run_id"] == "aaa"


def test_vc_debug_runs_list(tmp_path, capsys):
    _seed(tmp_path)
    parser = build_debug_parser()
    args = parser.parse_args(["runs", "--limit", "5", "--db", str(tmp_path / "runs.db")])
    run_debug(args)
    out = capsys.readouterr().out
    assert "aaa" in out
    assert "open chrome" in out


def test_vc_tail_dumps_recent_runs_when_no_follow(tmp_path, capsys):
    _seed(tmp_path)
    args = build_tail_parser().parse_args(["--no-follow", "--db", str(tmp_path / "runs.db")])
    run_tail(args)
    out = capsys.readouterr().out
    assert "aaa" in out
    assert "open chrome" in out


def test_vc_debug_grep_matches_transcript(tmp_path, capsys):
    """grep subcommand finds runs whose transcript contains the search term."""
    _seed(tmp_path)
    parser = build_debug_parser()
    args = parser.parse_args(["grep", "chrome", "--db", str(tmp_path / "runs.db")])
    run_debug(args)
    out = capsys.readouterr().out
    assert "aaa" in out
    assert "open chrome" in out


def test_vc_debug_grep_no_match_produces_no_output(tmp_path, capsys):
    """grep subcommand produces no output when no transcript matches."""
    _seed(tmp_path)
    parser = build_debug_parser()
    args = parser.parse_args(["grep", "zzznomatch", "--db", str(tmp_path / "runs.db")])
    run_debug(args)
    out = capsys.readouterr().out
    assert out.strip() == ""


def test_vc_debug_replay_full_without_yes_exits():
    """replay-full without --yes must exit with code 2 (safety gate)."""
    import pytest

    parser = build_debug_parser()
    args = parser.parse_args(["replay-full", "aaa"])  # no --yes flag
    with pytest.raises(SystemExit) as exc_info:
        run_debug(args)
    assert exc_info.value.code == 2
