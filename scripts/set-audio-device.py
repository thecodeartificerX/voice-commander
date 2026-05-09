"""set-audio-device.py — update [audio] device = N (and optionally device_name) in config.toml.

Usage:
    uv run python scripts/set-audio-device.py <device_index> [device_name]

Writes the device index (and optional device name) to config.toml (tracked,
single source of truth).  Creates the file with an [audio] section if it does
not exist.  Otherwise patches the existing `device = ...` (and `device_name =
"..."`) lines under [audio], or inserts them if the section is present but
lacks those keys, or appends a new [audio] section if it is missing entirely.
All other lines, comments, and sections are preserved verbatim.

When called with only <device_index>, behaviour is identical to before: only
`device` is written and any existing `device_name` line is left untouched.

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

CONFIG_PATH = Path(__file__).parent.parent / "config.toml"


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


def _patch_lines(lines: list[str], new_index: int, new_name: str | None = None) -> list[str]:
    """Patch device (and optionally device_name) line under [audio]; insert/append section as needed."""
    device_re = re.compile(r"^(device\s*=\s*)(-?\d+)(\s*(?:#.*)?)$")
    name_re = re.compile(r'^(device_name\s*=\s*)"[^"]*"(\s*(?:#.*)?)$')
    section_re = re.compile(r"^\[")

    in_audio = False
    audio_found = False
    replaced = False
    name_patched = False
    audio_header_idx = -1
    out: list[str] = []

    for _idx, line in enumerate(lines):
        stripped = line.rstrip("\n").rstrip("\r")
        if section_re.match(stripped):
            in_audio = stripped.strip() == "[audio]"
            if in_audio:
                audio_found = True
                audio_header_idx = len(out)
        if in_audio:
            if not replaced:
                m = device_re.match(stripped)
                if m:
                    ending = line[len(stripped) :]
                    out.append(f"{m.group(1)}{new_index}{m.group(3)}{ending}")
                    replaced = True
                    continue
            if not name_patched:
                mn = name_re.match(stripped)
                if mn:
                    if new_name is not None:
                        ending = line[len(stripped) :]
                        out.append(f'device_name = "{new_name}"{mn.group(2)}{ending}')
                        name_patched = True
                        continue
                    # new_name is None — leave existing line unchanged
        out.append(line)

    if replaced and (new_name is None or name_patched):
        return out

    if audio_found:
        # Insert missing key(s) right after the [audio] header line.
        insert_at = audio_header_idx + 1
        # Insert in reverse order so both land at the same position correctly.
        if not replaced:
            out.insert(insert_at, f"device = {new_index}\n")
            # Adjust insert position for name if we're also inserting device.
            if new_name is not None and not name_patched:
                out.insert(insert_at + 1, f'device_name = "{new_name}"\n')
                name_patched = True
        elif new_name is not None and not name_patched:
            out.insert(insert_at, f'device_name = "{new_name}"\n')
            name_patched = True
        return out

    # Append section at end; ensure trailing newline separation.
    if out and not out[-1].endswith("\n"):
        out[-1] = out[-1] + "\n"
    if out and out[-1].strip() != "":
        out.append("\n")
    new_section = ["[audio]\n", f"device = {new_index}\n"]
    if new_name is not None:
        new_section.append(f'device_name = "{new_name}"\n')
    out.extend(new_section)
    return out


def main() -> int:
    if len(sys.argv) not in (2, 3):
        return fail("usage: set-audio-device.py <device_index> [device_name]")
    try:
        new_index = int(sys.argv[1])
    except ValueError:
        return fail(f"device_index must be an integer, got: {sys.argv[1]!r}")
    new_name: str | None = sys.argv[2].strip() if len(sys.argv) == 3 else None

    if not CONFIG_PATH.exists():
        try:
            content = f"[audio]\ndevice = {new_index}\n"
            if new_name is not None:
                content += f'device_name = "{new_name}"\n'
            _atomic_write(CONFIG_PATH, content)
        except Exception as exc:
            return fail(f"write failed: {exc}")
    else:
        with CONFIG_PATH.open("r", encoding="utf-8", newline="") as fh:
            original_text = fh.read()
        lines = original_text.splitlines(keepends=True)
        new_lines = _patch_lines(lines, new_index, new_name)
        try:
            _atomic_write(CONFIG_PATH, "".join(new_lines))
        except Exception as exc:
            return fail(f"write failed: {exc}")

    try:
        with CONFIG_PATH.open("rb") as fh:
            parsed = tomllib.load(fh)
        actual = parsed.get("audio", {}).get("device")
        if actual != new_index:
            return fail(f"validation failed: wrote {new_index} but read back {actual}")
        if new_name is not None:
            actual_name = parsed.get("audio", {}).get("device_name")
            if actual_name != new_name:
                return fail(
                    f"validation failed: expected device_name={new_name!r}, got {actual_name!r}"
                )
    except Exception as exc:
        return fail(f"post-write validation failed: {exc}")

    msg = f"audio.device set to {new_index}"
    if new_name is not None:
        msg += f', device_name set to "{new_name}"'
    print(f"{msg} in {CONFIG_PATH.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
