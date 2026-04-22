# ADR 0050: Pyglet Transparent Overlay on Windows — Canonical Recipe

**Date:** 2026-04-22
**Status:** Accepted

## Context

ADR 0046 chose pyglet for the sprite companion window, on the assumption
that `WINDOW_STYLE_OVERLAY + alpha_size=8 + glClearColor(0,0,0,0) + GL_BLEND`
would "just work" on Windows. It did not. The window rendered with an
opaque black square around the cat on Windows 11. Six independent traps
all produce that exact symptom and have to be mitigated together.

## Decision

Pin the canonical recipe in code and docs. Specifically:

1. **Set `glClearColor(0,0,0,0)` inside `on_draw` every frame**, not just
   once in `__init__`. Pyglet can reset the clear color between frames;
   setting it once at init is insufficient on Windows (pyglet issue #1271).
2. **Set `sample_buffers=0, samples=0` in the `gl.Config`** to prevent the
   driver silently downgrading to a multisampled framebuffer without alpha.
3. **Use `glBlendFuncSeparate(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA, GL_ONE,
   GL_ONE_MINUS_SRC_ALPHA)`** so the framebuffer alpha accumulates as
   `src_α + dst_α*(1-src_α)` rather than collapsing toward zero.
4. **Set `blend_src` / `blend_dest` on every `pyglet.sprite.Sprite` instance**
   — sprites in pyglet 2.x do not inherit the window-level blend state.
5. **Do NOT call `SetLayeredWindowAttributes` from user code.** Pyglet's
   `WINDOW_STYLE_OVERLAY` already calls it internally with alpha=255 in a
   deliberate dance with `DwmEnableBlurBehindWindow`. A second call switches
   the window into constant-alpha mode, which disables per-pixel alpha.
6. **Do NOT call `DwmExtendFrameIntoClientArea` for `WS_POPUP` overlays.**
   It requires non-client area (title bar / borders) to extend and returns
   `E_INVALIDARG` for borderless windows.
7. **Log `window.context.config.alpha_size` at startup.** If the driver
   silently refused alpha (iGPU, RDP, hybrid GPU regressions), no Python-
   side fix can recover — the user needs to swap GPU / driver / session, or
   the sprite must fall back to a CPU `UpdateLayeredWindow` blit. The log
   line is the first diagnostic we look at when transparency regresses.

## Consequences

- `src/voice_sprite/window.py` centralises items 1–4 and 7.
- `src/voice_sprite/win32_flags.py` is pared down to items 5–6: only the
  extras pyglet doesn't set (`WS_EX_TOOLWINDOW`, `WS_EX_NOACTIVATE`,
  `HWND_TOPMOST`). Per-pixel alpha is pyglet's job; we must not fight it.
- Any future pyglet upgrade must re-verify the six traps still hold —
  pyglet 2.1 semantics for `WINDOW_STYLE_OVERLAY` are not covered by their
  test suite on Windows 11, so behaviour can drift. Regression detection
  is the startup `alpha_size=…` log line plus a visual eyeball of the
  sprite against a coloured desktop background.

## Rejected alternatives

- **`WINDOW_STYLE_TRANSPARENT`.** Gives per-pixel alpha but adds a title
  bar and border, which a companion overlay should not have. Also accepts
  mouse events, defeating the click-through requirement.
- **Manual `UpdateLayeredWindow` + CPU blit.** Bypasses pyglet's rendering
  path entirely and forces every frame through a GDI blit; 2–5× more CPU
  at 60 fps than the GL path. Held in reserve for the case where the
  startup `alpha_size` log reports 0 on the user's GPU.
- **`glBlendFunc` plain (not `Separate`).** Produces dark halos on
  semi-transparent sprite edges because output alpha collapses toward 0
  during compositing. Incompatible with DWM per-pixel alpha.

## See also

- Gotcha §23 for the per-trap breakdown and the exact log line to audit.
- Gotcha §24 for the charsheet cell-size detection (unrelated to
  transparency but surfaced during the same debugging session).
- ADR 0046 for the original "pyglet over Tkinter / PyQt / web" choice.
- Pyglet issue #1271 (blend modes on transparent windows), #693 (Win11
  transparent windows not working).
