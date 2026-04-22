"""Generate a placeholder charsheet.png for development/testing.

Usage: python scripts/generate-placeholder-charsheet.py
"""
from PIL import Image, ImageDraw

FRAME_W, FRAME_H = 128, 128
COLS = 6
ROWS = 16
img = Image.new("RGBA", (COLS * FRAME_W, ROWS * FRAME_H), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

states = [
    "idle", "listening", "hearing", "thinking", "llm_think",
    "success", "miss", "error", "warmup", "crashed",
]
for row, name in enumerate(states):
    for col in range(COLS):
        x, y = col * FRAME_W, row * FRAME_H
        draw.rectangle(
            [x + 2, y + 2, x + FRAME_W - 2, y + FRAME_H - 2],
            outline=(100, 200, 100, 200),
            width=2,
        )
        draw.text((x + 10, y + 10), f"{name}\nf{col}", fill=(255, 255, 255, 200))

img.save("assets/sprite/charsheet.png")
print("Placeholder charsheet.png created")
