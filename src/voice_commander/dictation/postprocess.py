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

# Extend this mapping to add new formatting actions.
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
        if not correction.wrong:
            continue
        pattern = re.compile(r"\b" + re.escape(correction.wrong) + r"\b", re.IGNORECASE)
        text = pattern.sub(lambda _m: correction.right, text)
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
        if not command.phrase:
            continue
        char = _ACTION_CHARS.get(command.action, "")
        if not char:
            continue
        pattern = re.compile(
            r"\s*\b" + re.escape(command.phrase) + r"\b\s*", re.IGNORECASE
        )
        text = pattern.sub(lambda _m: char, text)
    return text
