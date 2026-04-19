"""set-audio-device.py — update [audio] device = N in config.toml.

Usage:
    uv run python scripts/set-audio-device.py <device_index>

Atomically replaces the integer on the `device = ...` line inside the
[audio] section of config.toml.  All other formatting, comments, whitespace,
and sections are left untouched.

Exit codes:
    0  success
    1  error (message printed to stderr)
"""

import os
import re
import sys
import tempfile
import tomllib
from pathlib import Path


CONFIG_PATH = Path(__file__).parent.parent / "config.toml"


def fail(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 1


def main() -> int:
    # ------------------------------------------------------------------ args
    if len(sys.argv) != 2:
        return fail(f"usage: set-audio-device.py <device_index>")

    try:
        new_index = int(sys.argv[1])
    except ValueError:
        return fail(f"device_index must be an integer, got: {sys.argv[1]!r}")

    # ------------------------------------------------------------------ read
    if not CONFIG_PATH.exists():
        return fail(f"config.toml not found at {CONFIG_PATH}")

    original_text = CONFIG_PATH.read_text(encoding="utf-8")
    lines = original_text.splitlines(keepends=True)

    # ------------------------------------------------------------------ edit
    # State machine: find [audio] section, then within it find `device = N`.
    # Stop searching when we hit the next section header.
    in_audio_section = False
    replaced = False
    new_lines = []

    # Matches:  device = -1   device=42   device = 7 # comment
    device_re = re.compile(
        r'^(device\s*=\s*)(-?\d+)(\s*(?:#.*)?)$'
    )
    section_re = re.compile(r'^\[')

    for line in lines:
        stripped = line.rstrip('\n').rstrip('\r')

        if section_re.match(stripped):
            # Any section header toggles audio-section tracking.
            in_audio_section = stripped.strip() == '[audio]'

        if in_audio_section and not replaced:
            m = device_re.match(stripped)
            if m:
                prefix = m.group(1)        # "device = "
                suffix = m.group(3)        # optional whitespace + comment
                # Preserve original line ending
                ending = line[len(stripped):]
                new_lines.append(f"{prefix}{new_index}{suffix}{ending}")
                replaced = True
                continue

        new_lines.append(line)

    if not replaced:
        return fail(
            "could not find 'device = <int>' under [audio] in config.toml"
        )

    new_text = "".join(new_lines)

    # ------------------------------------------------------------------ atomic write
    config_dir = CONFIG_PATH.parent
    fd, tmp_path = tempfile.mkstemp(dir=config_dir, suffix=".toml.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        os.replace(tmp_path, CONFIG_PATH)
    except Exception as exc:
        # Clean up temp file if replace failed.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return fail(f"write failed: {exc}")

    # ------------------------------------------------------------------ validate
    try:
        with CONFIG_PATH.open("rb") as fh:
            parsed = tomllib.load(fh)
        actual = parsed["audio"]["device"]
        if actual != new_index:
            return fail(
                f"validation failed: wrote {new_index} but read back {actual}"
            )
    except Exception as exc:
        return fail(f"post-write validation failed: {exc}")

    print(f"audio.device set to {new_index}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
