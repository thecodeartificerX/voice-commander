# ADR 0046: Pyglet over Tkinter / PyQt / Web Overlay

**Date:** 2026-04-21
**Status:** Accepted

## Context

We need a transparent, always-on-top, click-through, borderless window for the sprite. Candidates: Tkinter, PyQt, Electron overlay, pyglet.

## Decision

pyglet 2.x. It provides:
- Per-pixel alpha transparency via OpenGL.
- Sprite-sheet primitives (TextureGrid, Animation) built-in.
- No heavy framework dependency (Qt = 100+ MB).
- Direct HWND access for Win32 extended style flags.

## Consequences

- Requires OpenGL-capable GPU (universal on modern Windows).
- Sprite-sheet slicing is native; no Pillow at runtime.
- pyglet's event loop runs on the main thread; SSE client runs on a daemon thread.
