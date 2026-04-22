# Sprite Companion — Character Sheet

## How to regenerate

Use your preferred image generator with this prompt template:

> A pixel-art character sheet grid for a small desktop assistant sprite.
> 128×128 pixel frames, 6 columns wide, 16 rows tall (total 768×2048 PNG).
> Transparent background (RGBA).
>
> Row 0: idle — gentle breathing loop (4 frames)
> Row 1: listening — ears perked, slight glow (6 frames)
> Row 2: hearing speech — mouth open, sound waves (4 frames)
> Row 3: thinking — spinning gear above head (6 frames)
> Row 4: LLM thinking — brain glow + sparkles (6 frames)
> Row 5: success — happy bounce + checkmark (3 frames)
> Row 6: miss — confused head tilt + question mark (3 frames)
> Row 7: tool error — red flash + exclamation (3 frames)
> Row 8: warmup — loading spinner (4 frames)
> Row 9: crashed — greyed out, X eyes (2 frames)
> Rows 10-15: transition animations (see charsheet.toml)

Save the output as `charsheet.png` in this directory. Run the daemon
to validate that all rows/frames fit within the PNG bounds.
