"""set-transcription-backend.py — update [transcription] backend + remote_endpoint_url.

Usage:
    uv run python scripts/set-transcription-backend.py local
    uv run python scripts/set-transcription-backend.py remote http://192.168.4.200:8765/inference

Patches `backend = "..."` (and `remote_endpoint_url = "..."` when supplied)
under [transcription] in config.toml. Preserves all other lines, comments,
and sections. Validates the result via Config.load() so a bad combination
(e.g. remote without URL) fails before launch instead of at daemon start.

Exit codes:
    0  success
    1  error (message printed to stderr)
"""

from __future__ import annotations

import contextlib
import os
import re
import sys
import tempfile
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config.toml"

_VALID_BACKENDS = ("local", "remote")


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


def _patch_lines(
    lines: list[str],
    backend: str,
    remote_url: str | None,
) -> list[str]:
    """Patch backend / remote_endpoint_url under [transcription].

    If [transcription] is absent, append it. If keys are absent inside the
    section, insert them right after the section header. Preserves trailing
    comments on patched lines.
    """
    section_re = re.compile(r"^\[")
    backend_re = re.compile(r'^(backend\s*=\s*)"[^"]*"(\s*(?:#.*)?)$')
    url_re = re.compile(r'^(remote_endpoint_url\s*=\s*)"[^"]*"(\s*(?:#.*)?)$')

    in_section = False
    section_found = False
    section_header_idx = -1
    backend_replaced = False
    url_replaced = False

    out: list[str] = []
    for line in lines:
        stripped = line.rstrip("\n").rstrip("\r")
        if section_re.match(stripped):
            in_section = stripped.strip() == "[transcription]"
            if in_section:
                section_found = True
                section_header_idx = len(out)

        if in_section:
            ending = line[len(stripped) :]
            if not backend_replaced:
                m = backend_re.match(stripped)
                if m:
                    out.append(f'{m.group(1)}"{backend}"{m.group(2)}{ending}')
                    backend_replaced = True
                    continue
            if remote_url is not None and not url_replaced:
                m = url_re.match(stripped)
                if m:
                    out.append(f'{m.group(1)}"{remote_url}"{m.group(2)}{ending}')
                    url_replaced = True
                    continue
        out.append(line)

    if not section_found:
        if out and not out[-1].endswith("\n"):
            out[-1] = out[-1] + "\n"
        if out and out[-1].strip() != "":
            out.append("\n")
        out.append("[transcription]\n")
        out.append(f'backend = "{backend}"\n')
        if remote_url is not None:
            out.append(f'remote_endpoint_url = "{remote_url}"\n')
        return out

    insert_at = section_header_idx + 1
    if not backend_replaced:
        out.insert(insert_at, f'backend = "{backend}"\n')
        insert_at += 1
    if remote_url is not None and not url_replaced:
        out.insert(insert_at, f'remote_endpoint_url = "{remote_url}"\n')

    return out


def main() -> int:
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        return fail("usage: set-transcription-backend.py <local|remote> [remote_endpoint_url]")

    backend = sys.argv[1].strip().lower()
    if backend not in _VALID_BACKENDS:
        return fail(f"backend must be one of {_VALID_BACKENDS}, got {backend!r}")

    remote_url: str | None = None
    if len(sys.argv) == 3:
        remote_url = sys.argv[2].strip()
        if not remote_url:
            return fail("remote_endpoint_url, when supplied, must be non-empty")

    if backend == "remote" and remote_url is None:
        # Allow re-using whatever URL is already in config.toml; Config.load()
        # validation below will fail loudly if neither this script nor the
        # existing file supplies one.
        pass

    original_text: str | None = None
    if not CONFIG_PATH.exists():
        seed = ['[transcription]\n', f'backend = "{backend}"\n']
        if remote_url is not None:
            seed.append(f'remote_endpoint_url = "{remote_url}"\n')
        try:
            _atomic_write(CONFIG_PATH, "".join(seed))
        except Exception as exc:
            return fail(f"write failed: {exc}")
    else:
        with CONFIG_PATH.open("r", encoding="utf-8", newline="") as fh:
            original_text = fh.read()
        lines = original_text.splitlines(keepends=True)
        new_lines = _patch_lines(lines, backend, remote_url)
        try:
            _atomic_write(CONFIG_PATH, "".join(new_lines))
        except Exception as exc:
            return fail(f"write failed: {exc}")

    # Validate via Config.load — catches "remote without URL" early. On
    # failure, restore the original file so the daemon can still start.
    try:
        from voice_commander.config import Config

        cfg = Config.load(CONFIG_PATH)
        if cfg.transcription.backend != backend:
            raise ValueError(
                f"wrote backend={backend!r} but Config.load read {cfg.transcription.backend!r}"
            )
        if remote_url is not None and cfg.transcription.remote_endpoint_url != remote_url:
            raise ValueError(
                f"wrote remote_endpoint_url={remote_url!r} but Config.load read "
                f"{cfg.transcription.remote_endpoint_url!r}"
            )
    except Exception as exc:
        if original_text is not None:
            with contextlib.suppress(OSError):
                _atomic_write(CONFIG_PATH, original_text)
        return fail(f"post-write validation failed: {exc}")

    if backend == "remote":
        url_for_log = remote_url or cfg.transcription.remote_endpoint_url
        print(f"transcription.backend set to remote @ {url_for_log} in {CONFIG_PATH.name}")
    else:
        print(f"transcription.backend set to local in {CONFIG_PATH.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
