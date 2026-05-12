from __future__ import annotations

import argparse
import faulthandler
import logging
import os
import platform
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import Config
from .daemon import build_streaming_daemon
from .single_instance import AlreadyRunning, SingleInstanceLock

logger = logging.getLogger(__name__)


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
            mute_key=cfg.hotkey.mute_key,
        )
    finally:
        lock.release()


if __name__ == "__main__":
    main()
