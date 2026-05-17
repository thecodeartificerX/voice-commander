# Dictation Custom Vocabulary — Design Spec

**Date:** 2026-05-17
**Status:** Approved (brainstorm complete; awaiting implementation plan)
**New ADR:** 0088 — Dictation custom vocabulary, corrections, and formatting commands

## Goal

Give the dictation pipeline (ADR 0086) a user-editable layer that improves transcription
quality for domain-specific jargon and proper nouns, and lets the user inject formatting
into pasted prose by voice. Three independently-useful blocks, all edited from the existing
`/page/dictation` web page:

1. **Vocabulary** — a curated word list seeded into the remote whisper.cpp `prompt` field so
   the decoder is biased toward correct spellings (*prevention*).
2. **Corrections** — ordered `wrong → right` text replacements applied to the returned
   transcription, fixing mistranscriptions the prompt did not prevent (*cure*).
3. **Commands** — spoken `phrase → action` mappings; the phrase is deleted from the text and
   a control character is injected in its place (e.g. "next line" → `\n`).

All three are read fresh on every dictation — no daemon restart is needed for edits to
take effect.

## Background — why these are not redundant

`prompt` biasing and post-processing corrections are *prevention* vs *cure*, not substitutes:

- Whisper mangles the same word differently each time depending on audio and context
  ("Supabase" → `supa base`, `soup a base`, `superbase`, …). A corrections map only fixes
  the exact garbled forms the user has already observed and entered. The `prompt` field is
  the only mechanism that reduces *unseen* mistranscriptions, because it biases the decoder
  at the source.
- Formatting commands ("next line" → newline) cannot be expressed via the `prompt` field at
  all — they are inherently a post-processing transform that injects a control character and
  removes the spoken trigger. They are a third, distinct category.

The two-layer (prompt + post-processing) approach is the standard pattern; commands extend it
with a third deterministic block.

## Non-goals

- Custom vocabulary for the **local** command pipeline (`transcriber.py`, faster-whisper
  `small.en`). Commands route through `VerbRouter` exact-match + per-command synonyms
  (`SynonymsEditor`, ADR 0080) already; `small.en` is a weak target for `initial_prompt`
  biasing. This spec covers the **remote dictation path only**.
- Free-form command actions. The Commands block exposes a fixed, small action set
  (`newline`, `paragraph`). New actions are added in code, not by the user.
- Per-VAD-segment command detection. Commands are matched on the final concatenated
  transcription text (see "False-trigger handling" below).
- Regex / wildcard corrections. Corrections are literal phrase matches (case-insensitive,
  word-boundary anchored).
- Fine-tuning or tokenizer expansion. Out of scope; the `prompt` field is the only
  model-side mechanism used.
- Versioned / multi-profile vocabularies. A single `vocab.json` per install.

## User-facing behavior

1. The user opens `GET /page/dictation`. Below the existing re-transcribe panel, three new
   editor sections appear: **Vocabulary**, **Corrections**, **Commands**.
2. **Vocabulary** — a list of words; add and remove entries. A live budget meter shows the
   estimated prompt token usage (e.g. `142 / 224 tokens`). The meter never blocks saving.
3. **Corrections** — a list of `wrong → right` rows; add and remove rows.
4. **Commands** — a list of `phrase → action` rows; `action` is a dropdown
   (`new line`, `new paragraph`); add and remove rows.
5. The user clicks **Save**. The page `POST`s to `/dictation/vocab`, the JSON file is
   written, and a result fragment is swapped in (same interaction pattern as the existing
   re-transcribe button).
6. The next dictation (and any re-transcribe) automatically uses the saved data. No daemon
   restart.
7. On a normal dictation: the user dictates, says the end word, and the pasted text has
   jargon spelled per the vocabulary bias, known mistranscriptions corrected, and any spoken
   command phrases replaced by their control characters.

## Architecture

### Data model — `vocab.json`

A single JSON file: `outputs/dictation/vocab.json`. It sits alongside the existing
dictation artefacts (`last.wav`, `last.txt`) and is, like them, local-only (the `outputs/`
tree is git-ignored) — appropriate for per-user personal jargon.

```json
{
  "vocab": ["n8n", "Supabase", "CTranslate2", "Artificer"],
  "corrections": [
    { "wrong": "supa base", "right": "Supabase" },
    { "wrong": "n-8-n",     "right": "n8n" }
  ],
  "commands": [
    { "phrase": "next line",      "action": "newline" },
    { "phrase": "new paragraph",  "action": "paragraph" }
  ]
}
```

- `vocab` — list of strings.
- `corrections` — ordered list of `{wrong, right}`; order is preserved and is the
  application order.
- `commands` — ordered list of `{phrase, action}`; `action` ∈ `{"newline", "paragraph"}`.

A missing or unparseable file is treated as an empty vocabulary (all three lists empty),
logged at WARNING level. Dictation never fails because of `vocab.json`.

### New module — `src/voice_commander/dictation/vocab.py`

Owns the data model and persistence.

```python
@dataclass(frozen=True)
class Correction:
    wrong: str
    right: str

@dataclass(frozen=True)
class Command:
    phrase: str
    action: Literal["newline", "paragraph"]

@dataclass(frozen=True)
class Vocabulary:
    vocab: tuple[str, ...] = ()
    corrections: tuple[Correction, ...] = ()
    commands: tuple[Command, ...] = ()

class VocabStore:
    """Loads/saves outputs/dictation/vocab.json. Tolerant of a missing/corrupt file."""
    def __init__(self, path: Path) -> None: ...
    def load(self) -> Vocabulary: ...      # never raises; empty Vocabulary on any failure
    def save(self, vocab: Vocabulary) -> None: ...
```

### New module — `src/voice_commander/dictation/postprocess.py`

Pure functions — no I/O, fully unit-testable.

```python
_PROMPT_CHAR_CAP = 800   # ~200 tokens at ~4 chars/token; safely under whisper's 224-token limit

def build_prompt(vocab: Vocabulary) -> str:
    """Comma-join vocab words; drop whole trailing words until under _PROMPT_CHAR_CAP."""

def apply_corrections(text: str, corrections: Sequence[Correction]) -> str:
    """Case-insensitive, word-boundary (\\b) replacement, applied in list order."""

def apply_commands(text: str, commands: Sequence[Command]) -> str:
    """Replace each phrase (with surrounding whitespace consumed) by its control char:
    newline -> '\\n', paragraph -> '\\n\\n'. Case-insensitive, word-boundary anchored."""
```

- `build_prompt` truncates by dropping **whole** trailing words — a word is never split.
  The web meter estimates tokens as `len(prompt) // 4` against the 224-token whisper limit.
- `apply_corrections` compiles `\b<wrong>\b` per entry, `re.IGNORECASE`; multi-word `wrong`
  phrases ("supa base") are supported.
- `apply_commands` matches `\s*\b<phrase>\b\s*` so the injected newline is not surrounded by
  stray spaces.

### whisper.cpp `prompt` wiring — `dictation/remote.py`

The whisper.cpp `/inference` endpoint accepts a `prompt` form field (verified against
`examples/server/server.cpp`; it maps to the decoder's `initial_prompt`) and a
`carry_initial_prompt` flag that re-applies the prompt to *every* decode window rather than
only the first 30 s — important because a dictation can span many segments.

`post_audio` gains a `prompt` parameter:

```python
def post_audio(wav_bytes: bytes, endpoint: str, prompt: str = "",
               timeout: float = _TIMEOUT_S) -> str:
    data = {"response_format": "json", "temperature": "0.0"}
    if prompt:
        data["prompt"] = prompt
        data["carry_initial_prompt"] = "true"
    ...
```

Whisper's prompt is capped at 224 tokens; `build_prompt`'s `_PROMPT_CHAR_CAP` keeps the
string well under that, so no server-side truncation occurs.

### Pipeline integration — `daemon.py::_finalize_dictation`

A `VocabStore` is constructed at daemon init (path `outputs/dictation/vocab.json`). In
`_finalize_dictation`, after the audio is encoded and before/around the remote call:

1. `vocab = self._vocab_store.load()` — fresh read every dictation (hot reload).
2. `prompt = build_prompt(vocab)`.
3. `text = remote.post_audio(wav_bytes, endpoint, prompt=prompt)`.
4. `text = apply_corrections(text, vocab.corrections)`.
5. `text = apply_commands(text, vocab.commands)`.
6. Existing path continues: `save_text(text)`, clipboard paste, `dictation.result` event.

**Order rationale:** corrections run before commands so a lightly-mistranscribed command
phrase can be repaired into its canonical form before command matching. This order is
documented and revisable.

### Re-transcribe path — `web/app.py::/dictation/retranscribe`

The re-transcribe route re-sends `last.wav`. It applies the **same** four-step
post-processing (prompt build → post_audio → corrections → commands) so the re-transcribed
text is consistent with a live dictation.

### Web UI — extend `/page/dictation`

- `GET /page/dictation` additionally loads `VocabStore.load()` and renders the three editor
  sections into `page_dictation.html`.
- New route `POST /dictation/vocab` — parses the submitted form into a `Vocabulary`, calls
  `VocabStore.save()`, and returns a result fragment (`_vocab_result.html`) reporting success
  plus the recomputed token-budget figure.
- The three sections use the same plain server-rendered + fragment-swap style as the existing
  re-transcribe control. No React; the dictation page is not part of the Builder SPA.
- The Vocabulary section renders a token-budget meter: estimated tokens vs `224`. Overflow is
  shown (e.g. meter turns amber) but **never blocks saving** — `build_prompt` truncates at
  use time.

### Configuration

No new `config.toml` key. `_PROMPT_CHAR_CAP` and `carry_initial_prompt=true` are code
constants. `vocab.json`'s location is derived from the existing dictation output directory.

## False-trigger handling (Commands)

Command phrases are matched on the **final concatenated transcription text** by literal,
word-boundary-anchored, case-insensitive match. Per-VAD-segment detection was rejected:
dictation buffers all audio and sends it as **one** WAV in **one** POST; preserving
per-segment marker positions would require splitting the audio into runs and issuing
multiple POSTs — more endpoint load, the opposite of the user's stated goal.

Residual risk: prose that literally contains a command phrase has that phrase consumed.
Mitigation: commands are entirely user-configured — the user chooses trigger phrases they
would not naturally speak in dictation. The word-boundary anchor prevents mid-word hits.
This tradeoff is documented in ADR 0088 and is acceptable for v1.

## Validation gates

Per `docs/testing-strategy.md`.

### Unit tests

- `tests/unit/test_dictation_vocab.py` — `VocabStore.load()` on a missing file, an empty
  file, a corrupt/non-JSON file, and a valid file; `save()` round-trips; unknown `action`
  values are dropped on load with a warning.
- `tests/unit/test_dictation_postprocess.py`
  - `build_prompt` — comma-join; truncation drops whole trailing words; never splits a word;
    empty vocab → empty string.
  - `apply_corrections` — case-insensitive; word-boundary (no mid-word hits); multi-word
    `wrong`; ordered application; empty list → no-op.
  - `apply_commands` — `newline`/`paragraph` control chars; surrounding whitespace consumed;
    word-boundary; empty list → no-op.
- `tests/unit/test_dictation_remote.py` — `post_audio` includes `prompt` +
  `carry_initial_prompt` form fields when `prompt` is non-empty, and omits both when empty
  (mock the HTTP layer; assert on the request).

### Integration tests

- `tests/integration/test_dictation_vocab_pipeline.py` — drive `_finalize_dictation` with a
  stubbed remote returning known text and a populated `vocab.json`; assert the pasted text
  has corrections and commands applied and that `post_audio` received the built prompt.
- Extend the re-transcribe integration coverage: `/dictation/retranscribe` applies the same
  post-processing.
- `tests/unit/test_web_routes.py` — `GET /page/dictation` renders the three sections;
  `POST /dictation/vocab` writes `vocab.json` and returns the result fragment.

### Visual E2E (mandatory — `docs/agents/visual-e2e-testing.md`)

- `scripts/dictation_vocab_e2e.py` — a subprocess harness that: starts the daemon + web
  server, drives `POST /dictation/vocab` to set a known vocabulary, runs a dictation through
  a stubbed whisper.cpp endpoint that returns text containing a known mistranscription and a
  command phrase, captures the pasted result, and asserts corrections + commands were
  applied and the prompt reached the (stub) endpoint. Evidence: captured request body +
  final pasted text logged and asserted.

### Manual validation (HITL gate)

- Open `/page/dictation`, add a vocab word, a correction, and a "next line" command; save.
- Run a real dictation containing the jargon word and "next line"; confirm the pasted text
  spells the jargon correctly and contains a real line break.
- Edit the vocabulary and run another dictation **without** restarting the daemon; confirm
  the edit took effect.
- Click **Re-transcribe**; confirm corrections + commands are applied to the result.
- Add enough vocab words to exceed the 224-token meter; confirm the meter shows overflow,
  the save still succeeds, and dictation still works (prompt truncated).

## Files touched

### New

- `src/voice_commander/dictation/vocab.py` — `Vocabulary`/`Correction`/`Command` dataclasses,
  `VocabStore`.
- `src/voice_commander/dictation/postprocess.py` — `build_prompt`, `apply_corrections`,
  `apply_commands`.
- `src/voice_commander/web/templates/_vocab_result.html` — save-result fragment.
- `tests/unit/test_dictation_vocab.py`
- `tests/unit/test_dictation_postprocess.py`
- `tests/integration/test_dictation_vocab_pipeline.py`
- `scripts/dictation_vocab_e2e.py`
- `docs/decisions/0088-dictation-custom-vocabulary.md` — new ADR.
- `docs/references/whisper-cpp-server-inference.md` — vendored whisper.cpp `/inference`
  parameter reference (`prompt`, `carry_initial_prompt`, 224-token limit), cited from
  `dictation/remote.py`.

### Modified

- `src/voice_commander/dictation/remote.py` — `post_audio` gains a `prompt` parameter; adds
  `prompt` + `carry_initial_prompt` form fields when non-empty; docstring updated.
- `src/voice_commander/daemon.py` — construct `VocabStore` at init; in `_finalize_dictation`
  load vocab, build prompt, pass to `post_audio`, apply corrections then commands.
- `src/voice_commander/web/app.py` — `GET /page/dictation` renders the three editor
  sections; new `POST /dictation/vocab` route; `/dictation/retranscribe` applies the same
  post-processing.
- `src/voice_commander/web/templates/page_dictation.html` — three editor sections + token
  meter.
- `tests/unit/test_web_routes.py`, `tests/unit/test_dictation_remote.py` — extended per the
  test plan.
- `CLAUDE.md` — refresh the dictation paragraph of **Current state**.
- `docs/agents/technical-decisions.md` — add the ADR 0088 row.
- `docs/index.md` — link the new reference doc if a references index entry is warranted.

## Open implementation questions (deferred to plan)

- Exact token-estimate divisor for the meter (`len // 4` is the starting heuristic) — the
  plan may refine it; it only affects the displayed figure, not correctness, since
  `build_prompt` enforces the char cap.
- Whether the three editor sections share one `<form>` + one Save, or each section saves
  independently. One combined form is simpler and is the default unless the plan finds a
  reason to split.
- Fragment vs full-page re-render on `POST /dictation/vocab` — match whatever the existing
  re-transcribe control does for consistency.
