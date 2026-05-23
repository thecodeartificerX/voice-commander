# Named command modes

A **named mode** is a scoped, voice-switchable command catalog. While a mode
is active, utterances are matched against that mode's phrases first; anything
that doesn't match falls through to the global primitives. Normal user commands
and workflows are suppressed. Saying the mode's end phrase returns to normal
operation.

Modes exist so you can define dozens of domain-specific shortcuts (video
editing, slide authoring, a CAD tool) without polluting the global command
space or memorising arbitrary spoken shortcut names.

See ADR 0100 (`docs/decisions/0100-named-command-modes.md`) for the full
design rationale.

---

## The `modes/<name>.toml` format

Each mode is a single TOML file in the `modes/` directory (configurable via
`[modes] dir` in `config.toml`). The filename stem is the default trigger word.

```toml
# modes/video.toml

[mode]                           # all fields are optional
trigger    = "video"             # spoken word to enter the mode (default: filename stem)
end_phrase = "video end"         # spoken phrase to exit (default: "<trigger> end")
badge      = "🎬 VIDEO"         # text shown on the sprite badge (default: TRIGGER in caps)

[[command]]
phrases = ["split", "blade", "razor", "cut clip"]   # non-empty list of spoken synonyms
action  = "press ctrl+b"                             # primitive transcript (see below)

[[command]]
phrases = ["undo"]
action  = "press ctrl+z"

[[command]]
phrases = ["redo"]
action  = "press ctrl+shift+z"

[[command]]
phrases = ["back", "previous", "last cut"]
action  = "press up"
```

### `[mode]` fields (all optional)

| Field | Default | Description |
|---|---|---|
| `trigger` | filename stem (normalized) | Spoken word that enters the mode |
| `end_phrase` | `"<trigger> end"` | Spoken phrase that exits the mode |
| `badge` | `trigger.upper()` | Text shown on the persistent sprite badge |

### `[[command]]` fields

| Field | Required | Description |
|---|---|---|
| `phrases` | yes | Non-empty list of spoken synonyms/phrases for this command |
| `action` | yes | A primitive transcript (see **Action grammar** below) |

---

## Action grammar

The `action` string is a spoken transcript that is compiled through a
**base-primitives router** at load time (no daemon restart) into a cached
sequence of primitive `ToolCall`s. This means:

**Allowed actions:**
- Any single primitive: `press ctrl+b`, `type hello`, `scroll up`,
  `click`, `focus chrome`, `open notepad`, `wait 500`
- A primitive `chain`: `chain click scroll` (see limitation below)
- A repeat-count form: `press space twice`, `scroll up three times`

**Not allowed:**
- User-authored commands or workflows (they are not visible to the
  base-primitives router)
- Bare synthetic intercepts: `dictate`, bare `focus` (without argument),
  `tabs` — these produce a `ModeLoadError` at load time

**Chain limitation:** inside a `chain` the arg-taking verbs (`press`, `type`,
`open`, `focus`, `wait`, `tabs`, `chain`) are forbidden (ADR 0085 rule).
A mode action `chain` can only sequence **nullary** primitives:

```toml
action = "chain click scroll"   # OK — both are nullary
action = "chain press ctrl+b"   # REJECTED at load time — press takes an arg
```

Bad actions log a WARNING and the file is skipped; the daemon still starts.

---

## Enter / exit / must-end-first

**Entering a mode:** in a normal (non-mode) voice session, say the trigger
word on its own. The daemon intercepts the utterance before routing, enters
the mode, shows the sprite badge, and plays the recording-START chime.

```
(session active, normal mode)
You: "video"
→ enters VIDEO mode — chime, badge shown
```

**Exiting a mode:** say the end phrase (default `"<trigger> end"`):

```
(in VIDEO mode)
You: "video end"
→ exits VIDEO mode — chime, badge cleared
```

**Must-end-first:** you cannot enter a second mode while one is already
active. The trigger word of another mode is simply not recognized; it
routes as a normal utterance against the current mode's catalog (and likely
misses and chimes).

**Scroll Lock close:** closing the voice session with Scroll Lock
force-exits any active mode before publishing `session_stopped`. The mode
badge is cleared; no exit chime is played on session close.

---

## In-mode routing scope

While a mode is active, each utterance is matched in this order:

1. **Mode phrases** — longest-token-count exact match against the mode's
   phrase map (transcript normalized: lowercase, punctuation-stripped;
   phrase keys with underscores are also matched as spoken words).
2. **Primitive fallthrough** — if no phrase matches, the utterance is routed
   through the base-primitives router: single primitives, `chain`, and
   repeat-count all work.
3. **Miss** — if neither matches, or if the fallthrough resolves to a
   synthetic intercept (`dictate`, bare picker), the utterance misses: one
   chime, stay in the mode.

Normal user commands and workflows **do not route** while a mode is active.

---

## Feedback

| Event | Audio | Visual |
|---|---|---|
| Mode entered | Recording-START chime | Persistent green badge at top-centre of the sprite (RGB 120, 220, 140) |
| Mode exited (end phrase) | Recording-STOP chime | Badge cleared |
| Mode exited (session close) | — | Badge cleared |
| Miss inside mode | Miss chime | — |

The mode badge sits at the **top-centre** of the sprite window so it does not
overlap the bottom-centre DICTATING / PROCESSING / CANCELLED badges; both can
be visible simultaneously.

---

## The shipped `video.toml`

`modes/video.toml` is a ready-to-use DaVinci Resolve Edit-page shortcut pack:

| Phrase(s) | Action |
|---|---|
| split, blade, razor, cut clip | press ctrl+b |
| undo | press ctrl+z |
| redo | press ctrl+shift+z |
| one / two / three | press 1 / 2 / 3 |
| ripple, ripple delete | press delete |
| back, previous, last cut | press up |
| forward, next, next cut | press down |
| play | press space |
| pause, stop | press space |

Trigger word: `"video"` — say "video" to enter, "video end" to exit.

Source: [`docs/references/davinci-resolve-shortcuts.md`](references/davinci-resolve-shortcuts.md)

---

## Authoring a new mode

1. **Create `modes/<name>.toml`** — the filename stem becomes the default
   trigger word after normalization (lowercase, punctuation stripped).
   Choose a stem that will not collide with primitive verb names (`focus`,
   `press`, `type`, `click`, `scroll`, `open`, `wait`, `chain`, `dictate`,
   `element`, `elements`) or the chain head aliases.

2. **Define `[mode]`** (optional). Omit the table entirely to use all
   defaults.

3. **Add `[[command]]` tables** — one per logical action. Each needs a
   `phrases` list and an `action` primitive transcript.

4. **Save the file.** The hot-reload watcher picks up changes within ~500 ms
   with no daemon restart. If a file has errors, a WARNING is logged and that
   file is **skipped** — it is dropped from the active registry until fixed,
   while all other valid mode files keep working. The daemon never crashes on
   a malformed catalog.

5. **Test in a session.** Open a voice session (Scroll Lock), say the trigger
   word, check the badge, say a command, say the end phrase.

### Reserved trigger words

The following normalized words cannot be used as mode triggers (they are
claimed by primitives, chain, elements, or dictation):

`focus`, `type`, `open`, `press`, `wait`, `click`, `scroll`, `tabs`,
`chain`, `chained`, `chains`, `element`, `elements`, `dictate`

A file whose trigger normalizes to a reserved word is skipped with a WARNING.

### Duplicate triggers

If two files produce the same normalized trigger, the first file in sorted
(alphabetical) order wins. The other file is skipped with a WARNING.

---

## Hot-reload

`ModesWatcher` (watchdog, 500 ms debounce) watches the `modes/` directory for
`*.toml` creates, modifications, deletes, and renames. Any change triggers a
full registry reload. Reloading while a mode is active is safe: the active
session keeps its captured router and is unaffected until the mode exits
naturally. After a reload, the daemon publishes `modes_reloaded {"count": N}`
on the EventBus.

---

## Configuration

```toml
[modes]
enabled = true    # master switch; false disables the subsystem and watcher entirely
dir     = "modes" # directory of *.toml files, relative to repo root
```
