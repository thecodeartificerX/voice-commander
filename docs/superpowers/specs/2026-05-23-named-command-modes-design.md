# Named Command Modes — Design Spec

- **Date:** 2026-05-23
- **Status:** Draft (awaiting user review)
- **Topic:** Scoped, voice-switchable command catalogs ("modes") — `video`, `mouseless`, …

## Summary

Add a **named command mode** mechanism to the voice daemon. Saying a mode's
trigger word (e.g. `"video"`) inside an active Scroll Lock session enters that
mode; while the mode is active the router matches **only** that mode's command
catalog plus the global primitives — every normal user command is suppressed.
Saying the mode's end phrase (e.g. `"video end"`) returns to normal command
mode. Modes are authored as self-contained per-mode catalog files
(`modes/<name>.toml`). A ready-made `modes/video.toml` ships with DaVinci
Resolve shortcuts pre-authored.

This is a generalisation: `video` is the first mode; `mouseless` and any other
named mode follow the identical pattern, each with its own catalog file.

## Motivation / goals

- Re-bind the same spoken words to different actions depending on context
  (a "video" context drives an editor; a "mouseless" context drives navigation).
- Keep each context isolated so the active vocabulary is small and unambiguous.
- Reuse the existing routing engine wholesale — no new action grammar, no
  bespoke per-mode execution path.
- Ship working, pre-authored content (DaVinci command pack) so the feature is
  usable out of the box.

## Non-goals

- No global matchability for mode commands. Mode commands match **only** inside
  their mode (decided 2026-05-23). They are not added to the normal-mode catalog.
- No Builder UI integration in this iteration. Modes are file-authored. (A future
  extension could surface modes in the Builder; out of scope here.)
- No nested / simultaneous modes. One mode at a time; switching requires exiting
  first (must-end-first).
- No new LLM-visible primitive (the LLM router was removed by ADR 0082; N/A).

## Concepts & terminology

- **Mode** — a named, scoped command catalog with a trigger word and an end phrase.
- **Trigger** — the exact spoken phrase that enters the mode (default: filename stem).
- **End phrase** — the exact spoken phrase that exits the mode (default: `"<trigger> end"`).
- **Mode command** — a `phrases → action` entry in a mode's catalog file.
- **Base router** — the existing primitive/chain/repeat routing logic, reused unchanged.

## § 1 — Catalog file format

One TOML file per mode: `modes/<name>.toml` (TOML matches repo convention —
`config.toml`, tool sidecars). The filename stem is the default trigger word.

```toml
[mode]
trigger    = "video"          # optional; default = filename stem ("video")
end_phrase = "video end"      # optional; default = "<trigger> end"
badge      = "🎬 VIDEO"       # sprite badge text shown while the mode is active

[[command]]
phrases = ["split", "blade", "razor", "cut clip"]   # synonyms (same semantics as graph phrases)
action  = "press ctrl+b"                             # a transcript run through the BASE router

[[command]]
phrases = ["export"]
action  = "chain press ctrl+m wait 500 press enter" # chains work for free
```

### Rules

- **`action` is a transcript string**, routed at load time through the base
  router (primitives + chain + repeat) and the resulting `Plan` is cached. There
  is no new action grammar. Valid examples: `"press ctrl+b"`, `"scroll up"`,
  `"chain copy paste"`, `"go down thrice"`.
  - **Explicit verb only** (decided 2026-05-23): the action must spell out the
    verb (`"press space"`, not `"space"`). No bare-combo shorthand. One grammar,
    identical to the base router.
- **`action` may not reference global commands.** Modes are self-contained and
  portable; only the base primitive/chain/repeat vocabulary is in scope at load.
- **Load fails loud, isolated:** any `action` the base router cannot resolve, a
  malformed file, a reserved trigger, or a duplicate trigger → logged with file +
  phrase, and **that mode is skipped**; the daemon stays up. No silent dead
  commands.
- **`phrases`** reuse the existing synonym / longest-match / underscore-as-space
  / punctuation-tolerant semantics. A mode command beats a primitive on a name
  clash (consistent with normal registered-command precedence).
- **Discovery:** every `modes/*.toml` is auto-loaded on daemon start and
  hot-reloaded on change (rebuild only the changed mode's router).

## § 2 — Routing, precedence, state machine

Add a new voice-session sub-state `ModeSession` (mirrors `ElementsSession` /
`PickerSession`), tracking `active_mode: str | None`. A `ModeRegistry` maps
`name → ModeDefinition{trigger, end_phrase, badge, router}`, where `router` is
that mode's pre-built `VerbRouter` (base rules + chain parser + the mode's own
command registry). **Approach B** (per-mode router instances) — chosen over a
scoped-registry filter or a plain phrase→Plan dict because it isolates modes
cleanly, matches per-mode files + must-end-first exactly, and reuses
`VerbRouter` / registry / chain / repeat / picker unchanged.

### Daemon pipeline order (per utterance)

The mode slot is inserted into the existing sub-state chain:

1. dictation → 2. elements → 3. picker *(unchanged)*
4. **mode sub-state (NEW).** If `active_mode` is set:
   - utterance == active mode's `end_phrase` (exact, normalized) → **exit**:
     exit-chime, clear badge, HUD line `"video mode ended"`, stay in session. Done.
   - else → route through the **mode's** `VerbRouter`.
   - hit → dispatch the plan. **miss → miss-chime, stay in the mode.**
   - Other modes' triggers are **not** checked here (must-end-first).
5. **normal mode (no active mode):**
   - **mode-trigger intercept (NEW, top precedence,** alongside the chain
     intercept**).** utterance == a registered `trigger` (exact, normalized) →
     **enter** that mode: enter-chime, set badge, HUD line `"→ video mode"`. Done.
   - else → existing `VerbRouter.route()` exactly as today.

### Precedence / rules

- Trigger and `end_phrase` match the **whole** normalized utterance, not a head
  word. So in normal mode `"video end"` is not a trigger (the trigger is
  `"video"`); it falls through to a normal miss. No accidental entry.
- A mode trigger beats a same-named global command (intercept sits above normal
  routing). Validation: a `trigger` may not equal a reserved head (`chain`,
  `dictate`, or any primitive verb) — loud error at load, mode skipped.
- **Scroll Lock = hard exit:** ending the session force-resets
  `active_mode = None` and clears the badge. Every new session starts in normal
  mode.
- Mode entry happens only inside an active session (mode is a sub-state of the
  session).

## § 3 — Feedback wiring

- **EventBus:** two new SSE events — `mode.enter {name, badge}` and
  `mode.exit {name, reason}` (mirror `dictation.processing` / `dictation.end`).
- **Sprite:** `StateMachine.active_mode_badge: str | None`, set on `mode.enter`,
  cleared on `mode.exit`. Rendered as a **persistent** badge (full opacity, on
  top) — same draw path as the DICTATING / CANCELLED badges, but it stays until
  exit. A module-level `_apply_mode_badge(sm, window)` helper is called after
  every SSE event, mirroring `_apply_cancelled_cue` / `_apply_dim`.
- **Cat brightness:** unchanged — a mode is in-session, so the cat is already
  bright by the `is_dim` rule (ADR 0097).
- **Chimes:** distinct enter (ascending) and exit (descending) cues added to the
  `FeedbackSink` / `feedback.py`. An in-mode miss reuses the existing miss-chime.
- **HUD:** the existing line renderer gains `mode.enter` / `mode.exit` lines;
  `transcript` / `tool_fired` lines keep flowing.

## § 4 — Config / discovery

- **Location:** `modes/` at repo root, sibling to `commands/`. Path configurable
  via `[modes] dir` (default `"modes"`).
- **config.toml:** new `[modes]` section — `enabled` (default `true`), `dir`.
  Update `config.toml.example` with inline comments.
- **Discovery:** `ModeRegistry` scans `modes/*.toml` on start; hot-reload via the
  existing file-watcher, rebuilding only the changed mode's router.
- **Load errors are loud but isolated** (see § 1): a bad mode is skipped and
  surfaced in logs + `/events`; the daemon stays up.

## § 5 — Built-in starter: `modes/video.toml` (DaVinci Resolve)

Pre-authored by the implementer so the user does not hand-build it; the file is
the source of truth, identical in effect to Builder-authored commands. DaVinci
Resolve **Edit page, default Windows** shortcuts (research vendored to
`docs/references/davinci-resolve-shortcuts.md`).

| Spoken phrase(s)            | Action            | DaVinci meaning                          |
|-----------------------------|-------------------|------------------------------------------|
| split / blade / razor / cut clip | `press ctrl+b` | Split clip at playhead (all tracks)      |
| undo                        | `press ctrl+z`    | Undo                                     |
| redo                        | `press ctrl+shift+z` | Redo (Resolve uses Ctrl+Shift+Z, not Ctrl+Y) |
| one                         | `press 1`         | Literal "1" key — see caveat ①           |
| two                         | `press 2`         | Literal "2" key — see caveat ①           |
| three                       | `press 3`         | Literal "3" key — see caveat ①           |
| ripple / ripple delete      | `press delete`    | Ripple delete (Windows forward-Delete closes the gap) |
| back / previous / last cut  | `press up`        | Move playhead to previous edit point     |
| forward / next / next cut   | `press down`      | Move playhead to next edit point         |
| play                        | `press l`         | JKL transport: L = play forward — caveat ② |
| pause / stop                | `press k`         | JKL transport: K = stop — caveat ②       |

### Caveats to confirm in review

- **① Number keys 1/2/3.** The user asked for "one → number 1", etc. These map to
  the literal `press 1/2/3` keys. Note: in current DaVinci Edit page, **bare**
  number keys have no default action; `Ctrl+1/2/3` focus the Source viewer /
  Timeline viewer / Timeline. If the intent is viewer focus, change these to
  `press ctrl+1` / `ctrl+2` / `ctrl+3`. Default kept as the literal request.
- **② Play / pause.** DaVinci's Spacebar is a single play/pause **toggle**.
  Because the user wants distinct `"play"` and `"pause"` words, this spec maps
  them to the JKL transport (`L` = play forward, `K` = stop) so each word is
  deterministic regardless of current state. Alternative: map both to
  `press space` (toggle).

All combo strings above are valid pyautogui keynames accepted by the `press`
primitive (verified against `tools/keyboard_combo.py`: `+` separator; arrows are
`up`/`down`; `del`→`delete` alias; single letters/digits pass through).

## § 6 — Testing / E2E

- **Unit:** `ModeRegistry` parsing — valid file plus every failure path (bad
  `action`, reserved trigger, duplicate trigger, malformed TOML). `ModeSession`
  state machine — enter / exit / `end_phrase` / miss-stays-in-mode /
  Scroll-Lock-reset. Per-mode router — mode command hit, primitive still live,
  chain + repeat in-mode, normal command suppressed.
- **Integration:** daemon pipeline order — mode slot precedence vs
  dictation / picker / elements.
- **Visual E2E (mandatory — `scripts/mode_visual_e2e.py`),** copying
  `scripts/picker_visual_e2e.py`: subprocess sprite + SSE; `mode.enter` →
  PrintWindow → assert the badge pixels are present; route a mode command →
  assert `tool_fired`; assert a normal command is **suppressed** while in-mode;
  `mode.exit` → assert the badge is gone.

## § 7 — Documentation deliverables (same change as code)

- New ADR: "Named command modes" under `docs/decisions/`.
- Update the **Current state** section of `CLAUDE.md`.
- Add a row to `docs/agents/technical-decisions.md`.
- Update `config.toml.example` (`[modes]` section, inline comments).
- New overview `docs/modes.md` + link from `docs/index.md`.
- Vendor `docs/references/davinci-resolve-shortcuts.md` (research source).
- Ship `modes/video.toml`.

## Decisions log (from brainstorming, 2026-05-23)

1. Generalised **named modes** (not a one-off video mode). ✔
2. In-mode scope = **mode commands + global primitives**; normal user commands
   suppressed; mode command wins clashes. ✔
3. Storage = **per-mode catalog files** (`modes/<name>.toml`). ✔
4. Action format = **same base engine** (transcript → base router), **explicit
   verb only**. ✔
5. Session relationship = **sub-state of an active session**; `"<mode> end"`
   drops to normal mode, keeps session; Scroll Lock ends everything. ✔
6. Mode switching = **must end first** (triggers live only in normal mode). ✔
7. Feedback = **persistent sprite badge + enter/exit chimes + HUD lines**. ✔
8. Routing integration = **per-mode `VerbRouter` instances** (Approach B). ✔
9. Built-ins = implementer **pre-authors `modes/video.toml`**; mode-scoped, file
   is the source of truth (not globally matchable). ✔

## Open questions for review

- Caveat ① (literal `press 1/2/3` vs `ctrl+1/2/3` viewer focus).
- Caveat ② (JKL `l`/`k` vs spacebar toggle for play/pause).
