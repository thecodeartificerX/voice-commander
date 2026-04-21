from __future__ import annotations

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


def _log_environment() -> None:
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


def main() -> None:
    cfg = Config.load(Path("config.toml"))
    _configure_logging(cfg)
    _enable_crash_reporting(cfg)
    _log_environment()
    lock = SingleInstanceLock(Path("outputs/.daemon.lock"))
    try:
        lock.acquire()
    except AlreadyRunning as e:
        logger.error("Voice Commander already running: %s", e)
        sys.exit(1)
    try:
        build_streaming_daemon(cfg).run(cfg.hotkey.key, mute_key=cfg.hotkey.mute_key)
    finally:
        lock.release()


if __name__ == "__main__":
    main()
