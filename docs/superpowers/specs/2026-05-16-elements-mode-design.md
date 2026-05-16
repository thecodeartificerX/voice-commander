# Elements Mode — Voice-Driven Element Click via UIA Overlay

**Date:** 2026-05-16
**Status:** Design approved — pending implementation plan
**Related ADR:** 0087 (to be written) — "elements mode / UIA element-click overlay"
**Prior art in repo:** ADR 0086 (dictation mode), 0045 (sprite via SSE), 0048 (event bus), 0050 (pyglet transparent overlay), 0051 (command HUD), 0066 (perception primitives).

## Summary

A new in-session mode. The user says `"element"` / `"elements"`; the app scans the
foreground window's UI tree, draws a numbered hint tag on every clickable element,
and waits. The user says a number; the app left-clicks that element and exits the
mode. One spoken trigger yields exactly one click ("one-shot" lifecycle).

This gives hands-free clicking of arbitrary on-screen UI — native desktop controls
and browser page content alike — without the user authoring a command graph per
target.

## Goals

- Click any interactive control in the foreground window by voice.
- Cover native Windows app UI **and** browser page content.
- Zero per-target configuration — the mode discovers elements live.
- Fail safe: any error returns to normal command mode; the daemon never crashes.

## Non-goals (v1)

- Right-click / double-click / drag — left-click only.
- Typing into a focused field / handoff to dictation.
- Sticky multi-click mode — v1 is one-shot.
- Icon / image targets with no accessibility name.
- Full-screen OCR fallback — UIA only.
- A dedicated entry hotkey — voice word only.

## Settled decisions

| Topic | Decision | Reason |
|-------|----------|--------|
| Capture API | Windows UI Automation (UIA) | Only API exposing native controls *and* browser page trees. OCR cannot distinguish a button from text. |
| Entry | Voice word only (`"element"` / `"elements"`) | Matches how the user described it; no free hotkey needed. |
| Feedback | Numbered hint overlay (Vimium / Voice Access style) | Most reliable — user reads a number, no guessing element names. |
| Action | Spoken number → left-click | User-chosen minimal scope. |
| Lifecycle | One-shot — exit after one click | User-chosen; simpler, no mode-trap. |
| Click method | Coordinate click (move mouse to element center → `pyautogui` click) | Behaves like a real human click (hover/focus fire); never depends on an element implementing a UIA pattern. Browser links frequently lack `InvokePattern`. |
| Capture lib | `uiautomation` Python package (comtypes-based) | Maintained UIA wrapper. Raw `comtypes` is the fallback if the package proves stale. Latest docs to be vendored to `docs/references/` before code, per repo rule. |

## Architecture

New package `src/voice_commander/elements/`, mirroring `src/voice_commander/dictation/`:

```
elements/
  session.py   ElementsSession  — state machine, owns mode lifecycle
  scanner.py   UIA tree walk → list[Element]
  uia.py       thin UIA wrapper (COM init, control-type constants, walker)
  numbers.py   spoken number → int  ("seven" / "17" / "twenty three" → int)
  clicker.py   click at a screen point (mouse move + pyautogui click)
```

### `Element` value object

```
Element(
  index: int,            # 1-based, assigned in reading order (top→bottom, left→right)
  label: str,            # UIA Name, for logging / overlay debugging
  control_type: str,     # e.g. "Button", "Hyperlink"
  rect: tuple[int,int,int,int],   # (x, y, w, h), virtual-desktop screen coords
  center: tuple[int,int],         # click point
)
```

### Integration points

- **`daemon.py`** — transcript intercept *before* `VerbRouter`:
  - `ElementsSession` idle **and** transcript is an entry word → start session.
  - `ElementsSession` active → the session consumes the transcript; it is **not**
    routed to `VerbRouter`.
- **`config.py`** — new `[elements]` section (see Config).
- **`event_bus`** — two new SSE event types: `elements.show`, `elements.hide`.
- **Sprite / overlay process** — new `ElementsOverlay` pyglet window subscribing to
  those events. Reuses the transparent-overlay recipe (ADR 0050) and the existing
  SSE consumer (ADR 0045 / 0048).
- **`validator.py`** — startup check: UIA / COM available. If not, warn and disable
  the feature (entry word becomes a no-op).

## State machine & data flow

```
IDLE
 │ transcript ∈ entry_words ("element" | "elements")
 ▼
SCANNING ── scanner runs on a worker thread (NOT the pipeline thread —
 │            the VAD / transcription pipeline must never block)
 │           UIA walk of foreground window → filter → cap → index
 │           start chime plays on entry
 │ on success: publish elements.show {elements}
 │ on zero elements / error: miss chime → IDLE
 ▼
HINTS_SHOWN ── overlay draws numbered tags
 │ next transcript:
 ├─ parses to a valid index N  → clicker.click(center_N) → elements.hide → IDLE
 ├─ anything else              → elements.hide → IDLE   (utterance dropped; implicit cancel)
 └─ hint_timeout_s elapsed     → elements.hide → IDLE
```

There is no explicit cancel word (user-chosen minimal scope). Any non-number
utterance while hints are shown is an implicit cancel: the overlay is dismissed,
the utterance is dropped (not re-routed, to avoid an accidental command), and the
mode exits.

### Scanner filter

Foreground window only. Walk descendants via the UIA `ControlViewWalker`.

- **Keep** interactive control types: Button, Hyperlink, MenuItem, ListItem,
  TabItem, CheckBox, RadioButton, ComboBox, Edit, SplitButton, TreeItem.
- **Drop**: `IsOffscreen == True`; empty or zero-area bounding rect; rect outside
  the foreground window bounds.
- Dedup overlapping rects.
- Cap at `max_elements` (default 200) — keeps overlay density and scan cost bounded;
  browser accessibility trees can hold thousands of nodes.
- Hard `scan_timeout_s` (default 3.0s) — on timeout, use partial results if any,
  else treat as zero elements.
- Assign 1-based `index` in reading order: sort by (y, x).

### Number parsing (`numbers.py`)

Spoken transcript → int:

- Lowercase, strip punctuation and whitespace.
- Pure digits (`"17"`) → `int` directly.
- Number words: ones 0–9, teens 10–19, tens 20–90; compound `"twenty three"` and
  hyphenated `"twenty-three"` → 23.
- Strict word map only — no homophone guessing (`"for" → 4` is too error-prone; the
  transcription confidence gate already filters low-quality utterances).
- A parsed value outside `[1, len(elements)]` is invalid → implicit cancel.

### Click execution (`clicker.py`)

- Take the element `center` (virtual-desktop screen coords).
- Move the mouse there, then `pyautogui.click()`, then a ~50ms settle (matches the
  existing `click` primitive).
- The overlay window is `WS_EX_NOACTIVATE` + click-through, so the foreground
  window is unchanged from scan time and the click lands on the real UI.
- Stale-rect risk (UI moved between scan and click): accepted — the mode is
  one-shot and completes within seconds; the click lands at the recorded coords.

## Overlay rendering

Drawn by the sprite / overlay process on `elements.show`; cleared on `elements.hide`.

```
┌─ transparent, click-through, topmost; spans the virtual desktop ──────┐
│  WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOPMOST  │
│                                                                       │
│   [12]┌──────────┐        [3]─link text                               │
│       │  Button  │                                                    │
│       └──────────┘     [7]┌────────┐                                  │
│                           │ field  │                                  │
└───────────────────────────────────────────────────────────────────────┘
```

- Each tag: a small filled box, Vimium colors (yellow background, black text),
  anchored at the element's top-left corner, number centered.
- Overlapping tags: a small offset nudge; minor overlap is accepted (the
  `max_elements` cap keeps density reasonable).

## Error handling

Every failure path plays the miss chime and returns to `IDLE`; the daemon survives.

| Condition | Behavior |
|-----------|----------|
| Desktop focused / no foreground window | miss chime → IDLE |
| Zero interactive elements found | miss chime → IDLE |
| UIA / COM error mid-scan | log + miss chime → IDLE |
| UIA library missing at startup | `validator.py` warns; entry word is a no-op |
| Scan timeout exceeded | use partial results, else treat as zero elements |
| Spoken number out of range | implicit cancel → IDLE |
| Scroll Lock closes the session mid-mode | force reset to IDLE, hide overlay |
| Entry word said during dictation mode | no conflict — dictation buffers audio without transcription, so the word is never seen; the two modes are naturally exclusive |

## Feedback (audio)

- Start chime on entering `SCANNING`.
- Miss chime on any failure path.
- Reuses `assets/sounds/` (`start.wav`, `miss.wav`).

## Config

New `[elements]` section in `config.toml`:

```toml
[elements]
entry_words    = ["element", "elements"]
max_elements   = 200
scan_timeout_s = 3.0
hint_timeout_s = 8.0
```

Interactive control-type whitelist is an internal constant, not user config, to
keep the surface minimal.

## Testing

- **Unit — `numbers.py`**: digits, ones / teens / tens, compound, hyphenated,
  junk input, out-of-range, `"zero"`.
- **Unit — scanner filter**: mock UIA elements → assert filter / dedup / cap /
  reading-order sort.
- **Unit — `ElementsSession`**: transitions — entry word, valid index, invalid
  utterance, timeout (mock scanner + event bus).
- **Unit — `clicker`**: mock `pyautogui`, assert click at element center.
- **Integration**: daemon transcript → entry word → `SCANNING`; index transcript
  → `clicker` fired; non-number transcript → `elements.hide`.
- **Live smoke script** (modeled on the dictation live endpoint test): scan a real
  Notepad / Explorer window, assert numbered elements are found. Manual run.

## Optional / deferred

- Observability spans (`elements scan` / `elements click`) written to `runs.db`
  (ADR 0070) — useful for tracing, can land after v1.
- Hybrid click (try UIA `InvokePattern`, fall back to coordinate click) — natural
  v2 upgrade for off-screen / occluded elements.
- Verb + number (`"right-click 5"`, `"double-click 5"`), type-into-field handoff
  to dictation, sticky multi-click lifecycle — explicit v1 non-goals.

## Open items for the implementation plan

- Vendor latest `uiautomation` / `comtypes` / Windows UIA docs to
  `docs/references/` before writing code (repo rule).
- Write ADR 0087 capturing the UIA-over-OCR and coordinate-click-over-pattern
  decisions.
- Confirm browser accessibility tree exposure: Chrome / Edge expose their page
  tree to UIA once an assistive client attaches — verify the `uiautomation`
  client attach triggers this, and whether a renderer-accessibility flag is needed.
