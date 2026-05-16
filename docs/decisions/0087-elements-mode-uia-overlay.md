# 0087 — Elements mode: UIA element-click overlay

## Status

Accepted.

## Context

Clicking arbitrary on-screen UI by voice previously required authoring a
command graph per target. Users wanted a generic "click that thing" mode for
both native Windows apps and browser page content.

## Decision

Add a one-shot "elements" mode. Saying "element"/"elements" during an open
session scans the foreground window, draws a numbered hint overlay on every
clickable control, and left-clicks the control whose number the user speaks.

- **Capture API: Windows UI Automation.** It is the only API exposing native
  controls *and* browser page trees. OCR cannot distinguish a button from text.
- **Click method: coordinate click.** The clicker moves the mouse to the
  element's center and left-clicks via pyautogui — identical to a human click
  and to the existing `click` primitive. It never depends on an element
  implementing a UIA pattern (browser links frequently do not).
- **Lifecycle: one-shot.** One entry word yields one click, then the mode
  exits. No sticky mode, no explicit cancel word — any non-number utterance or
  an 8s timeout dismisses the overlay.
- **Scope: foreground window only.** Bounds scan cost and overlay clutter, and
  keeps the overlay on a single monitor with uniform DPI.
- **Overlay: transparent click-through pyglet window** in the sprite process,
  driven by `elements.show` / `elements.hide` SSE events (consistent with
  ADR 0045 / 0048 / 0050).

## Consequences

- New `voice_commander.elements` package: scanner, session, numbers, clicker,
  desktop helpers. New `voice_sprite/elements_overlay.py`.
- New `uiautomation` dependency.
- **Browser page content is best-effort.** Chrome/Edge expose their renderer
  accessibility tree to UIA only when launched with
  `--force-renderer-accessibility`, or after another assistive client has
  activated it. Native app UI works unconditionally. This is a documented
  limitation, not a defect.
- Coordinate clicks can miss if the UI moves between scan and click; accepted
  because the mode completes within seconds.
