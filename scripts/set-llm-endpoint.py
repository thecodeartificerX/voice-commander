"""set-llm-endpoint.py — update [llm] endpoint_url + model_id in config.toml.

Usage:
    uv run python scripts/set-llm-endpoint.py <endpoint_url> <model_id>

Examples:
    uv run python scripts/set-llm-endpoint.py http://localhost:1234/v1 google/gemma-4-e4b
    uv run python scripts/set-llm-endpoint.py http://192.168.4.200:5050/v1 gemma4:e4b

Patches `endpoint_url = "..."` and `model_id = "..."` under [llm]. Preserves
all other lines, comments, and sections. Validates via Config.load() and
rolls back on failure.

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


def _patch_lines(lines: list[str], endpoint_url: str, model_id: str) -> list[str]:
    """Patch endpoint_url + model_id under [llm]. Insert if section missing."""
    section_re = re.compile(r"^\[")
    endpoint_re = re.compile(r'^(endpoint_url\s*=\s*)"[^"]*"(\s*(?:#.*)?)$')
    model_re = re.compile(r'^(model_id\s*=\s*)"[^"]*"(\s*(?:#.*)?)$')

    in_section = False
    section_found = False
    section_header_idx = -1
    endpoint_replaced = False
    model_replaced = False

    out: list[str] = []
    for line in lines:
        stripped = line.rstrip("\n").rstrip("\r")
        if section_re.match(stripped):
            in_section = stripped.strip() == "[llm]"
            if in_section:
                section_found = True
                section_header_idx = len(out)

        if in_section:
            ending = line[len(stripped) :]
            if not endpoint_replaced:
                m = endpoint_re.match(stripped)
                if m:
                    out.append(f'{m.group(1)}"{endpoint_url}"{m.group(2)}{ending}')
                    endpoint_replaced = True
                    continue
            if not model_replaced:
                m = model_re.match(stripped)
                if m:
                    out.append(f'{m.group(1)}"{model_id}"{m.group(2)}{ending}')
                    model_replaced = True
                    continue
        out.append(line)

    if not section_found:
        if out and not out[-1].endswith("\n"):
            out[-1] = out[-1] + "\n"
        if out and out[-1].strip() != "":
            out.append("\n")
        out.append("[llm]\n")
        out.append(f'endpoint_url = "{endpoint_url}"\n')
        out.append(f'model_id = "{model_id}"\n')
        return out

    insert_at = section_header_idx + 1
    if not endpoint_replaced:
        out.insert(insert_at, f'endpoint_url = "{endpoint_url}"\n')
        insert_at += 1
    if not model_replaced:
        out.insert(insert_at, f'model_id = "{model_id}"\n')

    return out


def main() -> int:
    if len(sys.argv) != 3:
        return fail("usage: set-llm-endpoint.py <endpoint_url> <model_id>")

    endpoint_url = sys.argv[1].strip()
    model_id = sys.argv[2].strip()
    if not endpoint_url:
        return fail("endpoint_url must be non-empty")
    if not model_id:
        return fail("model_id must be non-empty")

    original_text: str | None = None
    if not CONFIG_PATH.exists():
        try:
            _atomic_write(
                CONFIG_PATH,
                f'[llm]\nendpoint_url = "{endpoint_url}"\nmodel_id = "{model_id}"\n',
            )
        except Exception as exc:
            return fail(f"write failed: {exc}")
    else:
        with CONFIG_PATH.open("r", encoding="utf-8", newline="") as fh:
            original_text = fh.read()
        lines = original_text.splitlines(keepends=True)
        new_lines = _patch_lines(lines, endpoint_url, model_id)
        try:
            _atomic_write(CONFIG_PATH, "".join(new_lines))
        except Exception as exc:
            return fail(f"write failed: {exc}")

    try:
        from voice_commander.config import Config

        cfg = Config.load(CONFIG_PATH)
        if cfg.llm.endpoint_url != endpoint_url:
            raise ValueError(
                f"wrote endpoint_url={endpoint_url!r} but Config.load read "
                f"{cfg.llm.endpoint_url!r}"
            )
        if cfg.llm.model_id != model_id:
            raise ValueError(
                f"wrote model_id={model_id!r} but Config.load read {cfg.llm.model_id!r}"
            )
    except Exception as exc:
        if original_text is not None:
            with contextlib.suppress(OSError):
                _atomic_write(CONFIG_PATH, original_text)
        return fail(f"post-write validation failed: {exc}")

    print(f"llm.endpoint_url={endpoint_url} model_id={model_id} in {CONFIG_PATH.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
