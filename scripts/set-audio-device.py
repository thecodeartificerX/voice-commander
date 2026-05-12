"""set-audio-device.py — update [audio] device_name in config.toml.

Usage:
    uv run python scripts/set-audio-device.py <device_index> <device_name>

Writes *only* ``device_name`` to config.toml via ``update_user_config``.
The ``device_index`` argument is accepted for backward compatibility with
``start.ps1`` (which still passes the index), but is NOT written to the
config — the int PortAudio index is a non-authoritative cache per ADR 0081.

``device_name`` is required.  If it is absent or blank this script exits 1.

Exit codes:
    0  success
    1  error (message printed to stderr)
"""

import sys
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config.toml"


def fail(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 1


def main() -> int:
    if len(sys.argv) not in (2, 3):
        return fail("usage: set-audio-device.py <device_index> <device_name>")

    # Validate index arg for compat (but we don't use it).
    try:
        _index = int(sys.argv[1])
    except ValueError:
        return fail(f"device_index must be an integer, got: {sys.argv[1]!r}")

    new_name: str = sys.argv[2].strip() if len(sys.argv) == 3 else ""
    if not new_name:
        return fail("device_name is required and must not be empty (ADR 0081: use device_name, not index)")

    # Import here so the script can be invoked via `uv run python` without
    # the package installed in editable mode failing at import time.
    try:
        from voice_commander.config import update_user_config
    except ImportError as exc:
        return fail(f"could not import voice_commander.config: {exc}")

    try:
        update_user_config(CONFIG_PATH, {"audio": {"device_name": new_name}})
    except Exception as exc:
        return fail(f"write failed: {exc}")

    # Post-write validation — read back and confirm.
    try:
        import tomllib
        with CONFIG_PATH.open("rb") as fh:
            parsed = tomllib.load(fh)
        actual_name = parsed.get("audio", {}).get("device_name")
        if actual_name != new_name:
            return fail(
                f"validation failed: expected device_name={new_name!r}, got {actual_name!r}"
            )
        # Confirm the legacy int key was NOT written.
        if "device" in parsed.get("audio", {}):
            return fail(
                "validation failed: legacy 'device' key found in audio section after write "
                "(should have been stripped by update_user_config)"
            )
    except Exception as exc:
        return fail(f"post-write validation failed: {exc}")

    print(f'audio.device_name set to "{new_name}" in {CONFIG_PATH.name}')
    return 0


if __name__ == "__main__":
    sys.exit(main())
