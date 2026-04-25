"""Test fake — sleeps briefly, then exits with the code from a fixture file.

Usage::

    python scripts/_fake_daemon.py <fixture-file>

The fixture file is read on each invocation; the first line is consumed and
written back. So a fixture of `75\n75\n0\n` produces three successive runs
that exit 75, 75, 0.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path


def main() -> int:
    fixture = Path(sys.argv[1])
    lines = fixture.read_text().splitlines()
    code = int(lines[0])
    fixture.write_text("\n".join(lines[1:]) + ("\n" if lines[1:] else ""))
    time.sleep(0.1)  # represent real work
    return code


if __name__ == "__main__":
    sys.exit(main())
