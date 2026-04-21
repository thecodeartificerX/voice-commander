from __future__ import annotations

import argparse
import dataclasses
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


def apply_router_mode(cfg: Config, mode: str) -> Config:
    """Return a (possibly new) Config adjusted for the requested routing strategy.

    hybrid (default) — unchanged; rapidfuzz first, LLM fallback if enabled.
    fuzzy            — disable LLM router; rapidfuzz only.
    llm              — set matching.threshold = 101.0 so every utterance goes to
                       the LLM router. Requires endpoint_url and model_id to be
                       configured; enables llm_router even if it was disabled.
    """
    if mode == "hybrid":
        return cfg
    if mode == "fuzzy":
        new_llm = dataclasses.replace(cfg.llm_router, enabled=False)
        return dataclasses.replace(cfg, llm_router=new_llm)
    if mode == "llm":
        router = cfg.llm_router
        if not router.endpoint_url or not router.model_id:
            raise RuntimeError(
                "--router-mode llm requires [llm_router] section with "
                "endpoint_url and model_id populated in config"
            )
        new_llm = dataclasses.replace(router, enabled=True)
        new_matching = dataclasses.replace(cfg.matching, threshold=101.0)
        return dataclasses.replace(cfg, matching=new_matching, llm_router=new_llm)
    raise ValueError(f"unknown router mode: {mode}")


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
    parser.add_argument(
        "--router-mode",
        choices=["hybrid", "fuzzy", "llm"],
        default="hybrid",
        metavar="MODE",
        dest="router_mode",
        help=(
            "Routing strategy: hybrid=rapidfuzz-first with LLM fallback (default), "
            "fuzzy=rapidfuzz only, llm=LLM router only (requires [llm_router] config)."
        ),
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


def _log_router_mode(mode: str, cfg_before: Config, cfg_after: Config) -> None:
    """Emit an INFO line when --router-mode is explicitly set."""
    overrides: list[str] = []
    if cfg_after.matching.threshold != cfg_before.matching.threshold:
        overrides.append(
            f"matching.threshold {cfg_before.matching.threshold} -> {cfg_after.matching.threshold}"
        )
    if cfg_after.llm_router.enabled != cfg_before.llm_router.enabled:
        overrides.append(
            f"llm_router.enabled {cfg_before.llm_router.enabled} -> {cfg_after.llm_router.enabled}"
        )
    if overrides:
        logger.info("router-mode=%s overrides: %s", mode, ", ".join(overrides))
    else:
        logger.info("router-mode=%s (no config overrides)", mode)


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
    parser = _build_parser()
    args = parser.parse_args()

    if args.validate:
        cfg = Config.load(Path("config.toml"))
        _configure_logging(cfg)
        try:
            cfg_after = apply_router_mode(cfg, args.router_mode)
        except (RuntimeError, ValueError) as e:
            logger.error("router-mode error: %s", e)
            sys.exit(1)
        if args.router_mode != "hybrid":
            _log_router_mode(args.router_mode, cfg, cfg_after)
        _run_validate(cfg_after)
        return

    cfg = Config.load(Path("config.toml"))
    _configure_logging(cfg)
    try:
        cfg_after = apply_router_mode(cfg, args.router_mode)
    except (RuntimeError, ValueError) as e:
        logger.error("router-mode error: %s", e)
        sys.exit(1)
    if args.router_mode != "hybrid":
        _log_router_mode(args.router_mode, cfg, cfg_after)
    cfg = cfg_after
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
