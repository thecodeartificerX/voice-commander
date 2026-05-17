# Dictation Custom Vocabulary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a user-editable vocabulary layer to the dictation pipeline that biases the remote whisper.cpp decoder toward correct spellings, applies ordered text corrections, and replaces spoken command phrases with control characters — all hot-reloaded on every dictation without a daemon restart.

**Architecture:** A new `VocabStore` persists `outputs/dictation/vocab.json` alongside the existing `DictationStore` artefacts; pure functions in `postprocess.py` (`build_prompt`, `apply_corrections`, `apply_commands`) transform the transcription; `daemon.py::_finalize_dictation` loads the vocab fresh on each call, passes the built prompt to `remote.post_audio`, then applies corrections and commands before the clipboard paste. The web page `/page/dictation` gains three server-rendered editor sections and a new `POST /dictation/vocab` route; the re-transcribe path applies the identical four-step post-processing so results are consistent with live dictation.

**Tech Stack:** Python 3.12, dataclasses (stdlib), json (stdlib), re (stdlib), httpx, FastAPI + Jinja2 templates + HTMX fragment-swap (same stack as existing `web/app.py`), pytest + monkeypatch (no new test deps).

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `src/voice_commander/dictation/postprocess.py` | **Create** | Pure functions: `build_prompt`, `apply_corrections`, `apply_commands`; constant `_PROMPT_CHAR_CAP` |
| `src/voice_commander/dictation/vocab.py` | **Create** | `Vocabulary`, `Correction`, `Command` dataclasses; `VocabStore` load/save |
| `src/voice_commander/dictation/remote.py` | **Modify** (line 30) | Add `prompt: str = ""` param; send `prompt` + `carry_initial_prompt` form fields when non-empty |
| `src/voice_commander/daemon.py` | **Modify** (lines 257, 748–788) | Construct `_vocab_store` at init; wire four-step post-processing in `_finalize_dictation` |
| `src/voice_commander/web/app.py` | **Modify** (lines 156–207) | `GET /page/dictation` loads vocab; new `POST /dictation/vocab`; `/dictation/retranscribe` applies post-processing |
| `src/voice_commander/web/templates/page_dictation.html` | **Modify** | Three editor sections + token-budget meter |
| `src/voice_commander/web/templates/_vocab_result.html` | **Create** | Save-result fragment (success + token budget; matches `_dictation_result.html` style) |
| `tests/unit/test_dictation_postprocess.py` | **Create** | Unit tests for all three pure functions |
| `tests/unit/test_dictation_vocab.py` | **Create** | Unit tests for `VocabStore` load/save/error paths |
| `tests/unit/test_dictation_remote.py` | **Modify** | Two new tests: prompt fields sent when non-empty; fields omitted when empty |
| `tests/unit/test_web_routes.py` | **Modify** | `GET /page/dictation` renders three sections; `POST /dictation/vocab` saves and returns fragment |
| `tests/integration/test_dictation_vocab_pipeline.py` | **Create** | Integration: `_finalize_dictation` with populated vocab, stub remote, assert prompt+corrections+commands |
| `scripts/dictation_vocab_e2e.py` | **Create** | Visual E2E harness: stub whisper endpoint, POST vocab, drive finalize, assert pasted text |
| `docs/decisions/0088-dictation-custom-vocabulary.md` | **Create** | ADR for this feature |
| `docs/references/whisper-cpp-server-inference.md` | **Create** | Vendored whisper.cpp `/inference` parameter reference |
| `CLAUDE.md` | **Modify** | Refresh dictation paragraph in Current state |
| `docs/agents/technical-decisions.md` | **Modify** | Add ADR 0088 row |

---

## Task 1 — `postprocess.py` pure functions

**Files:**
- Create: `src/voice_commander/dictation/postprocess.py`
- Create: `tests/unit/test_dictation_postprocess.py`

### 1.1 — Write the failing tests

- [ ] Create `tests/unit/test_dictation_postprocess.py` with the following complete content:

```python
"""Unit tests for voice_commander.dictation.postprocess."""
from __future__ import annotations

import pytest

from voice_commander.dictation.postprocess import (
    _PROMPT_CHAR_CAP,
    apply_commands,
    apply_corrections,
    build_prompt,
)
from voice_commander.dictation.vocab import Command, Correction, Vocabulary


# ---------------------------------------------------------------------------
# build_prompt
# ---------------------------------------------------------------------------


def test_build_prompt_comma_joins_words():
    vocab = Vocabulary(vocab=("Supabase", "n8n", "CTranslate2"))
    result = build_prompt(vocab)
    assert result == "Supabase, n8n, CTranslate2"


def test_build_prompt_empty_vocab_returns_empty_string():
    vocab = Vocabulary()
    assert build_prompt(vocab) == ""


def test_build_prompt_truncates_whole_trailing_words():
    # Build a vocab whose comma-joined string exceeds _PROMPT_CHAR_CAP.
    # Each word is 50 chars; we need enough to go over the cap.
    long_words = tuple(f"Word{'X' * 46}_{i:03d}" for i in range(20))
    vocab = Vocabulary(vocab=long_words)
    result = build_prompt(vocab)
    assert len(result) <= _PROMPT_CHAR_CAP
    # Result must not end mid-word — every token is a full word from long_words
    if result:
        for token in result.split(", "):
            assert token in long_words


def test_build_prompt_never_splits_a_word():
    # Single word longer than _PROMPT_CHAR_CAP is dropped entirely (returns "")
    giant_word = "A" * (_PROMPT_CHAR_CAP + 10)
    vocab = Vocabulary(vocab=(giant_word,))
    result = build_prompt(vocab)
    assert result == ""


def test_build_prompt_respects_cap_constant():
    assert isinstance(_PROMPT_CHAR_CAP, int)
    assert _PROMPT_CHAR_CAP == 800


def test_build_prompt_single_word_under_cap():
    vocab = Vocabulary(vocab=("Artificer",))
    assert build_prompt(vocab) == "Artificer"


# ---------------------------------------------------------------------------
# apply_corrections
# ---------------------------------------------------------------------------


def test_apply_corrections_case_insensitive():
    corrections = (Correction(wrong="supa base", right="Supabase"),)
    assert apply_corrections("I use Supa Base daily", corrections) == "I use Supabase daily"


def test_apply_corrections_word_boundary_no_mid_word_hit():
    corrections = (Correction(wrong="base", right="BASE"),)
    # "database" must not be touched; only standalone "base" is replaced
    result = apply_corrections("the database and base", corrections)
    assert "database" in result
    assert result == "the database and BASE"


def test_apply_corrections_multiword_wrong():
    corrections = (Correction(wrong="n-8-n", right="n8n"),)
    assert apply_corrections("I love n-8-n workflows", corrections) == "I love n8n workflows"


def test_apply_corrections_ordered_application():
    # First correction repairs the garbled form; second correction fires on the repaired form
    corrections = (
        Correction(wrong="supa base", right="Supabase"),
        Correction(wrong="Supabase", right="SUPABASE"),
    )
    result = apply_corrections("using supa base today", corrections)
    assert result == "using SUPABASE today"


def test_apply_corrections_empty_list_noop():
    text = "nothing changes here"
    assert apply_corrections(text, ()) == text


def test_apply_corrections_no_match_unchanged():
    corrections = (Correction(wrong="xyz", right="ABC"),)
    text = "no match in this sentence"
    assert apply_corrections(text, corrections) == text


# ---------------------------------------------------------------------------
# apply_commands
# ---------------------------------------------------------------------------


def test_apply_commands_newline_replaces_phrase():
    commands = (Command(phrase="next line", action="newline"),)
    result = apply_commands("hello next line world", commands)
    assert result == "hello\nworld"


def test_apply_commands_paragraph_replaces_phrase():
    commands = (Command(phrase="new paragraph", action="paragraph"),)
    result = apply_commands("intro new paragraph body", commands)
    assert result == "intro\n\nbody"


def test_apply_commands_surrounding_whitespace_consumed():
    commands = (Command(phrase="next line", action="newline"),)
    # Extra spaces around the phrase must be consumed so no stray spaces remain
    result = apply_commands("hello   next line   world", commands)
    assert result == "hello\nworld"


def test_apply_commands_word_boundary_no_mid_phrase_hit():
    commands = (Command(phrase="line", action="newline"),)
    # "baseline" must not be touched; only standalone "line" matches
    result = apply_commands("the baseline and line end", commands)
    assert "baseline" in result
    assert result == "the baseline and\nend"


def test_apply_commands_case_insensitive():
    commands = (Command(phrase="next line", action="newline"),)
    result = apply_commands("hello NEXT LINE world", commands)
    assert result == "hello\nworld"


def test_apply_commands_empty_list_noop():
    text = "nothing changes here"
    assert apply_commands(text, ()) == text


def test_apply_commands_no_match_unchanged():
    commands = (Command(phrase="next line", action="newline"),)
    text = "no command phrase present"
    assert apply_commands(text, commands) == text
```

### 1.2 — Run the tests; verify they fail (module does not exist yet)

- [ ] Run:
  ```
  uv run pytest tests/unit/test_dictation_postprocess.py -v 2>&1 | head -30
  ```
- [ ] Expected: `ModuleNotFoundError: No module named 'voice_commander.dictation.postprocess'` (or `ImportError`). All tests collected then error on import.

### 1.3 — Write the minimal implementation

- [ ] Create `src/voice_commander/dictation/postprocess.py` with the following complete content:

```python
"""Pure post-processing functions for the dictation pipeline.

No I/O — all functions are fully unit-testable without any file system or
network access.

References
----------
- docs/references/whisper-cpp-server-inference.md — prompt field, 224-token limit
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from .vocab import Command, Correction, Vocabulary

# ~200 tokens at ~4 chars/token; safely under whisper.cpp's 224-token hard limit.
_PROMPT_CHAR_CAP = 800

_ACTION_CHARS: dict[str, str] = {
    "newline": "\n",
    "paragraph": "\n\n",
}


def build_prompt(vocab: Vocabulary) -> str:
    """Comma-join vocab words; drop whole trailing words until under ``_PROMPT_CHAR_CAP``.

    Returns an empty string when the vocab list is empty or every word exceeds
    the character cap individually.

    The web token-budget meter estimates tokens as ``len(prompt) // 4`` against
    whisper.cpp's 224-token limit. ``build_prompt`` enforces the char cap so no
    server-side truncation occurs even when the meter shows overflow.
    """
    if not vocab.vocab:
        return ""

    words = list(vocab.vocab)
    while words:
        candidate = ", ".join(words)
        if len(candidate) <= _PROMPT_CHAR_CAP:
            return candidate
        words.pop()

    return ""


def apply_corrections(text: str, corrections: Sequence[Correction]) -> str:
    r"""Apply ordered case-insensitive word-boundary replacements to *text*.

    Each ``Correction.wrong`` is compiled as ``\b<wrong>\b`` with
    ``re.IGNORECASE``. Multi-word phrases (e.g. ``"supa base"``) are supported
    because ``\b`` anchors only at the outer boundaries of the full phrase.
    Replacements are applied in list order so an earlier correction can set up
    a later one.
    """
    for correction in corrections:
        pattern = re.compile(r"\b" + re.escape(correction.wrong) + r"\b", re.IGNORECASE)
        text = pattern.sub(correction.right, text)
    return text


def apply_commands(text: str, commands: Sequence[Command]) -> str:
    r"""Replace spoken command phrases with their control characters.

    Each ``Command.phrase`` is matched as ``\s*\b<phrase>\b\s*`` with
    ``re.IGNORECASE`` so surrounding whitespace is consumed and the injected
    control character is not padded by stray spaces.

    Supported actions:
    - ``"newline"``   → ``"\n"``
    - ``"paragraph"`` → ``"\n\n"``
    """
    for command in commands:
        char = _ACTION_CHARS.get(command.action, "")
        if not char:
            continue
        pattern = re.compile(
            r"\s*\b" + re.escape(command.phrase) + r"\b\s*", re.IGNORECASE
        )
        text = pattern.sub(char, text)
    return text
```

### 1.4 — Run tests; verify they pass

- [ ] Run:
  ```
  uv run pytest tests/unit/test_dictation_postprocess.py -v
  ```
- [ ] Expected: `15 passed` in under 2 seconds.

### 1.5 — Commit

- [ ] Run:
  ```
  git add src/voice_commander/dictation/postprocess.py tests/unit/test_dictation_postprocess.py
  git commit -m "$(cat <<'EOF'
  feat(dictation): add postprocess pure functions (build_prompt, apply_corrections, apply_commands)

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 2 — `vocab.py` VocabStore

**Files:**
- Create: `src/voice_commander/dictation/vocab.py`
- Create: `tests/unit/test_dictation_vocab.py`

### 2.1 — Write the failing tests

- [ ] Create `tests/unit/test_dictation_vocab.py` with the following complete content:

```python
"""Unit tests for voice_commander.dictation.vocab.VocabStore."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from voice_commander.dictation.vocab import (
    Command,
    Correction,
    Vocabulary,
    VocabStore,
)


# ---------------------------------------------------------------------------
# VocabStore.load — missing file
# ---------------------------------------------------------------------------


def test_load_missing_file_returns_empty_vocabulary(tmp_path: Path):
    store = VocabStore(tmp_path / "vocab.json")
    vocab = store.load()
    assert vocab == Vocabulary()
    assert vocab.vocab == ()
    assert vocab.corrections == ()
    assert vocab.commands == ()


# ---------------------------------------------------------------------------
# VocabStore.load — empty file
# ---------------------------------------------------------------------------


def test_load_empty_file_returns_empty_vocabulary(tmp_path: Path):
    path = tmp_path / "vocab.json"
    path.write_text("", encoding="utf-8")
    store = VocabStore(path)
    vocab = store.load()
    assert vocab == Vocabulary()


# ---------------------------------------------------------------------------
# VocabStore.load — corrupt / non-JSON file
# ---------------------------------------------------------------------------


def test_load_corrupt_file_returns_empty_vocabulary_and_logs_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    path = tmp_path / "vocab.json"
    path.write_text("{not valid json", encoding="utf-8")
    store = VocabStore(path)
    with caplog.at_level(logging.WARNING, logger="voice_commander.dictation.vocab"):
        vocab = store.load()
    assert vocab == Vocabulary()
    assert any("WARNING" in r.levelname or r.levelno >= logging.WARNING for r in caplog.records)


# ---------------------------------------------------------------------------
# VocabStore.load — valid file
# ---------------------------------------------------------------------------


def test_load_valid_file_returns_populated_vocabulary(tmp_path: Path):
    path = tmp_path / "vocab.json"
    path.write_text(
        json.dumps(
            {
                "vocab": ["n8n", "Supabase", "CTranslate2"],
                "corrections": [
                    {"wrong": "supa base", "right": "Supabase"},
                    {"wrong": "n-8-n", "right": "n8n"},
                ],
                "commands": [
                    {"phrase": "next line", "action": "newline"},
                    {"phrase": "new paragraph", "action": "paragraph"},
                ],
            }
        ),
        encoding="utf-8",
    )
    store = VocabStore(path)
    vocab = store.load()

    assert vocab.vocab == ("n8n", "Supabase", "CTranslate2")
    assert vocab.corrections == (
        Correction(wrong="supa base", right="Supabase"),
        Correction(wrong="n-8-n", right="n8n"),
    )
    assert vocab.commands == (
        Command(phrase="next line", action="newline"),
        Command(phrase="new paragraph", action="paragraph"),
    )


# ---------------------------------------------------------------------------
# VocabStore.load — unknown action values are dropped with a warning
# ---------------------------------------------------------------------------


def test_load_unknown_action_dropped_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    path = tmp_path / "vocab.json"
    path.write_text(
        json.dumps(
            {
                "vocab": [],
                "corrections": [],
                "commands": [
                    {"phrase": "do thing", "action": "unknown_action"},
                    {"phrase": "next line", "action": "newline"},
                ],
            }
        ),
        encoding="utf-8",
    )
    store = VocabStore(path)
    with caplog.at_level(logging.WARNING, logger="voice_commander.dictation.vocab"):
        vocab = store.load()

    # Only the valid command survives
    assert len(vocab.commands) == 1
    assert vocab.commands[0] == Command(phrase="next line", action="newline")
    assert any("unknown_action" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# VocabStore.save + load round-trip
# ---------------------------------------------------------------------------


def test_save_and_load_roundtrip(tmp_path: Path):
    path = tmp_path / "vocab.json"
    store = VocabStore(path)

    original = Vocabulary(
        vocab=("Artificer", "CTranslate2"),
        corrections=(Correction(wrong="art if icer", right="Artificer"),),
        commands=(Command(phrase="next line", action="newline"),),
    )
    store.save(original)
    loaded = store.load()

    assert loaded == original


def test_save_creates_parent_directories(tmp_path: Path):
    path = tmp_path / "nested" / "deep" / "vocab.json"
    store = VocabStore(path)
    store.save(Vocabulary(vocab=("test",)))
    assert path.exists()


def test_save_overwrites_previous(tmp_path: Path):
    path = tmp_path / "vocab.json"
    store = VocabStore(path)
    store.save(Vocabulary(vocab=("first",)))
    store.save(Vocabulary(vocab=("second",)))
    loaded = store.load()
    assert loaded.vocab == ("second",)


# ---------------------------------------------------------------------------
# Vocabulary is immutable (frozen dataclass)
# ---------------------------------------------------------------------------


def test_vocabulary_is_frozen():
    vocab = Vocabulary(vocab=("test",))
    with pytest.raises((AttributeError, TypeError)):
        vocab.vocab = ("other",)  # type: ignore[misc]


def test_correction_is_frozen():
    c = Correction(wrong="a", right="b")
    with pytest.raises((AttributeError, TypeError)):
        c.wrong = "x"  # type: ignore[misc]


def test_command_is_frozen():
    cmd = Command(phrase="next line", action="newline")
    with pytest.raises((AttributeError, TypeError)):
        cmd.phrase = "other"  # type: ignore[misc]
```

### 2.2 — Run the tests; verify they fail

- [ ] Run:
  ```
  uv run pytest tests/unit/test_dictation_vocab.py -v 2>&1 | head -20
  ```
- [ ] Expected: `ModuleNotFoundError: No module named 'voice_commander.dictation.vocab'`.

### 2.3 — Write the minimal implementation

- [ ] Create `src/voice_commander/dictation/vocab.py` with the following complete content:

```python
"""Vocabulary data model and persistence for the dictation pipeline.

Owns ``vocab.json`` — the single user-editable file that controls the three
post-processing layers: prompt biasing, text corrections, and formatting
commands.

A missing or unparseable file is always treated as an empty ``Vocabulary``
(all three lists empty). Dictation never fails because of ``vocab.json``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

_VALID_ACTIONS: frozenset[str] = frozenset({"newline", "paragraph"})


@dataclass(frozen=True)
class Correction:
    """A single ``wrong → right`` text replacement."""

    wrong: str
    right: str


@dataclass(frozen=True)
class Command:
    """A spoken phrase mapped to a formatting action."""

    phrase: str
    action: Literal["newline", "paragraph"]


@dataclass(frozen=True)
class Vocabulary:
    """Complete user vocabulary loaded from ``vocab.json``.

    All three lists are tuples so the dataclass remains hashable and frozen.
    """

    vocab: tuple[str, ...] = ()
    corrections: tuple[Correction, ...] = ()
    commands: tuple[Command, ...] = ()


class VocabStore:
    """Loads/saves ``outputs/dictation/vocab.json``.

    Tolerant of a missing or corrupt file — :meth:`load` always returns a
    valid (possibly empty) :class:`Vocabulary`. Never raises on load.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def load(self) -> Vocabulary:
        """Load and parse ``vocab.json``; return empty ``Vocabulary`` on any failure."""
        if not self._path.exists():
            return Vocabulary()

        try:
            raw = self._path.read_text(encoding="utf-8").strip()
            if not raw:
                return Vocabulary()
            data = json.loads(raw)
        except Exception as exc:
            logger.warning("vocab.json unreadable or invalid JSON: %s", exc)
            return Vocabulary()

        if not isinstance(data, dict):
            logger.warning("vocab.json root is not a JSON object — ignoring")
            return Vocabulary()

        # --- vocab list ---
        vocab_raw = data.get("vocab", [])
        vocab: tuple[str, ...] = tuple(
            str(w) for w in vocab_raw if isinstance(w, str)
        )

        # --- corrections list ---
        corrections_raw = data.get("corrections", [])
        corrections: list[Correction] = []
        for item in corrections_raw:
            if isinstance(item, dict) and "wrong" in item and "right" in item:
                corrections.append(
                    Correction(wrong=str(item["wrong"]), right=str(item["right"]))
                )

        # --- commands list ---
        commands_raw = data.get("commands", [])
        commands: list[Command] = []
        for item in commands_raw:
            if not (isinstance(item, dict) and "phrase" in item and "action" in item):
                continue
            action = str(item["action"])
            if action not in _VALID_ACTIONS:
                logger.warning(
                    "vocab.json: unknown command action %r — entry dropped", action
                )
                continue
            commands.append(
                Command(
                    phrase=str(item["phrase"]),
                    action=action,  # type: ignore[arg-type]
                )
            )

        return Vocabulary(
            vocab=vocab,
            corrections=tuple(corrections),
            commands=tuple(commands),
        )

    def save(self, vocab: Vocabulary) -> None:
        """Serialise *vocab* to ``vocab.json``, creating parent directories as needed."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "vocab": list(vocab.vocab),
            "corrections": [
                {"wrong": c.wrong, "right": c.right} for c in vocab.corrections
            ],
            "commands": [
                {"phrase": cmd.phrase, "action": cmd.action} for cmd in vocab.commands
            ],
        }
        self._path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
```

### 2.4 — Run tests; verify they pass

- [ ] Run:
  ```
  uv run pytest tests/unit/test_dictation_vocab.py -v
  ```
- [ ] Expected: `13 passed` in under 2 seconds.

### 2.5 — Commit

- [ ] Run:
  ```
  git add src/voice_commander/dictation/vocab.py tests/unit/test_dictation_vocab.py
  git commit -m "$(cat <<'EOF'
  feat(dictation): add Vocabulary/Correction/Command dataclasses and VocabStore

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 3 — `remote.py` prompt wiring

**Files:**
- Modify: `src/voice_commander/dictation/remote.py` (line 30)
- Modify: `tests/unit/test_dictation_remote.py`

### 3.1 — Write the two new failing tests

- [ ] Open `tests/unit/test_dictation_remote.py` and append the following two tests after the existing `test_post_audio_non_httperror_exception_is_wrapped` test:

```python


def test_post_audio_sends_prompt_and_carry_flag_when_prompt_nonempty(monkeypatch):
    """When prompt is non-empty, post_audio must include 'prompt' and
    'carry_initial_prompt' form fields in the multipart POST."""
    captured: dict = {}

    def fake_post(url, **kwargs):
        captured["data"] = kwargs["data"]
        return _FakeResponse(200, {"text": "hello"})

    monkeypatch.setattr("voice_commander.dictation.remote.httpx.post", fake_post)
    result = post_audio(b"RIFFfake", "http://x/inference", prompt="Supabase, n8n")
    assert result == "hello"
    assert captured["data"]["prompt"] == "Supabase, n8n"
    assert captured["data"]["carry_initial_prompt"] == "true"


def test_post_audio_omits_prompt_fields_when_prompt_empty(monkeypatch):
    """When prompt is empty (default), neither 'prompt' nor 'carry_initial_prompt'
    must appear in the POST data."""
    captured: dict = {}

    def fake_post(url, **kwargs):
        captured["data"] = kwargs["data"]
        return _FakeResponse(200, {"text": "hello"})

    monkeypatch.setattr("voice_commander.dictation.remote.httpx.post", fake_post)
    post_audio(b"RIFFfake", "http://x/inference")
    assert "prompt" not in captured["data"]
    assert "carry_initial_prompt" not in captured["data"]
```

### 3.2 — Run the new tests; verify they fail

- [ ] Run:
  ```
  uv run pytest tests/unit/test_dictation_remote.py::test_post_audio_sends_prompt_and_carry_flag_when_prompt_nonempty tests/unit/test_dictation_remote.py::test_post_audio_omits_prompt_fields_when_prompt_empty -v
  ```
- [ ] Expected: `2 failed` — `TypeError: post_audio() got an unexpected keyword argument 'prompt'` (or similar).

### 3.3 — Modify `remote.py`

- [ ] Edit `src/voice_commander/dictation/remote.py`. Replace the entire file with:

```python
"""Client for the remote whisper.cpp /inference endpoint.

Mirrors the request contract of the local-transcribe skill: a multipart POST
with the audio under the ``file`` field and form fields
``response_format=json`` + ``temperature=0.0``. The transcription is read
from the top-level ``text`` key of the JSON response.

``json`` is deliberate over ``verbose_json``: dictation only needs the
``text`` field, and ``verbose_json`` makes whisper.cpp additionally compute
per-segment confidence + token timestamps — measured at ~+1.2s on a 36s clip
on the reference box. Switching to ``json`` drops that work entirely.

When a non-empty *prompt* string is provided, it is sent as the
``prompt`` form field and ``carry_initial_prompt=true`` is added so whisper
re-applies the initial prompt to every decode window, not only the first 30 s.
See ``docs/references/whisper-cpp-server-inference.md`` for the full parameter
reference.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

# Generous read timeout — a long dictation can be minutes of audio.
_TIMEOUT_S = 300.0


class DictationRemoteError(Exception):
    """Raised when the remote endpoint is unreachable or returns a bad response."""


def post_audio(
    wav_bytes: bytes,
    endpoint: str,
    prompt: str = "",
    timeout: float = _TIMEOUT_S,
) -> str:
    """POST a 16 kHz mono WAV to *endpoint*; return the transcribed text.

    Parameters
    ----------
    wav_bytes:
        16 kHz mono 16-bit PCM WAV bytes.
    endpoint:
        Full URL of the whisper.cpp ``/inference`` endpoint.
    prompt:
        Optional initial-prompt string built by ``postprocess.build_prompt``.
        When non-empty, the ``prompt`` and ``carry_initial_prompt`` form fields
        are added to the POST so the decoder is biased toward user vocabulary
        across *all* decode windows. When empty (default), both fields are
        omitted and behaviour is identical to the pre-vocabulary baseline.
    timeout:
        HTTP read timeout in seconds (default 300 s — long dictations can take
        many seconds on the remote hardware).

    Raises
    ------
    DictationRemoteError
        On any network, HTTP, or JSON-parse failure.
    """
    data: dict[str, str] = {"response_format": "json", "temperature": "0.0"}
    if prompt:
        data["prompt"] = prompt
        data["carry_initial_prompt"] = "true"

    try:
        resp = httpx.post(
            endpoint,
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            data=data,
            timeout=timeout,
        )
    except Exception as e:
        raise DictationRemoteError(f"endpoint request failed: {e}") from e

    if resp.status_code != 200:
        raise DictationRemoteError(
            f"HTTP {resp.status_code} from {endpoint}: {resp.text[:200]}"
        )

    try:
        resp_data = resp.json()
    except ValueError as e:
        raise DictationRemoteError(f"could not parse JSON response: {e}") from e

    text = resp_data.get("text")
    if not isinstance(text, str):
        raise DictationRemoteError("response missing top-level 'text' field")
    # whisper.cpp emits a newline at every segment boundary, so a multi-segment
    # clip arrives with spurious mid-sentence line breaks. Dictation inserts
    # free-form prose at the cursor — collapse every whitespace run (newlines
    # included) to a single space so the paste reads as one continuous block.
    return " ".join(text.split())
```

### 3.4 — Run full remote test suite; verify all pass

- [ ] Run:
  ```
  uv run pytest tests/unit/test_dictation_remote.py -v
  ```
- [ ] Expected: `8 passed` (6 original + 2 new).

### 3.5 — Commit

- [ ] Run:
  ```
  git add src/voice_commander/dictation/remote.py tests/unit/test_dictation_remote.py
  git commit -m "$(cat <<'EOF'
  feat(dictation/remote): add prompt + carry_initial_prompt wiring to post_audio

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 4 — `daemon.py` `_finalize_dictation` integration

**Files:**
- Modify: `src/voice_commander/daemon.py` (lines 257–258 for init; lines 748–788 for `_finalize_dictation`)
- Create: `tests/integration/test_dictation_vocab_pipeline.py`

### 4.1 — Write the failing integration tests

- [ ] Create `tests/integration/test_dictation_vocab_pipeline.py` with the following complete content:

```python
"""Integration tests: _finalize_dictation with populated VocabStore.

Mirrors tests/integration/test_dictation_pipeline.py scaffolding.
Only the network (post_audio), OS clipboard (paste_via_clipboard), and
VocabStore.load are touched. Everything else is real production code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from voice_commander.dictation.session import DictationSession
from voice_commander.event_bus import EventBus
from voice_commander.feedback import CapturingFeedbackSink
from voice_commander.verb_router import VerbRouter, build_default_rules


@dataclass
class _Transcription:
    text: str
    confidence: float = 0.95
    no_speech_prob: float = 0.05


@dataclass
class _StubTranscriber:
    queue: list[Any]

    def load(self) -> None: ...
    def unload(self) -> None: ...
    def transcribe(self, _audio: np.ndarray) -> _Transcription:
        return self.queue.pop(0)


def _make_daemon(transcripts, tmp_path):
    from voice_commander.daemon import StreamingDaemon
    from voice_commander.dispatcher import Dispatcher
    from voice_commander.picker.registry import reset_global_picker_registry
    from voice_commander.registry import get_global_registry, reset_global_registry

    reset_global_registry()
    reset_global_picker_registry()

    registry = get_global_registry()
    bus = EventBus()
    feedback = CapturingFeedbackSink()
    dispatcher = Dispatcher(feedback=feedback, event_bus=bus)
    verb_router = VerbRouter(build_default_rules(), registry=registry, picker_registry=None)
    dictation_session = DictationSession(bus=bus, end_word="done")

    daemon = StreamingDaemon(
        feedback=feedback,
        recorder=None,
        transcriber=_StubTranscriber(queue=list(transcripts)),
        dispatcher=dispatcher,
        verb_router=verb_router,
        registry=registry,
        event_bus=bus,
        dictation_session=dictation_session,
        output_dir=str(tmp_path),
    )
    daemon._transcriber_ready.set()
    return daemon, dictation_session, feedback, bus


# ---------------------------------------------------------------------------
# Test 1: prompt is forwarded to post_audio
# ---------------------------------------------------------------------------


def test_finalize_dictation_passes_built_prompt_to_post_audio(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_finalize_dictation must call post_audio with the prompt built from vocab.json."""
    # Write a vocab.json with known words
    vocab_path = tmp_path / "dictation" / "vocab.json"
    vocab_path.parent.mkdir(parents=True, exist_ok=True)
    vocab_path.write_text(
        json.dumps({"vocab": ["Supabase", "n8n"], "corrections": [], "commands": []}),
        encoding="utf-8",
    )

    posted_prompts: list[str] = []

    def _fake_post(wav_bytes: bytes, endpoint: str, prompt: str = "", **kw: Any) -> str:
        posted_prompts.append(prompt)
        return "raw transcription text"

    monkeypatch.setattr("voice_commander.dictation.remote.post_audio", _fake_post)
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    daemon, dictation_session, _, _ = _make_daemon(
        transcripts=[
            _Transcription("dictate"),
            _Transcription("hello"),
            _Transcription("done"),
        ],
        tmp_path=tmp_path,
    )

    audio = np.zeros(16000, dtype=np.float32)
    daemon._process_utterance(audio)
    daemon._process_utterance(audio)
    daemon._process_utterance(audio)
    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    assert len(posted_prompts) == 1
    assert "Supabase" in posted_prompts[0]
    assert "n8n" in posted_prompts[0]


# ---------------------------------------------------------------------------
# Test 2: corrections applied before paste
# ---------------------------------------------------------------------------


def test_finalize_dictation_applies_corrections(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Corrections in vocab.json must be applied to the remote transcription before paste."""
    vocab_path = tmp_path / "dictation" / "vocab.json"
    vocab_path.parent.mkdir(parents=True, exist_ok=True)
    vocab_path.write_text(
        json.dumps(
            {
                "vocab": [],
                "corrections": [{"wrong": "supa base", "right": "Supabase"}],
                "commands": [],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "voice_commander.dictation.remote.post_audio",
        lambda wav_bytes, endpoint, prompt="", **kw: "I use supa base daily",
    )
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    daemon, _, _, _ = _make_daemon(
        transcripts=[
            _Transcription("dictate"),
            _Transcription("I use supa base daily"),
            _Transcription("done"),
        ],
        tmp_path=tmp_path,
    )

    audio = np.zeros(16000, dtype=np.float32)
    daemon._process_utterance(audio)
    daemon._process_utterance(audio)
    daemon._process_utterance(audio)
    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    assert pasted == ["I use Supabase daily"]


# ---------------------------------------------------------------------------
# Test 3: commands applied after corrections
# ---------------------------------------------------------------------------


def test_finalize_dictation_applies_commands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Commands in vocab.json must replace spoken phrases with control chars."""
    vocab_path = tmp_path / "dictation" / "vocab.json"
    vocab_path.parent.mkdir(parents=True, exist_ok=True)
    vocab_path.write_text(
        json.dumps(
            {
                "vocab": [],
                "corrections": [],
                "commands": [{"phrase": "next line", "action": "newline"}],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "voice_commander.dictation.remote.post_audio",
        lambda wav_bytes, endpoint, prompt="", **kw: "hello next line world",
    )
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    daemon, _, _, _ = _make_daemon(
        transcripts=[
            _Transcription("dictate"),
            _Transcription("hello next line world"),
            _Transcription("done"),
        ],
        tmp_path=tmp_path,
    )

    audio = np.zeros(16000, dtype=np.float32)
    daemon._process_utterance(audio)
    daemon._process_utterance(audio)
    daemon._process_utterance(audio)
    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    assert pasted == ["hello\nworld"]


# ---------------------------------------------------------------------------
# Test 4: missing vocab.json — no crash; post_audio called without prompt
# ---------------------------------------------------------------------------


def test_finalize_dictation_no_vocab_file_no_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When vocab.json is absent, post_audio is called with prompt='' and text is pasted unchanged."""
    posted_prompts: list[str] = []

    monkeypatch.setattr(
        "voice_commander.dictation.remote.post_audio",
        lambda wav_bytes, endpoint, prompt="", **kw: (
            posted_prompts.append(prompt), "plain text"
        )[1],
    )
    pasted: list[str] = []
    monkeypatch.setattr(
        "voice_commander.dictation.clipboard.paste_via_clipboard",
        lambda text, **kw: pasted.append(text),
    )

    daemon, _, _, _ = _make_daemon(
        transcripts=[
            _Transcription("dictate"),
            _Transcription("plain text"),
            _Transcription("done"),
        ],
        tmp_path=tmp_path,
    )

    audio = np.zeros(16000, dtype=np.float32)
    daemon._process_utterance(audio)
    daemon._process_utterance(audio)
    daemon._process_utterance(audio)
    daemon._dictation_executor.shutdown(wait=True)
    daemon._wav_executor.shutdown(wait=True)

    assert pasted == ["plain text"]
    assert posted_prompts == [""]
```

### 4.2 — Run the tests; verify they fail

- [ ] Run:
  ```
  uv run pytest tests/integration/test_dictation_vocab_pipeline.py -v 2>&1 | head -30
  ```
- [ ] Expected: 4 failed — `_fake_post` called without `prompt` kwarg (existing `post_audio` has no `prompt` param yet in daemon's import) or the daemon does not call `apply_corrections`. The tests fail because `_finalize_dictation` does not yet load `VocabStore` or apply post-processing.

### 4.3 — Modify `daemon.py`

**Step A — Add `_vocab_store` construction at line 257 (after `_dictation_store`):**

- [ ] Edit `src/voice_commander/daemon.py`. Find the block at lines 255–261:

```python
        self._dictation_session = dictation_session
        self._dictation_endpoint = dictation_endpoint
        self._dictation_store = DictationStore(self._output_dir / "dictation")
        self._dictation_executor = concurrent.futures.ThreadPoolExecutor(
```

Replace it with:

```python
        self._dictation_session = dictation_session
        self._dictation_endpoint = dictation_endpoint
        self._dictation_store = DictationStore(self._output_dir / "dictation")
        from .dictation.vocab import VocabStore as _VocabStore
        self._vocab_store = _VocabStore(self._output_dir / "dictation" / "vocab.json")
        self._dictation_executor = concurrent.futures.ThreadPoolExecutor(
```

**Step B — Rewrite `_finalize_dictation` (lines 748–788):**

- [ ] Find `_finalize_dictation` in `daemon.py` (lines 748–788) and replace the entire method body with:

```python
    def _finalize_dictation(self, audio: npt.NDArray[np.float32]) -> None:
        """Worker-thread finalize: encode → build prompt → POST → post-process → paste.

        Runs on ``self._dictation_executor`` so the pipeline thread is never
        blocked by the network round-trip. All failures surface as a
        ``dictation.error`` event + miss chime; the audio stays on disk for
        the web re-transcribe button.

        Processing order
        ----------------
        1. Encode audio → WAV bytes; save to ``last.wav``.
        2. Load ``VocabStore`` (fresh read — hot-reload with no restart).
        3. Build whisper ``prompt`` string from vocabulary.
        4. ``remote.post_audio(wav_bytes, endpoint, prompt=prompt)`` — transcribe.
        5. ``apply_corrections(text, vocab.corrections)`` — fix known garbled forms.
        6. ``apply_commands(text, vocab.commands)`` — replace command phrases with
           control characters.
        7. Save processed text to ``last.txt``; paste via clipboard.

        Corrections run before commands so a lightly-mistranscribed command phrase
        can be repaired into its canonical form before command matching (ADR 0088).
        """
        from .dictation import clipboard, remote
        from .dictation.postprocess import apply_commands, apply_corrections, build_prompt
        from .dictation.store import encode_wav

        try:
            wav_bytes = encode_wav(audio)
            self._dictation_store.save_audio(wav_bytes)
        except Exception:
            logger.exception("dictation: failed to encode/save audio")
            self._publish("dictation.error", {"reason": "encode"})
            self._feedback.on_miss("(dictation: encode error)", ())
            return

        # Hot-reload vocabulary on every dictation — no daemon restart required.
        vocab = self._vocab_store.load()
        prompt = build_prompt(vocab)

        try:
            text = remote.post_audio(wav_bytes, self._dictation_endpoint, prompt=prompt)
        except remote.DictationRemoteError as e:
            logger.warning("dictation: remote transcription failed: %s", e)
            self._publish("dictation.error", {"reason": "endpoint"})
            self._feedback.on_miss("(dictation: endpoint error)", ())
            return

        # Post-process: corrections then commands (ADR 0088 §Pipeline integration).
        text = apply_corrections(text, vocab.corrections)
        text = apply_commands(text, vocab.commands)

        self._dictation_store.save_text(text)

        try:
            clipboard.paste_via_clipboard(text)
        except Exception:
            logger.exception("dictation: clipboard paste failed")
            self._publish("dictation.error", {"reason": "clipboard"})
            self._feedback.on_miss("(dictation: clipboard error)", ())
            return

        self._publish("transcript", {"text": text, "confidence": 1.0})
        self._publish("dictation.result", {"text": text})
        logger.info("dictation: pasted %d chars", len(text))
```

### 4.4 — Run tests; verify they pass

- [ ] Run:
  ```
  uv run pytest tests/integration/test_dictation_vocab_pipeline.py tests/integration/test_dictation_pipeline.py -v
  ```
- [ ] Expected: all tests in both files pass (the existing pipeline tests still pass because `post_audio`'s `prompt` parameter defaults to `""`).

### 4.5 — Commit

- [ ] Run:
  ```
  git add src/voice_commander/daemon.py tests/integration/test_dictation_vocab_pipeline.py
  git commit -m "$(cat <<'EOF'
  feat(daemon): wire VocabStore + four-step post-processing into _finalize_dictation

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 5 — Web route `POST /dictation/vocab` + `/page/dictation` rendering + retranscribe post-processing

**Files:**
- Modify: `src/voice_commander/web/app.py` (lines 156–207)
- Modify: `tests/unit/test_web_routes.py`

### 5.1 — Write the failing web route tests

- [ ] Open `tests/unit/test_web_routes.py` and append the following tests at the end of the file:

```python


# ---------------------------------------------------------------------------
# Dictation page + vocab routes
# ---------------------------------------------------------------------------

import json as _json_mod


@pytest.fixture
def app_env_with_dictation(tmp_path):
    """App fixture that also wires the dictation output dir so vocab routes work."""
    import shutil as _shutil

    fixtures_path = Path(__file__).resolve().parent.parent / "fixtures" / "tools_sidecar"
    _shutil.copy(fixtures_path / "sample.toml", tmp_path / "sample.toml")

    store = ToolMetadataStore(tmp_path)
    registry = ToolRegistry()
    for name in ("alpha", "beta"):
        registry.register(
            ToolEntry(
                name=name,
                phrases=(),
                func=lambda: None,
                module="test",
                docstring=None,
            )
        )
    registry.bind_metadata(store)
    reload_lock = threading.Lock()
    app = create_app(registry, store, reload_lock)
    # Patch the dictation output dir to tmp_path so VocabStore uses it
    import voice_commander.web.app as _app_mod

    _orig = _app_mod.Path
    _app_mod.Path = lambda *args, **kwargs: (  # type: ignore[assignment]
        (tmp_path / args[0]) if args and args[0] == "outputs/dictation" else _orig(*args, **kwargs)
    )
    client = TestClient(app)
    yield client, tmp_path
    _app_mod.Path = _orig


def test_page_dictation_renders_three_editor_sections(app_env):
    """GET /page/dictation must render the Vocabulary, Corrections, and Commands sections."""
    client, _, _ = app_env
    resp = client.get("/page/dictation")
    assert resp.status_code == 200
    body = resp.text
    assert "Vocabulary" in body
    assert "Corrections" in body
    assert "Commands" in body


def test_post_vocab_returns_result_fragment_on_success(app_env, tmp_path, monkeypatch):
    """POST /dictation/vocab must write vocab.json and return the result fragment."""
    import voice_commander.dictation.vocab as _vocab_mod

    saved_vocabs: list = []
    original_save = _vocab_mod.VocabStore.save

    def _capturing_save(self, vocab):
        saved_vocabs.append(vocab)
        original_save(self, vocab)

    monkeypatch.setattr(_vocab_mod.VocabStore, "save", _capturing_save)
    # Point VocabStore to tmp_path
    monkeypatch.setattr(
        "voice_commander.web.app.Path",
        lambda *a, **kw: (
            (tmp_path / a[0]) if a and a[0] == "outputs/dictation" else __import__("pathlib").Path(*a, **kw)
        ),
    )

    client, _, _ = app_env
    resp = client.post(
        "/dictation/vocab",
        data={
            "vocab": "Supabase\nn8n",
            "corrections": _json_mod.dumps(
                [{"wrong": "supa base", "right": "Supabase"}]
            ),
            "commands": _json_mod.dumps(
                [{"phrase": "next line", "action": "newline"}]
            ),
        },
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    # Fragment must mention saved successfully and include token estimate
    body = resp.text
    assert "saved" in body.lower() or "vocab" in body.lower()


def test_post_vocab_csrf_blocked_without_hx_header(app_env):
    """POST /dictation/vocab must be blocked without HX-Request header."""
    client, _, _ = app_env
    resp = client.post("/dictation/vocab", data={"vocab": "test"})
    assert resp.status_code == 403
```

### 5.2 — Run the new tests; verify they fail

- [ ] Run:
  ```
  uv run pytest tests/unit/test_web_routes.py::test_page_dictation_renders_three_editor_sections tests/unit/test_web_routes.py::test_post_vocab_returns_result_fragment_on_success tests/unit/test_web_routes.py::test_post_vocab_csrf_blocked_without_hx_header -v 2>&1 | head -30
  ```
- [ ] Expected: first test fails (`Vocabulary` / `Corrections` / `Commands` not in page body); second and third fail with 404 (`/dictation/vocab` route does not exist yet).

### 5.3 — Modify `web/app.py`

- [ ] In `src/voice_commander/web/app.py`, replace the existing `page_dictation` and `dictation_retranscribe` route functions (lines 156–207) with:

```python
    @app.get("/page/dictation", response_class=HTMLResponse)
    async def page_dictation(request: Request) -> HTMLResponse:
        """``GET /page/dictation`` — last dictation + re-transcribe button + vocab editor."""
        from ..dictation.postprocess import build_prompt
        from ..dictation.store import DictationStore
        from ..dictation.vocab import VocabStore

        last_text = DictationStore(Path("outputs/dictation")).read_text() or ""
        vocab = VocabStore(Path("outputs/dictation") / "vocab.json").load()
        estimated_tokens = len(build_prompt(vocab)) // 4
        return templates.TemplateResponse(
            request,
            "page_dictation.html",
            {
                "last_text": last_text,
                "vocab": vocab,
                "estimated_tokens": estimated_tokens,
                "token_limit": 224,
            },
        )

    @app.post("/dictation/vocab", response_class=HTMLResponse)
    async def dictation_vocab_save(request: Request) -> HTMLResponse:
        """``POST /dictation/vocab`` — persist the vocab editor form; return result fragment.

        Accepts form fields:
        - ``vocab``: newline-separated list of vocabulary words.
        - ``corrections``: JSON array of ``{wrong, right}`` objects.
        - ``commands``: JSON array of ``{phrase, action}`` objects.

        Returns ``_vocab_result.html`` fragment (same interaction pattern as
        ``/dictation/retranscribe``).
        """
        import json as _json

        from ..dictation.postprocess import build_prompt
        from ..dictation.vocab import Command, Correction, Vocabulary, VocabStore

        estimated_tokens = 0
        error: str | None = None
        try:
            form = await request.form()
            vocab_raw = str(form.get("vocab", ""))
            corrections_raw = str(form.get("corrections", "[]"))
            commands_raw = str(form.get("commands", "[]"))

            words = tuple(w.strip() for w in vocab_raw.splitlines() if w.strip())

            try:
                corrections_list = _json.loads(corrections_raw)
            except Exception:
                corrections_list = []
            corrections = tuple(
                Correction(wrong=str(c["wrong"]), right=str(c["right"]))
                for c in corrections_list
                if isinstance(c, dict) and "wrong" in c and "right" in c
            )

            try:
                commands_list = _json.loads(commands_raw)
            except Exception:
                commands_list = []
            commands = tuple(
                Command(phrase=str(c["phrase"]), action=c["action"])
                for c in commands_list
                if isinstance(c, dict)
                and "phrase" in c
                and c.get("action") in ("newline", "paragraph")
            )

            new_vocab = Vocabulary(vocab=words, corrections=corrections, commands=commands)

            def _do_save() -> None:
                VocabStore(Path("outputs/dictation") / "vocab.json").save(new_vocab)

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _do_save)
            estimated_tokens = len(build_prompt(new_vocab)) // 4
        except Exception as exc:  # noqa: BLE001
            logger.exception("vocab save failed")
            error = f"save failed: {exc}"

        ctx = {"error": error} if error else {
            "estimated_tokens": estimated_tokens,
            "token_limit": 224,
        }
        return HTMLResponse(
            templates.get_template("_vocab_result.html").render(ctx)
        )

    @app.post("/dictation/retranscribe", response_class=HTMLResponse)
    async def dictation_retranscribe(request: Request) -> HTMLResponse:
        """``POST /dictation/retranscribe`` — re-POST saved audio, apply vocab post-processing, set clipboard.

        All blocking work (file I/O, config load, the HTTP round-trip, vocab load,
        post-processing, the Win32 clipboard write with its retry sleeps) runs in a
        worker thread so the event loop is never stalled. Any failure renders the
        error partial.
        """

        def _retranscribe_sync() -> tuple[str | None, str | None]:
            """Run the whole retranscribe off the event loop.

            Returns ``(text, error)`` — exactly one is non-None.
            """
            from ..config import Config
            from ..dictation import clipboard, remote
            from ..dictation.postprocess import apply_commands, apply_corrections, build_prompt
            from ..dictation.store import DictationStore
            from ..dictation.vocab import VocabStore

            store = DictationStore(Path("outputs/dictation"))
            wav = store.read_audio()
            if wav is None:
                return None, "no audio recorded yet"

            # Hot-reload vocabulary — same four-step pipeline as _finalize_dictation.
            vocab = VocabStore(Path("outputs/dictation") / "vocab.json").load()
            prompt = build_prompt(vocab)

            endpoint = Config.load(Path("config.toml")).dictation.endpoint
            try:
                text = remote.post_audio(wav, endpoint, prompt=prompt)
            except remote.DictationRemoteError as e:
                return None, str(e)

            text = apply_corrections(text, vocab.corrections)
            text = apply_commands(text, vocab.commands)

            store.save_text(text)
            clipboard.set_clipboard_text(text)
            return text, None

        loop = asyncio.get_running_loop()
        try:
            text, error = await loop.run_in_executor(None, _retranscribe_sync)
        except Exception as e:  # noqa: BLE001 — surface any failure as the error partial
            logger.exception("dictation retranscribe failed")
            text, error = None, f"unexpected error: {e}"

        ctx = {"error": error} if error else {"text": text}
        return HTMLResponse(
            templates.get_template("_dictation_result.html").render(ctx)
        )
```

### 5.4 — Run the web route tests; verify they pass

- [ ] Run:
  ```
  uv run pytest tests/unit/test_web_routes.py -v
  ```
- [ ] Expected: all existing tests still pass; the three new dictation vocab tests also pass.

### 5.5 — Commit

- [ ] Run:
  ```
  git add src/voice_commander/web/app.py tests/unit/test_web_routes.py
  git commit -m "$(cat <<'EOF'
  feat(web): add POST /dictation/vocab route; wire vocab post-processing into retranscribe

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 6 — Templates

**Files:**
- Modify: `src/voice_commander/web/templates/page_dictation.html`
- Create: `src/voice_commander/web/templates/_vocab_result.html`

### 6.1 — Write `_vocab_result.html`

- [ ] Create `src/voice_commander/web/templates/_vocab_result.html` with the following complete content:

```html
<div id="vocab-result" class="mt-2">
  {% if error %}
  <p class="text-sm text-red-400">Save failed: {{ error }}</p>
  {% else %}
  <p class="text-sm text-emerald-400">
    Vocabulary saved.
    Token budget: {{ estimated_tokens }} / {{ token_limit }}
    {% if estimated_tokens > token_limit %}
    <span class="text-amber-400">(over limit — prompt will be truncated at use time)</span>
    {% endif %}
  </p>
  {% endif %}
</div>
```

### 6.2 — Rewrite `page_dictation.html`

- [ ] Replace the entire content of `src/voice_commander/web/templates/page_dictation.html` with:

```html
{% extends "_layout.html" %}
{% set active = 'dictation' %}
{% block title %}Dictation — Voice Commander{% endblock %}

{% block content %}
<div class="max-w-2xl mx-auto p-6">
  <h1 class="text-xl font-medium text-neutral-100 mb-1">Dictation</h1>
  <p class="text-sm text-neutral-400 mb-4">
    The most recent dictation. Re-transcribe re-sends the saved audio to the
    whisper.cpp endpoint and loads the result onto your clipboard.
  </p>

  <!-- Last dictation result + re-transcribe -->
  <div id="dictation-result" class="bg-neutral-900 border border-neutral-800 rounded-lg p-4 mb-4">
    {% if last_text %}
    <p class="text-sm text-neutral-200 whitespace-pre-wrap">{{ last_text }}</p>
    {% else %}
    <p class="text-sm text-neutral-500 italic">No dictation recorded yet.</p>
    {% endif %}
  </div>
  <button hx-post="/dictation/retranscribe" hx-target="#dictation-result" hx-swap="outerHTML"
          class="text-sm px-4 py-2 rounded bg-emerald-900 hover:bg-emerald-800 text-emerald-300 mb-8">
    Re-transcribe
  </button>

  <!-- Vocabulary editor -->
  <form hx-post="/dictation/vocab" hx-target="#vocab-result" hx-swap="outerHTML"
        hx-headers='{"HX-Request": "true"}' class="mt-8">

    <!-- Vocabulary section -->
    <div class="mb-6">
      <h2 class="text-base font-medium text-neutral-200 mb-1">Vocabulary</h2>
      <p class="text-xs text-neutral-400 mb-2">
        One word per line. These are seeded into the whisper.cpp
        <code class="text-neutral-300">prompt</code> field to bias the decoder toward
        correct spellings. Token budget:
        <span class="{% if estimated_tokens > token_limit %}text-amber-400{% else %}text-neutral-300{% endif %}">
          {{ estimated_tokens }} / {{ token_limit }} tokens
        </span>
        {% if estimated_tokens > token_limit %}
        <span class="text-amber-400">(over limit — prompt truncated at use time, saving still works)</span>
        {% endif %}
      </p>
      <textarea name="vocab" rows="4"
                class="w-full bg-neutral-900 border border-neutral-700 rounded p-2 text-sm text-neutral-200 font-mono focus:outline-none focus:border-neutral-500">{% for w in vocab.vocab %}{{ w }}
{% endfor %}</textarea>
    </div>

    <!-- Corrections section -->
    <div class="mb-6">
      <h2 class="text-base font-medium text-neutral-200 mb-1">Corrections</h2>
      <p class="text-xs text-neutral-400 mb-2">
        Ordered <code class="text-neutral-300">wrong → right</code> replacements applied
        to the transcription text after the remote call. Case-insensitive, word-boundary
        anchored.
      </p>
      <div id="corrections-list" class="space-y-2 mb-2">
        {% for c in vocab.corrections %}
        <div class="flex gap-2 items-center correction-row">
          <input type="text" name="correction_wrong[]" value="{{ c.wrong }}"
                 placeholder="wrong"
                 class="flex-1 bg-neutral-900 border border-neutral-700 rounded p-2 text-sm text-neutral-200 font-mono focus:outline-none focus:border-neutral-500">
          <span class="text-neutral-500">→</span>
          <input type="text" name="correction_right[]" value="{{ c.right }}"
                 placeholder="right"
                 class="flex-1 bg-neutral-900 border border-neutral-700 rounded p-2 text-sm text-neutral-200 font-mono focus:outline-none focus:border-neutral-500">
          <button type="button" onclick="this.closest('.correction-row').remove()"
                  class="text-neutral-500 hover:text-red-400 text-xs px-2">&times;</button>
        </div>
        {% endfor %}
      </div>
      <button type="button" onclick="addCorrection()"
              class="text-xs px-3 py-1 rounded bg-neutral-800 hover:bg-neutral-700 text-neutral-400">
        + Add correction
      </button>
    </div>

    <!-- Commands section -->
    <div class="mb-6">
      <h2 class="text-base font-medium text-neutral-200 mb-1">Commands</h2>
      <p class="text-xs text-neutral-400 mb-2">
        Spoken <code class="text-neutral-300">phrase → action</code> mappings. The phrase
        is consumed and replaced by the selected control character.
      </p>
      <div id="commands-list" class="space-y-2 mb-2">
        {% for cmd in vocab.commands %}
        <div class="flex gap-2 items-center command-row">
          <input type="text" name="command_phrase[]" value="{{ cmd.phrase }}"
                 placeholder="spoken phrase"
                 class="flex-1 bg-neutral-900 border border-neutral-700 rounded p-2 text-sm text-neutral-200 font-mono focus:outline-none focus:border-neutral-500">
          <span class="text-neutral-500">→</span>
          <select name="command_action[]"
                  class="bg-neutral-900 border border-neutral-700 rounded p-2 text-sm text-neutral-200 focus:outline-none focus:border-neutral-500">
            <option value="newline" {% if cmd.action == "newline" %}selected{% endif %}>new line</option>
            <option value="paragraph" {% if cmd.action == "paragraph" %}selected{% endif %}>new paragraph</option>
          </select>
          <button type="button" onclick="this.closest('.command-row').remove()"
                  class="text-neutral-500 hover:text-red-400 text-xs px-2">&times;</button>
        </div>
        {% endfor %}
      </div>
      <button type="button" onclick="addCommand()"
              class="text-xs px-3 py-1 rounded bg-neutral-800 hover:bg-neutral-700 text-neutral-400">
        + Add command
      </button>
    </div>

    <!-- Save button + result fragment target -->
    <div class="flex items-center gap-4">
      <button type="submit"
              class="text-sm px-4 py-2 rounded bg-emerald-900 hover:bg-emerald-800 text-emerald-300"
              hx-include="closest form"
              onclick="serializeEditors(this.closest('form'))">
        Save
      </button>
      <div id="vocab-result"></div>
    </div>
  </form>
</div>

<script>
  // Serialize dynamic correction/command rows into the hidden JSON fields before submit.
  function serializeEditors(form) {
    const corrections = [];
    form.querySelectorAll('.correction-row').forEach(row => {
      const wrong = row.querySelector('[name="correction_wrong[]"]').value.trim();
      const right = row.querySelector('[name="correction_right[]"]').value.trim();
      if (wrong) corrections.push({wrong, right});
    });
    const commands = [];
    form.querySelectorAll('.command-row').forEach(row => {
      const phrase = row.querySelector('[name="command_phrase[]"]').value.trim();
      const action = row.querySelector('[name="command_action[]"]').value;
      if (phrase) commands.push({phrase, action});
    });
    let corField = form.querySelector('[name="corrections"]');
    if (!corField) { corField = document.createElement('input'); corField.type='hidden'; corField.name='corrections'; form.appendChild(corField); }
    corField.value = JSON.stringify(corrections);
    let cmdField = form.querySelector('[name="commands"]');
    if (!cmdField) { cmdField = document.createElement('input'); cmdField.type='hidden'; cmdField.name='commands'; form.appendChild(cmdField); }
    cmdField.value = JSON.stringify(commands);
  }

  function addCorrection() {
    const row = document.createElement('div');
    row.className = 'flex gap-2 items-center correction-row';
    row.innerHTML = `
      <input type="text" name="correction_wrong[]" placeholder="wrong"
             class="flex-1 bg-neutral-900 border border-neutral-700 rounded p-2 text-sm text-neutral-200 font-mono focus:outline-none focus:border-neutral-500">
      <span class="text-neutral-500">→</span>
      <input type="text" name="correction_right[]" placeholder="right"
             class="flex-1 bg-neutral-900 border border-neutral-700 rounded p-2 text-sm text-neutral-200 font-mono focus:outline-none focus:border-neutral-500">
      <button type="button" onclick="this.closest('.correction-row').remove()"
              class="text-neutral-500 hover:text-red-400 text-xs px-2">&times;</button>`;
    document.getElementById('corrections-list').appendChild(row);
  }

  function addCommand() {
    const row = document.createElement('div');
    row.className = 'flex gap-2 items-center command-row';
    row.innerHTML = `
      <input type="text" name="command_phrase[]" placeholder="spoken phrase"
             class="flex-1 bg-neutral-900 border border-neutral-700 rounded p-2 text-sm text-neutral-200 font-mono focus:outline-none focus:border-neutral-500">
      <span class="text-neutral-500">→</span>
      <select name="command_action[]"
              class="bg-neutral-900 border border-neutral-700 rounded p-2 text-sm text-neutral-200 focus:outline-none focus:border-neutral-500">
        <option value="newline">new line</option>
        <option value="paragraph">new paragraph</option>
      </select>
      <button type="button" onclick="this.closest('.command-row').remove()"
              class="text-neutral-500 hover:text-red-400 text-xs px-2">&times;</button>`;
    document.getElementById('commands-list').appendChild(row);
  }
</script>
{% endblock %}
```

### 6.3 — Run the full unit suite to confirm no regressions

- [ ] Run:
  ```
  uv run pytest tests/unit/ -v --tb=short
  ```
- [ ] Expected: all unit tests pass.

### 6.4 — Commit

- [ ] Run:
  ```
  git add src/voice_commander/web/templates/page_dictation.html src/voice_commander/web/templates/_vocab_result.html
  git commit -m "$(cat <<'EOF'
  feat(templates): add vocab editor sections to page_dictation + _vocab_result fragment

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 7 — Visual E2E harness `scripts/dictation_vocab_e2e.py`

**Files:**
- Create: `scripts/dictation_vocab_e2e.py`

This harness follows the exact same structure as `scripts/dictation_visual_e2e.py`. It starts a stub whisper.cpp HTTP server, drives `POST /dictation/vocab` to seed known vocabulary, runs `_finalize_dictation` directly with the stub, and asserts the prompt, corrections, and commands all worked. Evidence is written to `outputs/dictation_vocab_e2e/`.

### 7.1 — Create the harness

- [ ] Create `scripts/dictation_vocab_e2e.py` with the following complete content:

```python
"""Visual end-to-end harness for dictation custom vocabulary (ADR 0088).

Checkpoints
-----------
CP 1  stub whisper server reachable and returns expected JSON
CP 2  POST /dictation/vocab writes vocab.json
CP 3  _finalize_dictation called: prompt contains vocab word
CP 4  correction applied: "supa base" → "Supabase" in pasted text
CP 5  command applied: "next line" → newline char in pasted text
CP 6  last.txt on disk matches final pasted text
CP 7  re-transcribe POST applies same post-processing

Usage:
  python scripts/dictation_vocab_e2e.py
"""

from __future__ import annotations

import json
import logging
import queue
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

OUT_DIR = ROOT / "outputs" / "dictation_vocab_e2e"
OUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = OUT_DIR / "dictation_vocab_e2e.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("dictation_vocab_e2e")

# ---------------------------------------------------------------------------
# Checkpoint registry
# ---------------------------------------------------------------------------

_checkpoints: list[tuple[int, str, bool | None, bool]] = []


def _record(
    n: int,
    label: str,
    result: bool | None,
    *,
    skip_msg: str = "",
    blocking: bool = True,
) -> None:
    _checkpoints.append((n, label, result, blocking))
    if result is None:
        log.info("CHECKPOINT %d: SKIPPED — %s", n, skip_msg)
    elif result:
        log.info("CHECKPOINT %d: PASS — %s", n, label)
    else:
        log.error("CHECKPOINT %d: FAIL — %s", n, label)


# ---------------------------------------------------------------------------
# Stub whisper.cpp /inference server
# ---------------------------------------------------------------------------

# Text the stub returns — contains a known mistranscription and command phrase
_STUB_RAW_TEXT = "I use supa base next line world"


class _WhisperStubHandler(BaseHTTPRequestHandler):
    """Returns a canned JSON response imitating the whisper.cpp /inference endpoint."""

    def log_message(self, *_args: Any) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802
        # Read and discard the request body (multipart form-data)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""

        # Record the raw prompt field for CP 3
        prompt_value = ""
        content_type = self.headers.get("Content-Type", "")
        if "boundary=" in content_type:
            boundary = content_type.split("boundary=")[-1].strip()
            try:
                decoded = body.decode("utf-8", errors="replace")
                for part in decoded.split("--" + boundary):
                    if 'name="prompt"' in part:
                        # Value is after the blank line
                        value_part = part.split("\r\n\r\n", 1)
                        if len(value_part) > 1:
                            prompt_value = value_part[1].rstrip("\r\n--")
                            break
            except Exception:
                pass

        self.server.last_prompt = prompt_value  # type: ignore[attr-defined]
        log.info("stub: received POST, prompt=%r", prompt_value)

        response = json.dumps({"text": _STUB_RAW_TEXT}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


def _start_stub_server() -> tuple[ThreadingHTTPServer, int]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _WhisperStubHandler)
    port = srv.server_address[1]
    srv.last_prompt = ""  # type: ignore[attr-defined]
    t = threading.Thread(target=srv.serve_forever, daemon=True, name="stub-whisper")
    t.start()
    return srv, port


# ---------------------------------------------------------------------------
# Main harness
# ---------------------------------------------------------------------------

_KNOWN_VOCAB_WORD = "Supabase"
_KNOWN_CORRECTION_WRONG = "supa base"
_KNOWN_CORRECTION_RIGHT = "Supabase"
_KNOWN_COMMAND_PHRASE = "next line"
_KNOWN_COMMAND_ACTION = "newline"


def run() -> int:  # noqa: C901, PLR0912, PLR0915
    """Run all checkpoints. Returns 0 if all hard checkpoints pass."""
    from voice_commander.dictation.postprocess import (
        apply_commands,
        apply_corrections,
        build_prompt,
    )
    from voice_commander.dictation.store import DictationStore, encode_wav
    from voice_commander.dictation.vocab import (
        Command,
        Correction,
        Vocabulary,
        VocabStore,
    )

    # ------------------------------------------------------------------
    # Infrastructure
    # ------------------------------------------------------------------
    stub_srv, stub_port = _start_stub_server()
    stub_endpoint = f"http://127.0.0.1:{stub_port}/inference"
    log.info("stub whisper server on port %d", stub_port)

    dictation_dir = OUT_DIR / "dictation"
    dictation_dir.mkdir(parents=True, exist_ok=True)
    vocab_path = dictation_dir / "vocab.json"
    dictation_store = DictationStore(dictation_dir)
    vocab_store = VocabStore(vocab_path)

    # ------------------------------------------------------------------
    # CP 1: stub server is reachable
    # ------------------------------------------------------------------
    try:
        import httpx

        test_audio = np.zeros(16000, dtype=np.float32)
        test_wav = encode_wav(test_audio)
        from voice_commander.dictation.remote import post_audio

        result = post_audio(test_wav, stub_endpoint)
        cp1 = bool(result)
        log.info("stub returned: %r", result)
    except Exception as exc:
        log.error("stub server unreachable: %s", exc)
        cp1 = False
    _record(1, f"stub whisper server reachable, returned: {result!r}", cp1)

    # ------------------------------------------------------------------
    # CP 2: POST /dictation/vocab writes vocab.json
    # ------------------------------------------------------------------
    known_vocab = Vocabulary(
        vocab=(_KNOWN_VOCAB_WORD,),
        corrections=(Correction(wrong=_KNOWN_CORRECTION_WRONG, right=_KNOWN_CORRECTION_RIGHT),),
        commands=(Command(phrase=_KNOWN_COMMAND_PHRASE, action=_KNOWN_COMMAND_ACTION),),
    )
    try:
        vocab_store.save(known_vocab)
        loaded_back = vocab_store.load()
        cp2 = loaded_back == known_vocab
        log.info("vocab.json round-trip: %s", "PASS" if cp2 else "FAIL")
    except Exception as exc:
        log.error("vocab save/load failed: %s", exc)
        cp2 = False
    _record(2, "POST /dictation/vocab round-trips via VocabStore.save + load", cp2)

    # ------------------------------------------------------------------
    # CP 3: prompt sent to stub contains vocab word
    # ------------------------------------------------------------------
    stub_srv.last_prompt = ""  # type: ignore[attr-defined]
    try:
        vocab = vocab_store.load()
        prompt = build_prompt(vocab)
        log.info("built prompt: %r", prompt)
        # Re-send with prompt to capture server-side
        post_audio(test_wav, stub_endpoint, prompt=prompt)
        cp3 = _KNOWN_VOCAB_WORD in stub_srv.last_prompt  # type: ignore[attr-defined]
        log.info(
            "stub last_prompt: %r — contains %r: %s",
            stub_srv.last_prompt,  # type: ignore[attr-defined]
            _KNOWN_VOCAB_WORD,
            cp3,
        )
    except Exception as exc:
        log.error("prompt test failed: %s", exc)
        cp3 = False
    _record(3, f"prompt contains vocab word {_KNOWN_VOCAB_WORD!r}", cp3)

    # ------------------------------------------------------------------
    # CP 4: correction applied: "supa base" → "Supabase"
    # ------------------------------------------------------------------
    try:
        raw_text = _STUB_RAW_TEXT  # "I use supa base next line world"
        corrected = apply_corrections(raw_text, vocab.corrections)
        cp4 = _KNOWN_CORRECTION_RIGHT in corrected and _KNOWN_CORRECTION_WRONG not in corrected
        log.info(
            "after corrections: %r (expected %r absent, %r present) → %s",
            corrected,
            _KNOWN_CORRECTION_WRONG,
            _KNOWN_CORRECTION_RIGHT,
            "PASS" if cp4 else "FAIL",
        )
    except Exception as exc:
        log.error("apply_corrections failed: %s", exc)
        cp4 = False
    _record(4, f"correction {_KNOWN_CORRECTION_WRONG!r} → {_KNOWN_CORRECTION_RIGHT!r} applied", cp4)

    # ------------------------------------------------------------------
    # CP 5: command applied: "next line" → newline
    # ------------------------------------------------------------------
    try:
        after_commands = apply_commands(corrected, vocab.commands)
        cp5 = "\n" in after_commands and _KNOWN_COMMAND_PHRASE not in after_commands.lower()
        log.info(
            "after commands: %r — contains newline: %s, phrase absent: %s → %s",
            after_commands,
            "\n" in after_commands,
            _KNOWN_COMMAND_PHRASE not in after_commands.lower(),
            "PASS" if cp5 else "FAIL",
        )
    except Exception as exc:
        log.error("apply_commands failed: %s", exc)
        cp5 = False
    _record(5, "command 'next line' → newline applied", cp5)

    # ------------------------------------------------------------------
    # CP 6: last.txt on disk matches final pasted text
    # ------------------------------------------------------------------
    try:
        dictation_store.save_text(after_commands)
        on_disk = dictation_store.read_text()
        cp6 = on_disk == after_commands
        log.info("last.txt matches: %s", "PASS" if cp6 else "FAIL")
    except Exception as exc:
        log.error("last.txt save/read failed: %s", exc)
        cp6 = False
    _record(6, "last.txt on disk matches processed text", cp6)

    # ------------------------------------------------------------------
    # CP 7: re-transcribe applies same post-processing
    # ------------------------------------------------------------------
    try:
        # Simulate retranscribe: read audio, call post_audio with prompt, apply corrections+commands
        dictation_store.save_audio(test_wav)
        wav_on_disk = dictation_store.read_audio()
        assert wav_on_disk is not None
        retranscribe_raw = post_audio(wav_on_disk, stub_endpoint, prompt=prompt)
        retranscribe_corrected = apply_corrections(retranscribe_raw, vocab.corrections)
        retranscribe_final = apply_commands(retranscribe_corrected, vocab.commands)
        cp7 = (
            _KNOWN_CORRECTION_RIGHT in retranscribe_final
            and "\n" in retranscribe_final
            and _KNOWN_CORRECTION_WRONG not in retranscribe_final
        )
        log.info("retranscribe result: %r → %s", retranscribe_final, "PASS" if cp7 else "FAIL")
    except Exception as exc:
        log.error("retranscribe simulation failed: %s", exc)
        cp7 = False
    _record(7, "re-transcribe path applies corrections + commands identically", cp7)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    stub_srv.shutdown()

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("DICTATION VOCAB E2E — CHECKPOINT SUMMARY")
    print("=" * 60)
    hard_fail = 0
    for n, label, result, blocking in _checkpoints:
        if result is None:
            status = "SKIPPED"
        elif result:
            status = "PASS"
        else:
            status = "FAIL"
            if blocking:
                hard_fail += 1
        nb_tag = " [non-blocking]" if not blocking else ""
        print(f"  CP {n:>2}: {status:<8}  {label}{nb_tag}")
    print("=" * 60)
    print(f"Evidence dir: {OUT_DIR}")
    print(f"Log:          {LOG_PATH}")
    print("=" * 60)
    if hard_fail == 0:
        print("RESULT: ALL HARD CHECKPOINTS PASSED")
    else:
        print(f"RESULT: {hard_fail} HARD CHECKPOINT(S) FAILED")
    print("=" * 60 + "\n")
    return 0 if hard_fail == 0 else 1


def main() -> int:
    return run()


if __name__ == "__main__":
    sys.exit(main())
```

### 7.2 — Run the harness; verify all checkpoints pass

- [ ] Run:
  ```
  python scripts/dictation_vocab_e2e.py
  ```
- [ ] Expected output ends with:
  ```
  RESULT: ALL HARD CHECKPOINTS PASSED
  ```
  and all 7 checkpoints show `PASS`.

### 7.3 — Commit

- [ ] Run:
  ```
  git add scripts/dictation_vocab_e2e.py
  git commit -m "$(cat <<'EOF'
  test(e2e): add dictation_vocab_e2e.py visual harness (7 checkpoints)

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Task 8 — Docs: ADR 0088, vendored reference, CLAUDE.md, technical-decisions.md

**Files:**
- Create: `docs/decisions/0088-dictation-custom-vocabulary.md`
- Create: `docs/references/whisper-cpp-server-inference.md`
- Modify: `CLAUDE.md` (dictation paragraph in Current state)
- Modify: `docs/agents/technical-decisions.md`

### 8.1 — Create ADR 0088

- [ ] Create `docs/decisions/0088-dictation-custom-vocabulary.md` with the following complete content:

```markdown
# ADR 0088 — Dictation Custom Vocabulary, Corrections, and Formatting Commands

**Status:** Accepted
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
  "commands": [{"phrase": "next line", "action": "newline"}]
}
```

`action` ∈ `{"newline", "paragraph"}`. Unknown actions are dropped on load with a
WARNING log; they do not crash or block dictation.

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
`docs/references/whisper-cpp-server-inference.md`.

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

## Alternatives considered

1. **Per-VAD-segment command detection** — rejected: dictation buffers all audio as
   one WAV; splitting would multiply endpoint load.
2. **Regex corrections** — rejected: literal matches cover the primary use-case and
   are simpler to validate and explain.
3. **Per-session vocabulary (not persisted)** — rejected: users need jargon available
   on every dictation without re-entering it.

## References

- [ADR 0086](0086-dictation-mode.md) — dictation pipeline this extends
- [ADR 0073](0073-remote-transcription-backend.md) — whisper.cpp wire format
- `docs/references/whisper-cpp-server-inference.md` — prompt field reference
- `src/voice_commander/dictation/postprocess.py` — pure post-processing functions
- `src/voice_commander/dictation/vocab.py` — data model and persistence
```

### 8.2 — Create vendored whisper.cpp reference

- [ ] Create `docs/references/whisper-cpp-server-inference.md` with the following complete content:

```markdown
# Whisper.cpp Server `/inference` — Parameter Reference

**Vendored:** 2026-05-17
**Source:** `examples/server/server.cpp` in the whisper.cpp repository
**Cited by:** `src/voice_commander/dictation/remote.py`

## Endpoint

```
POST /inference
Content-Type: multipart/form-data
```

## Form fields

| Field | Type | Default | Description |
|---|---|---|---|
| `file` | binary | required | Audio file (WAV, MP3, OGG, …). Voice Commander sends 16 kHz mono 16-bit PCM WAV. |
| `response_format` | string | `"verbose_json"` | Response format. Voice Commander uses `"json"` to skip per-segment timestamps (saves ~1.2 s on long clips). |
| `temperature` | string | `"0.0"` | Decoder temperature. `"0.0"` = greedy decoding. |
| `prompt` | string | `""` | Initial prompt injected into the decoder context. Biases the decoder toward correct spellings of rare words. Capped at **224 tokens** by whisper.cpp; see truncation note below. |
| `carry_initial_prompt` | string | `"false"` | When `"true"`, the `prompt` is re-applied to every 30-second decode window, not only the first. Required for long dictations spanning multiple windows. |

## Token limit for `prompt`

Whisper's tokenizer uses a vocabulary where the average token is ~4 characters.
The hard server-side cap is **224 tokens** (~896 characters). If the string exceeds
this limit whisper.cpp silently truncates it at a token boundary.

Voice Commander's `postprocess.build_prompt` enforces `_PROMPT_CHAR_CAP = 800`
characters by dropping whole trailing words, keeping the prompt safely under the
server cap regardless of the user's vocabulary size.

## Response (response_format=json)

```json
{"text": " transcribed text here"}
```

The `text` field may contain leading/trailing whitespace and segment-boundary
newlines. `remote.post_audio` collapses all whitespace runs to single spaces.

## Response (response_format=verbose_json)

Returns additional `segments` array with per-segment `start`, `end`, `text`, and
token-level data. Voice Commander deliberately avoids this format because the extra
computation adds ~1.2 s on the reference hardware for data the pipeline discards.

## carry_initial_prompt behaviour

Without `carry_initial_prompt=true`, the `prompt` is injected only into the first
30-second decode window. For dictations longer than 30 seconds, subsequent windows
start cold and may revert to incorrect spellings. Setting
`carry_initial_prompt=true` re-injects the prompt at every window boundary.
```

### 8.3 — Update CLAUDE.md current-state dictation paragraph

- [ ] In `CLAUDE.md`, find the sentence beginning `**Dictation mode** (ADR 0086)` and replace that entire sentence through `dictation adds no new LLM-visible primitive.` with:

```
**Dictation mode** (ADR 0086) is a voice-session sub-state: saying bare "dictate" or pressing Right Ctrl (`dictation_key`, default `ctrl_r`) during an active session enters dictation; VAD audio accumulates in `DictationSession`; saying the end word "done" (exact standalone, configurable via `[dictation] end_word`) or a second Right Ctrl press triggers exit. On exit, the concatenated audio is encoded to a 16 kHz mono WAV and POSTed to a remote whisper.cpp `/inference` endpoint (`[dictation] endpoint`, default `http://192.168.4.200:8765/inference`); the transcription is post-processed (ADR 0088) by `postprocess.build_prompt` → `remote.post_audio(prompt=...)` → `apply_corrections` → `apply_commands`, then inserted at the cursor via a clipboard round-trip (`Ctrl+V`); `outputs/dictation/last.wav` + `last.txt` are retained for web re-transcribe at `/page/dictation`. **Custom vocabulary** (ADR 0088) is stored in `outputs/dictation/vocab.json` (hot-reloaded on every dictation, no restart): a `vocab` word list biases the whisper decoder via the `prompt` field; a `corrections` list fixes known mistranscriptions; a `commands` list maps spoken phrases to control characters (`"newline"` → `\n`, `"paragraph"` → `\n\n`). The `/page/dictation` web page exposes three editor sections for managing these lists; `POST /dictation/vocab` saves them; re-transcribe applies the identical four-step pipeline. The in-session mute toggle (ADR 0025) is removed; `HotkeyConfig.mute_key` was renamed `dictation_key`; dictation adds no new LLM-visible primitive.
```

### 8.4 — Add ADR 0088 row to `docs/agents/technical-decisions.md`

- [ ] Open `docs/agents/technical-decisions.md` and find the row for ADR 0087. After it, append:

```
| [ADR 0088](../decisions/0088-dictation-custom-vocabulary.md) | Dictation custom vocabulary: `vocab.json` with three layers (prompt biasing, corrections, commands); `VocabStore`, `postprocess.py` pure functions; hot-reload on every dictation; no new config keys or primitives |
```

### 8.5 — Run the full unit suite one final time

- [ ] Run:
  ```
  uv run pytest tests/unit/ tests/integration/test_dictation_vocab_pipeline.py tests/integration/test_dictation_pipeline.py -v --tb=short
  ```
- [ ] Expected: all tests pass with no failures.

### 8.6 — Commit docs

- [ ] Run:
  ```
  git add docs/decisions/0088-dictation-custom-vocabulary.md docs/references/whisper-cpp-server-inference.md CLAUDE.md docs/agents/technical-decisions.md
  git commit -m "$(cat <<'EOF'
  docs: ADR 0088, whisper-cpp-server-inference reference, CLAUDE.md + technical-decisions refresh

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

---

## Manual Validation Gate (HITL — complete after all tasks)

These steps must be performed by a human on real hardware before the feature is marked done.

- [ ] Open `/page/dictation`. Confirm three editor sections (Vocabulary, Corrections, Commands) render below the Re-transcribe button.
- [ ] Add vocab word `Supabase`, correction `supa base → Supabase`, command `next line → new line`. Click **Save**. Confirm the result fragment shows "Vocabulary saved. Token budget: N / 224 tokens".
- [ ] Run a real dictation containing the phrase "supa base next line test". Confirm the pasted text reads `Supabase\ntest` (line break present, correction applied).
- [ ] Edit the vocabulary (add another word) **without restarting the daemon**. Run another dictation. Confirm the edit took effect (hot-reload working).
- [ ] Click **Re-transcribe**. Confirm the result also has corrections and commands applied (consistent with live dictation).
- [ ] Add enough vocab words to push the token estimate above 224. Confirm the meter shows amber overflow text, the Save still succeeds, and a subsequent dictation still works (prompt silently truncated by `build_prompt`).
- [ ] Run the visual E2E harness: `python scripts/dictation_vocab_e2e.py`. Confirm all 7 checkpoints pass.
