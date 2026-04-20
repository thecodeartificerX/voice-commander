from __future__ import annotations

import logging
from pathlib import Path

from .config import Config
from .daemon import build_phase3


def main() -> None:
    cfg = Config.load(Path("config.toml"))
    logging.basicConfig(
        level=cfg.logging.level,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        handlers=[
            logging.FileHandler(cfg.logging.file),
            logging.StreamHandler(),
        ],
    )
    build_phase3(cfg).run(cfg.hotkey.key)


if __name__ == "__main__":
    main()
