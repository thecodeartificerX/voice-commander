# ADR 0088 — Dictation Custom Vocabulary, Corrections, and Formatting Commands

**Status:** Accepted (amended 2026-05-20 — see D11, D12)
**Date:** 2026-05-17

## Context

ADR 0086 introduced the dictation pipeline: VAD audio accumulated in
`DictationSession`, encoded to WAV, and POSTed to a remote whisper.cpp
`/inference` endpoint. The pipeline had no mechanism to improve transcription
quality for domain-specific jargon (e.g. "Supabase", "n8n", "CTranslate2") or
to inject formatting into pasted prose by voice.

Three independently useful blocks are needed:

1. **Vocabulary** — bias the decoder toward correct spellings via the whisper.cpp
   `prompt` field (*prevention*).
2. **Corrections** — ordered literal text replacements fixing mistranscriptions the
   prompt did not prevent (*cure*).
3. **Commands** — spoken `phrase → action` mappings that inject control characters
   (`\n`, `\n\n`) and are inherently a post-processing transform.

## Decision

### D1 — Single `vocab.json` file

All three blocks live in `outputs/dictation/vocab.json` (alongside `last.wav` /
`last.txt`). The file is local-only and git-ignored. A missing or unparseable file
is treated as empty vocabulary; dictation never fails because of it.

### D2 — Data model

```json
{
  "vocab": ["n8n", "Supabase"],
  "corrections": [{"wrong": "supa base", "right": "Supabase"}],
  "commands": [
    {"phrase": "next line",  "action": "newline"},
    {"phrase": "full stop",  "insert": "."},
    {"phrase": "backslash",  "insert": "/"}
  ]
}
```

`action` ∈ `{"newline", "paragraph"}`. Unknown actions are dropped on load with a
WARNING log; they do not crash or block dictation.

**`insert` field (added D11):** commands may also carry `"insert": "<literal>"` in
place of `"action"`. When `insert` is present it takes precedence over `action`.
This allows mapping any spoken phrase to an arbitrary literal string (punctuation,
path separators, bracket characters, etc.) without extending `_ACTION_CHARS`.

### D3 — New modules

- `src/voice_commander/dictation/vocab.py` — `Vocabulary`, `Correction`, `Command`
  frozen dataclasses; `VocabStore` load/save.
- `src/voice_commander/dictation/postprocess.py` — pure functions `build_prompt`,
  `apply_corrections`, `apply_commands`; constant `_PROMPT_CHAR_CAP = 800`.

### D4 — `build_prompt` character cap

`_PROMPT_CHAR_CAP = 800` (~200 tokens at ~4 chars/token) keeps the string safely
under whisper.cpp's 224-token hard limit. Truncation drops **whole trailing words**
— a word is never split. The web meter displays `len(prompt) // 4` vs 224; overflow
is amber-coloured but **never blocks saving**.

### D5 — `post_audio` prompt wiring

`post_audio` gains `prompt: str = ""`. When non-empty it adds form fields
`prompt=<value>` and `carry_initial_prompt=true` so whisper re-applies the initial
prompt to every decode window, not only the first 30 s. See
`docs/references/ws-transcribe-server.md`.

### D6 — Pipeline integration order

In `_finalize_dictation`:
1. Encode + save WAV.
2. `vocab = _vocab_store.load()` — fresh read, hot-reload.
3. `prompt = build_prompt(vocab)`.
4. `text = post_audio(wav, endpoint, prompt=prompt)`.
5. `text = apply_corrections(text, vocab.corrections)`.
6. `text = apply_commands(text, vocab.commands)`.
7. Save + paste.

Corrections run before commands so a lightly-mistranscribed command phrase is
repaired into its canonical form before command matching.

### D7 — Re-transcribe path consistency

`/dictation/retranscribe` applies the identical four-step post-processing so
re-transcribed text is consistent with live dictation.

### D8 — Web UI

`GET /page/dictation` renders three editor sections below the existing re-transcribe
panel. A new `POST /dictation/vocab` route saves the form and returns a result
fragment (`_vocab_result.html`) — same HTMX fragment-swap pattern as the existing
re-transcribe control. No React; the dictation page is plain server-rendered HTML.

### D9 — False-trigger handling for Commands

Commands are matched on the **final concatenated transcription text** (not per-VAD
segment). The word-boundary anchor prevents mid-word hits. Residual risk (prose
containing a command phrase verbatim) is accepted for v1 and documented here;
commands are entirely user-configured, so the user chooses trigger phrases they
would not naturally speak in prose.

### D10 — No new config keys

`_PROMPT_CHAR_CAP` and `carry_initial_prompt=true` are code constants.
`vocab.json`'s location is derived from the existing dictation output directory.

## Consequences

### Positive

- Domain jargon spelled correctly without the user memorising Whisper's quirks.
- Known mistranscriptions corrected deterministically.
- Voice-controlled formatting ("next line" → newline) without new primitives.
- All three blocks hot-reload on every dictation — no daemon restart required.
- The tool catalogue remains at exactly 11 primitives (ADR 0043).

### Negative

- `vocab.json` is per-install; no multi-profile support (non-goal for v1).
- False-trigger risk for commands (see D9 above).

### Neutral

- Corrections are literal phrase matches only; no regex/wildcard support (non-goal).

### D11 — `insert` field for arbitrary literal string injection (amendment 2026-05-20)

The original `action`-only design required extending `_ACTION_CHARS` in Python code
to add any new character mapping. A live-transcript test showed users need a large
set of spoken-symbol → literal-char mappings (path separators, brackets, dashes,
punctuation) that are user-configured, not hard-coded. Adding `insert` to `Command`
(alongside `action`) allows this entirely from `vocab.json` without touching code.

`vocab.json` ships with a default commands list covering ~35 common spoken-control
phrases (see `outputs/dictation/vocab.json`). Legacy `action`-only entries are
still loaded and respected for backward compatibility.

### D12 — Whitespace-aware splicing for `insert` entries (amendment 2026-05-20)

The original `\s*\b<phrase>\b\s*` pattern consumed surrounding spaces on both sides.
This is correct for joiner chars (`-`, `/`) but wrong for sentence-ending punctuation
(`.`, `,`, `;`, `:`, `!`, `?`) which in English prose are followed by a space before
the next word. A `_SENTENCE_ENDERS` frozenset drives per-entry trim behaviour:

- **Insert is a sentence-ender single char:** pattern is `\s*\b<phrase>\b` — leading
  space consumed, trailing space preserved.  Result: `"word. Next"`, not `"word.Next"`.
- **All other inserts (joiners, spacers like ` - `, newlines):** pattern is
  `\s*\b<phrase>\b\s*` — both spaces consumed.  Result: `"a/b"`, `"a-b"`.

`action`-based entries (`newline`, `paragraph`) continue to consume both sides.

Known limitation: when Whisper auto-punctuates the final word of an utterance with a
period (e.g. transcribes `"full stop."` rather than `"full stop"`), applying `full stop
→ .` on the `\s*\bfull stop\b` pattern leaves the whisper-appended `.` in place,
yielding `"captured.."` instead of `"captured."`.  Work-around: add a correction
entry `{"wrong": "full stop.", "right": "full stop"}` before the command fires
(corrections run first per D6). This is not done automatically because not every user
wants that correction. Documented here; not fixed in code (non-trivial to fix cleanly
without altering the Correction `\b` boundary logic).

## Alternatives considered

1. **Per-VAD-segment command detection** — rejected: dictation buffers all audio as
   one WAV; splitting would multiply endpoint load.
2. **Regex corrections** — rejected: literal matches cover the primary use-case and
   are simpler to validate and explain.
3. **Per-session vocabulary (not persisted)** — rejected: users need jargon available
   on every dictation without re-entering it.
4. **`trim: both|left|none` field per command entry** — rejected in favour of the
   `_SENTENCE_ENDERS` heuristic: same outcome, zero extra vocab.json fields for the
   vast majority of cases, and the heuristic matches standard English typography rules.

## References

- [ADR 0086](0086-dictation-mode.md) — dictation pipeline this extends
- [ADR 0073](0073-remote-transcription-backend.md) — whisper.cpp wire format
- `docs/references/ws-transcribe-server.md` — prompt field / server contract reference
- `src/voice_commander/dictation/postprocess.py` — pure post-processing functions
- `src/voice_commander/dictation/vocab.py` — data model and persistence
