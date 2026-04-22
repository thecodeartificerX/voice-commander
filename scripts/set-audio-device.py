"""set-audio-device.py — update [audio] device = N in config.local.toml.

Usage:
    uv run python scripts/set-audio-device.py <device_index>

Writes the device index to config.local.toml (machine-local, gitignored).
Creates the file with an [audio] section if it does not exist. Otherwise
patches the existing `device = ...` line under [audio], or inserts one if
the section is present but has no device line, or appends a new [audio]
section if it is missing. All other lines, comments, and sections are
preserved verbatim.

Exit codes:
    0  success
    1  error (message printed to stderr)
"""

import contextlib
import os
import re
import sys
import tempfile
import tomllib
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config.local.toml"


def fail(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 1


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".toml.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp_path, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def _patch_lines(lines: list[str], new_index: int) -> list[str]:
    """Patch device line under [audio]; insert/append section as needed."""
    device_re = re.compile(r"^(device\s*=\s*)(-?\d+)(\s*(?:#.*)?)$")
    section_re = re.compile(r"^\[")

    in_audio = False
    audio_found = False
    replaced = False
    audio_header_idx = -1
    out: list[str] = []

    for _idx, line in enumerate(lines):
        stripped = line.rstrip("\n").rstrip("\r")
        if section_re.match(stripped):
            in_audio = stripped.strip() == "[audio]"
            if in_audio:
                audio_found = True
                audio_header_idx = len(out)
        if in_audio and not replaced:
            m = device_re.match(stripped)
            if m:
                ending = line[len(stripped) :]
                out.append(f"{m.group(1)}{new_index}{m.group(3)}{ending}")
                replaced = True
                continue
        out.append(line)

    if replaced:
        return out

    if audio_found:
        # Insert after [audio] header.
        insert_at = audio_header_idx + 1
        out.insert(insert_at, f"device = {new_index}\n")
        return out

    # Append section at end; ensure trailing newline separation.
    if out and not out[-1].endswith("\n"):
        out[-1] = out[-1] + "\n"
    if out and out[-1].strip() != "":
        out.append("\n")
    out.extend(["[audio]\n", f"device = {new_index}\n"])
    return out


def main() -> int:
    if len(sys.argv) != 2:
        return fail("usage: set-audio-device.py <device_index>")
    try:
        new_index = int(sys.argv[1])
    except ValueError:
        return fail(f"device_index must be an integer, got: {sys.argv[1]!r}")

    if not CONFIG_PATH.exists():
        try:
            _atomic_write(CONFIG_PATH, f"[audio]\ndevice = {new_index}\n")
        except Exception as exc:
            return fail(f"write failed: {exc}")
    else:
        with CONFIG_PATH.open("r", encoding="utf-8", newline="") as fh:
            original_text = fh.read()
        lines = original_text.splitlines(keepends=True)
        new_lines = _patch_lines(lines, new_index)
        try:
            _atomic_write(CONFIG_PATH, "".join(new_lines))
        except Exception as exc:
            return fail(f"write failed: {exc}")

    try:
        with CONFIG_PATH.open("rb") as fh:
            parsed = tomllib.load(fh)
        actual = parsed["audio"]["device"]
        if actual != new_index:
            return fail(f"validation failed: wrote {new_index} but read back {actual}")
    except Exception as exc:
        return fail(f"post-write validation failed: {exc}")

    print(f"audio.device set to {new_index} in {CONFIG_PATH.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
