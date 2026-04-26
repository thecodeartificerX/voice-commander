#!/usr/bin/env python
"""CLI wrapper around graph_migrate.migrate_legacy_to_graphs.

Usage:
    uv run python scripts/migrate-graphs.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from voice_commander.commands.graph_migrate import migrate_legacy_to_graphs

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    commands = repo_root / "commands.json"
    workflows = repo_root / "workflows.json"

    cmd_n, wf_n = migrate_legacy_to_graphs(commands, workflows)
    print(f"Migrated {cmd_n} commands and {wf_n} workflows.")
    if cmd_n + wf_n == 0:
        print("(Nothing to migrate — already in canonical schema.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
