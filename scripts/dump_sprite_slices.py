"""Dump every state + transition from the charsheet as a labeled strip PNG.

Usage: uv run python scripts/dump_sprite_slices.py

Writes one PNG per state to outputs/sprite_debug/<state>.png — each strip
shows frame_index 0..N-1 left-to-right, scaled 4x with NEAREST so pixel-
level misalignment is obvious. Also writes _all_rows.png: the full sheet
with a red grid overlay at the configured cell size so you can spot
row/column misregistration.

Eyeball check:
- Each strip should contain clean, centered cats (one per frame).
- If a cat is half in one cell and half in the next → frame_width wrong.
- If the strip shows the wrong pose → row index wrong in charsheet.toml.
- The grid overlay should land exactly on pose boundaries.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets" / "sprite"
OUT = ROOT / "outputs" / "sprite_debug"
SCALE = 4  # upscale for eyeballing


def load_toml() -> dict:
    with (ASSETS / "charsheet.toml").open("rb") as f:
        return tomllib.load(f)


def extract_cell(sheet: Image.Image, col: int, row: int, w: int, h: int) -> Image.Image:
    """Top-left origin, top-down rows. PIL crop is top-down so no flip."""
    left = col * w
    top = row * h
    return sheet.crop((left, top, left + w, top + h))


def build_strip(sheet: Image.Image, row: int, frames: int, w: int, h: int) -> Image.Image:
    """Side-by-side strip of frames 0..frames-1 on `row`, with frame indices above."""
    margin_top = 14
    gap = 2
    strip_w = frames * w + (frames - 1) * gap
    strip = Image.new("RGBA", (strip_w, h + margin_top), (32, 32, 32, 255))
    draw = ImageDraw.Draw(strip)
    try:
        font = ImageFont.truetype("arial.ttf", 10)
    except OSError:
        font = ImageFont.load_default()
    for i in range(frames):
        cell = extract_cell(sheet, col=i, row=row, w=w, h=h)
        x = i * (w + gap)
        strip.paste(cell, (x, margin_top), cell)
        draw.text((x + 2, 0), f"#{i}", fill=(200, 200, 200), font=font)
    # upscale NEAREST for pixel-perfect eyeballing
    return strip.resize(
        (strip.width * SCALE, strip.height * SCALE),
        Image.Resampling.NEAREST,
    )


def build_grid_overlay(sheet: Image.Image, w: int, h: int) -> Image.Image:
    """Full sheet with red grid lines at cell boundaries + row-number labels."""
    overlay = sheet.copy().convert("RGBA")
    draw = ImageDraw.Draw(overlay)
    sw, sh = overlay.size
    try:
        font = ImageFont.truetype("arial.ttf", 10)
    except OSError:
        font = ImageFont.load_default()
    # vertical lines (column boundaries)
    for x in range(0, sw + 1, w):
        draw.line([(x, 0), (x, sh)], fill=(255, 0, 0, 180), width=1)
    # horizontal lines (row boundaries) + row number
    for y in range(0, sh + 1, h):
        draw.line([(0, y), (sw, y)], fill=(255, 0, 0, 180), width=1)
        row_idx = y // h
        if y < sh:
            draw.text((2, y + 1), f"r{row_idx}", fill=(255, 255, 0), font=font)
    return overlay


def main() -> int:
    cs = load_toml()
    w = cs["frame_width"]
    h = cs["frame_height"]
    print(f"[dump] cell = {w}x{h}")

    sheet = Image.open(ASSETS / "charsheet.png").convert("RGBA")
    print(f"[dump] sheet = {sheet.size[0]}x{sheet.size[1]}")

    OUT.mkdir(parents=True, exist_ok=True)
    # cleanup stale
    for p in OUT.glob("*.png"):
        p.unlink()

    # states
    for name, entry in cs.get("states", {}).items():
        row = entry["row"]
        frames = entry["frames"]
        sw = entry.get("frame_width", w)
        sh = entry.get("frame_height", h)
        strip = build_strip(sheet, row, frames, sw, sh)
        out_path = OUT / f"state_{name}_row{row}_f{frames}.png"
        strip.save(out_path)
        print(f"  state.{name:<15} row={row:>2} frames={frames} cell={sw}x{sh} -> {out_path.name}")

    # transitions
    for key, entry in cs.get("transitions", {}).items():
        row = entry["row"]
        frames = entry["frames"]
        sw = entry.get("frame_width", w)
        sh = entry.get("frame_height", h)
        strip = build_strip(sheet, row, frames, sw, sh)
        safe_key = key.replace("->", "_to_")
        out_path = OUT / f"trans_{safe_key}_row{row}_f{frames}.png"
        strip.save(out_path)
        print(f"  trans.{key:<30} row={row:>2} frames={frames} -> {out_path.name}")

    # grid overlay
    grid = build_grid_overlay(sheet, w, h)
    grid_path = OUT / "_grid_overlay.png"
    grid.save(grid_path)
    print(f"[dump] grid overlay -> {grid_path.name}")

    print(f"\n[dump] done. open: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
