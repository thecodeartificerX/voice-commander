"""Test fake — writes its own pid then sleeps until killed."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def main() -> int:
    pid_path = Path(sys.argv[1])
    pid_path.write_text(str(os.getpid()))
    while True:
        time.sleep(0.5)


if __name__ == "__main__":
    sys.exit(main())
