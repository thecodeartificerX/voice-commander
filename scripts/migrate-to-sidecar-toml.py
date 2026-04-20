#!/usr/bin/env python3
"""Validate that sidecar TOML files match all @tool-decorated functions.

Run after migrating from @tool(phrases=[...]) to bare @tool() + sidecar TOMLs.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add src to path so we can import voice_commander
src = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src))

from voice_commander.registry import discover, get_global_registry, reset_global_registry
from voice_commander.tool_metadata import ToolMetadataStore


def main() -> None:
    reset_global_registry()
    tools_dir = src / "voice_commander" / "tools"
    store = ToolMetadataStore(tools_dir)

    # This will raise ToolMetadataError on mismatch
    registry = discover("voice_commander.tools", store=store)

    all_tools = registry.all()
    print(f"✓ {len(all_tools)} tools registered and paired with TOML metadata:")
    for t in all_tools:
        status = "enabled" if t.enabled else "DISABLED"
        print(f"  [{status:>8}] {t.name:20s} ({', '.join(t.phrases[:3])}{'...' if len(t.phrases) > 3 else ''})")
    print(f"\nMigration validated. All tools have matching sidecar TOML entries.")


if __name__ == "__main__":
    main()
