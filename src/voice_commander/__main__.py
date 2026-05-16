from __future__ import annotations

import argparse
import atexit
import faulthandler
import logging
import os
import platform
import signal
import sys
import threading
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import Config
from .daemon import build_streaming_daemon
from .single_instance import AlreadyRunning, SingleInstanceLock

logger = logging.getLogger(__name__)


def _install_exit_diagnostics(cfg: Config) -> None:
    """Trace every path the daemon can leave by.

    Three hooks:

    * ``atexit`` — runs on normal interpreter teardown (sys.exit, return from main,
      unhandled exception that propagates out of main). Captures cause + thread snapshot.
    * ``sys.excepthook`` — last-resort logger for unhandled exceptions on the main
      thread that would otherwise vanish into a tracestream.
    * ``signal`` handlers for SIGINT/SIGTERM/SIGBREAK — logs the signal name before
      the existing handler (registered later in daemon.run) takes over.

    Does NOT replace ``faulthandler`` (signals like SIGSEGV stay on the C-level
    dump path). This is the soft-exit complement.
    """
    exit_log = Path(cfg.logging.file).with_suffix(".exit.log").open(
        "a", buffering=1, encoding="utf-8",
    )

    def _stamp() -> str:
        import datetime
        return datetime.datetime.now().isoformat(timespec="milliseconds")

    def _on_exit() -> None:
        try:
            exit_log.write(f"\n=== {_stamp()} pid={os.getpid()} atexit ===\n")
            exit_log.write(f"sys.exc_info()={sys.exc_info()}\n")
            for tid, frame in sys._current_frames().items():
                exit_log.write(f"\n--- Thread {tid} ---\n")
                exit_log.write("".join(traceback.format_stack(frame)))
            exit_log.flush()
        except Exception:
            pass

    atexit.register(_on_exit)

    def _excepthook(exc_type, exc_value, exc_tb):  # type: ignore[no-untyped-def]
        try:
            exit_log.write(f"\n=== {_stamp()} pid={os.getpid()} unhandled ===\n")
            traceback.print_exception(exc_type, exc_value, exc_tb, file=exit_log)
            exit_log.flush()
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook

    def _log_signal(signum: int, _frame: object) -> None:
        try:
            name = signal.Signals(signum).name  # type: ignore[attr-defined]
        except Exception:
            name = str(signum)
        try:
            exit_log.write(f"\n=== {_stamp()} pid={os.getpid()} signal={name} ===\n")
            for tid, frame in sys._current_frames().items():
                exit_log.write(f"\n--- Thread {tid} ---\n")
                exit_log.write("".join(traceback.format_stack(frame)))
            exit_log.flush()
        except Exception:
            pass
        # Re-raise default behaviour so the daemon's own handler in run() still fires.
        raise KeyboardInterrupt(name)

    # Only log; the daemon installs its own SIGINT/SIGBREAK -> shutdown() inside run().
    # We register here too so signals that arrive BEFORE run() does are captured.
    for _signame in ("SIGINT", "SIGTERM", "SIGBREAK"):
        _sig = getattr(signal, _signame, None)
        if _sig is not None:
            try:
                signal.signal(_sig, _log_signal)
            except (ValueError, OSError):
                pass  # not the main thread, or unsupported

    logger.info(
        "exit diagnostics enabled: atexit + excepthook + signal logger -> %s",
        exit_log.name,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voice-commander",
        description="Voice-driven command launcher for Windows.",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Discover tools, validate config, print OK, and exit.",
    )
    return parser


def _configure_logging(cfg: Config) -> None:
    level_name = os.environ.get("VC_LOG_LEVEL", cfg.logging.level).upper()
    level = getattr(logging, level_name, logging.INFO)
    file_handler = RotatingFileHandler(
        cfg.logging.file,
        maxBytes=cfg.logging.max_bytes,
        backupCount=cfg.logging.backup_count,
        encoding="utf-8",
    )
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(threadName)s %(name)s %(levelname)s: %(message)s",
        handlers=[file_handler, logging.StreamHandler()],
        force=True,
    )
    logging.getLogger("faster_whisper").setLevel(level)


def _enable_crash_reporting(cfg: Config) -> None:
    crash_log_path = Path(cfg.logging.file).with_suffix(".crash.log")
    crash_log = crash_log_path.open("a", buffering=1, encoding="utf-8")
    faulthandler.enable(file=crash_log, all_threads=True)
    logger.info("faulthandler enabled, native crashes will be dumped to %s", crash_log_path)


def _log_environment(cfg: Config) -> None:
    logger.info(
        "python=%s platform=%s executable=%s",
        sys.version.split()[0],
        platform.platform(),
        sys.executable,
    )
    try:
        import ctranslate2

        logger.info(
            "ctranslate2=%s cuda_devices=%d supported_compute=%s",
            ctranslate2.__version__,
            ctranslate2.get_cuda_device_count(),
            sorted(ctranslate2.get_supported_compute_types("cuda"))
            if ctranslate2.get_cuda_device_count() > 0
            else [],
        )
    except Exception:
        logger.exception("failed to probe ctranslate2")
    try:
        import faster_whisper

        logger.info("faster_whisper=%s", faster_whisper.__version__)
    except Exception:
        logger.exception("failed to probe faster_whisper")


def _run_validate(cfg: Config) -> None:
    """Discover tools, bind metadata, run startup validator, and exit."""
    from .registry import discover
    from .tool_metadata import ToolMetadataStore
    from .validator import validate_config_or_die, validate_or_die

    tools_dir = Path(__file__).resolve().parent / "tools"
    store = ToolMetadataStore(tools_dir)
    registry = discover("voice_commander.tools", store=store)
    validate_config_or_die(cfg)
    validate_or_die(registry, store)
    print("OK")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "debug":
        from voice_commander.observability.cli import build_debug_parser, run_debug

        parser = build_debug_parser()
        ns = parser.parse_args(sys.argv[2:])
        run_debug(ns)
        return

    if len(sys.argv) > 1 and sys.argv[1] == "tail":
        from voice_commander.observability.cli import build_tail_parser, run_tail

        ns = build_tail_parser().parse_args(sys.argv[2:])
        run_tail(ns)
        return

    parser = _build_parser()
    args = parser.parse_args()

    # One-shot migration: convert legacy commands/workflows to canonical Graph schema
    # before anything else loads them.
    _state_dir = Path(os.environ.get("VC_STATE_DIR", str(Path(__file__).resolve().parents[2])))
    from voice_commander.commands.graph_migrate import migrate_legacy_to_graphs

    migrate_legacy_to_graphs(
        _state_dir / "commands.json",
        _state_dir / "workflows.json",
    )

    cfg = Config.load(Path("config.toml"))
    _configure_logging(cfg)

    if args.validate:
        _run_validate(cfg)
        return

    _enable_crash_reporting(cfg)
    _install_exit_diagnostics(cfg)
    _log_environment(cfg)
    lock = SingleInstanceLock(Path("outputs/.daemon.lock"))
    try:
        lock.acquire()
    except AlreadyRunning as e:
        logger.error("Voice Commander already running: %s", e)
        sys.exit(1)
    try:
        build_streaming_daemon(cfg, config_path=Path("config.toml")).run(
            cfg.hotkey.key,
            dictation_key=cfg.hotkey.dictation_key,
        )
    finally:
        lock.release()


if __name__ == "__main__":
    main()
