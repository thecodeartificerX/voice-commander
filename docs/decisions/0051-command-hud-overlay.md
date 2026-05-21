# 0051. Command HUD Overlay (RPG-Style Chat Log Next to Sprite)

**Status:** Accepted
**Date:** 2026-04-22

## Context

The daemon already emits rich per-step logs and granular SSE events, but
user-facing feedback is limited to the sprite state animation plus a miss
chime. Users cannot glance at the screen to see *what the last command was*
or *whether it succeeded / why it failed*.

## Decision

Render a persistent, RPG-style chat-log overlay directly next to the sprite.
One line per voice command cycle — the one-line summary of what happened,
colour-coded by outcome (green=ok, red=error, amber=miss). Five lines
visible by default, each holding full opacity for 4 s then linearly fading
over 3 s. The HUD lives in the SAME pyglet window as the sprite so sprite
moves (ADR 0053) drag the HUD with them automatically.

Rejected alternatives:
- **Web UI chat panel** — requires the user to have the dashboard tab open;
  poor glanceability.
- **Windows toast notifications** — re-opens the ADR 0013 can of worms.
- **Sprite speech bubble only** — already exists, overwrites previous entries,
  no history.
- **Separate process for HUD** — unnecessary complexity; sprite already owns
  the transparent window and the SSE connection.

## Consequences

- Existing pyglet window grows in size (sprite + HUD + future bubble).
- Introduces a new SSE event type `plan_outcome` (ADR 0052 covers the
  summarization strategy).
- No new processes, no new deps.
- Preserved: miss chimes (ADR 0049), audio-only feedback philosophy (ADR 0013
  — toasts are still banned, on-canvas glyphs are not toasts).
