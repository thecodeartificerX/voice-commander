"""Pure post-processing functions for the dictation pipeline.

No I/O — all functions are fully unit-testable without any file system or
network access.

References
----------
- docs/references/ws-transcribe-server.md — prompt field, 224-token limit
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from .vocab import Command, Correction, Vocabulary

# whisper.cpp's initial-prompt token cap.
WHISPER_TOKEN_LIMIT = 224

# ~200 tokens at ~4 chars/token; safely under whisper.cpp's 224-token hard limit.
_PROMPT_CHAR_CAP = 800

# Extend this mapping to add new formatting actions.
_ACTION_CHARS: dict[str, str] = {
    "newline": "\n",
    "paragraph": "\n\n",
}

# Punctuation characters that conventionally precede a space in English prose
# (e.g. "word. Next" not "word.Next").  When ``Command.insert`` is one of these
# single characters, ``apply_commands`` preserves the trailing space that follows
# the matched phrase rather than consuming it.  Multi-character inserts (e.g.
# " - ") and joiner chars (e.g. "/" "-") always consume both surrounding spaces.
_SENTENCE_ENDERS: frozenset[str] = frozenset(".,;:!?)]}")


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
        if not correction.wrong:
            continue
        pattern = re.compile(r"\b" + re.escape(correction.wrong) + r"\b", re.IGNORECASE)
        text = pattern.sub(lambda _m: correction.right, text)
    return text


def apply_commands(text: str, commands: Sequence[Command]) -> str:
    r"""Replace spoken command phrases with a literal insert string or control char.

    Two entry shapes are supported (``insert`` takes precedence):

    * ``Command(phrase="full stop", insert=".")`` — splices the literal ``"."``
      into the transcript.
    * ``Command(phrase="next line", action="newline")`` — legacy shape; maps to
      ``"\n"`` via ``_ACTION_CHARS``.

    **Whitespace handling.**  Each phrase is matched with a leading ``\s*`` so
    the space *before* the spoken word is always consumed.  The trailing ``\s*``
    is applied only when the insert is **not** a sentence-ending punctuation
    character (``.,;:!?)]}``) — for those characters English typography expects
    a space after them (e.g. ``"word. Next"``), so the original trailing space
    is preserved.

    Examples::

        "picked dash up"    + {"phrase":"dash",      "insert":"-"}  → "picked-up"
        "handover full stop"+ {"phrase":"full stop",  "insert":"."}  → "handover. "
        "no tbd space dash space end"
                            + {"phrase":"space dash space","insert":" - "} → "no tbd - end"

    Assumes *text* contains no embedded newlines — the ``\s*`` whitespace
    consumption would otherwise silently eat them.  ``ws_client.stream_transcribe``
    guarantees this invariant: the server returns the transcript as a single
    line of space-separated words with no embedded newlines.
    """
    for command in commands:
        if not command.phrase:
            continue
        # Resolve the replacement string.
        if command.insert:
            char = command.insert
        else:
            char = _ACTION_CHARS.get(command.action, "")
        if not char:
            continue

        # Determine whether to consume the trailing space.
        # Sentence-ending single chars (. , ; : ! ? ) ] }) keep the trailing
        # space so the next word is not run-together with the punctuation.
        # All other inserts (joiners like / -, whitespace like \n, spacer
        # phrases like " - ") consume both surrounding spaces.
        eat_right = char not in _SENTENCE_ENDERS
        right_pat = r"\s*" if eat_right else ""
        pattern = re.compile(
            r"\s*\b" + re.escape(command.phrase) + r"\b" + right_pat,
            re.IGNORECASE,
        )
        text = pattern.sub(lambda _m: char, text)
    return text
