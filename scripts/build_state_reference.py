"""Build a single labeled reference PNG showing every sprite state.

One row per state: left = state name + trigger + row/frames, right = all
frames of that state on a checkerboard background, upscaled 3x NEAREST.

Usage: uv run python scripts/build_state_reference.py
Output: outputs/sprite_debug/_state_reference.png
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets" / "sprite"
OUT = ROOT / "outputs" / "sprite_debug"
SCALE = 3

STATE_NOTES: dict[str, str] = {
    "warmup": "daemon booting",
    "idle": "session ended / default",
    "listening": "Scroll Lock pressed, waiting",
    "hearing_speech": "VAD detects speech",
    "thinking": "Whisper + resolver running",
    "llm_thinking": "POST to LM Studio",
    "success": "tool fired (1s hold)",
    "miss": "no plan / low confidence (1s hold)",
    "tool_error": "tool execution failed (1s hold)",
    "crashed": "no daemon heartbeat for 3s",
}


def checker(w: int, h: int, tile: int = 8) -> Image.Image:
    bg = Image.new("RGBA", (w, h), (80, 80, 80, 255))
    d = ImageDraw.Draw(bg)
    for y in range(0, h, tile):
        for x in range(0, w, tile):
            if ((x // tile) + (y // tile)) % 2 == 0:
                d.rectangle([x, y, x + tile - 1, y + tile - 1], fill=(120, 120, 120, 255))
    return bg


def main() -> int:
    with (ASSETS / "charsheet.toml").open("rb") as f:
        cs = tomllib.load(f)
    gw = cs["frame_width"]
    gh = cs["frame_height"]
    states = cs["states"]
    sheet = Image.open(ASSETS / "charsheet.png").convert("RGBA")

    try:
        font = ImageFont.truetype("arial.ttf", 14)
        font_small = ImageFont.truetype("arial.ttf", 10)
    except OSError:
        font = ImageFont.load_default()
        font_small = ImageFont.load_default()

    label_col_w = 280
    gap = 4
    row_pad = 12
    bg_color = (24, 24, 24, 255)
    fg_color = (240, 240, 240, 255)
    dim_color = (170, 170, 170, 255)

    rows_imgs: list[Image.Image] = []

    for name, entry in states.items():
        row = entry["row"]
        frames = entry["frames"]
        fw = entry.get("frame_width", gw)
        fh = entry.get("frame_height", gh)

        strip_w_src = frames * fw + (frames - 1) * gap
        strip_src = Image.new("RGBA", (strip_w_src, fh), (0, 0, 0, 0))
        for i in range(frames):
            cell = sheet.crop((i * fw, row * fh, (i + 1) * fw, (row + 1) * fh))
            bg = checker(fw, fh)
            bg.alpha_composite(cell)
            strip_src.paste(bg, (i * (fw + gap), 0))
        strip = strip_src.resize(
            (strip_src.width * SCALE, strip_src.height * SCALE),
            Image.Resampling.NEAREST,
        )

        row_h = max(strip.height, fh * SCALE) + row_pad * 2
        row_w = label_col_w + strip.width + row_pad * 2
        row_img = Image.new("RGBA", (row_w, row_h), bg_color)
        d = ImageDraw.Draw(row_img)

        # label column
        x, y = row_pad, row_pad
        d.text((x, y), name, fill=fg_color, font=font)
        y += 20
        d.text((x, y), STATE_NOTES.get(name, ""), fill=dim_color, font=font_small)
        y += 14
        d.text(
            (x, y),
            f"row={row}  frames={frames}  cell={fw}x{fh}",
            fill=dim_color,
            font=font_small,
        )

        # strip
        row_img.paste(strip, (label_col_w, row_pad), strip)
        rows_imgs.append(row_img)

    total_h = sum(r.height for r in rows_imgs) + gap * (len(rows_imgs) - 1)
    total_w = max(r.width for r in rows_imgs)
    out = Image.new("RGBA", (total_w, total_h), bg_color)
    y = 0
    for r in rows_imgs:
        out.paste(r, (0, y))
        y += r.height + gap

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "_state_reference.png"
    out.save(path)
    print(f"wrote {path}  ({out.width}x{out.height})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
