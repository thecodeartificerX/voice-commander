# DaVinci Resolve — Edit Page Default Windows Shortcuts (vendored)

Researched 2026-05-23 for `modes/video.toml`. DaVinci Resolve 18–20, Edit page,
default Windows bindings. Shortcuts are user-customisable (Ctrl+Alt+K) and vary
slightly across versions; this is the default set the starter mode targets.

| Action | Combo | Notes |
|---|---|---|
| Split / blade / razor at playhead | `ctrl+b` | Splits across all tracks |
| Undo | `ctrl+z` | |
| Redo | `ctrl+shift+z` | Resolve uses Ctrl+Shift+Z, not Ctrl+Y |
| Ripple delete (close gap) | `delete` | Windows **forward Delete** ripples; **Backspace** lifts (leaves a gap) |
| Lift / delete (leave gap) | `backspace` | for reference; not bound in the starter |
| Previous edit point | `up` | |
| Next edit point | `down` | |
| Play / pause | `space` | single toggle (JKL: J reverse, K stop, L play forward) |
| Number keys 1/2/3 | `1` / `2` / `3` | literal — the user has bound custom shortcuts to these |

Sources:
- Blackmagic DaVinci Resolve manual (JKL transport; Undo/Redo).
- Evercast / Simon Says AI / StoryBlocks / Pixflow shortcut references.
