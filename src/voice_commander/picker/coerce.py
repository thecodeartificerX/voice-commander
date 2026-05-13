"""Coerce a spoken transcript to a picker index (1-based).

Recognises bare digits, spelled cardinals (one..nine), spelled ordinals
(first..ninth), and the common prefixed forms ("number three", "option 2",
"the third", "third one"). Returns ``None`` for anything outside that
vocabulary or outside ``[1, max_n]``.
"""

from __future__ import annotations

import re

_WORDS: dict[str, int] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
}

_STRIP_PUNCT_RE = re.compile(r"[^a-z0-9 -]+")
_PREFIX_TOKENS = frozenset({"number", "option", "the"})
_SUFFIX_TOKENS = frozenset({"one", "option"})


def _normalise(text: str) -> str:
    normalized = " ".join(_STRIP_PUNCT_RE.sub(" ", text.lower()).split())
    # Reject if there's a leading hyphen (negative number)
    if normalized.startswith("-"):
        return ""
    return normalized


def coerce_number(text: str, max_n: int) -> int | None:
    """Return the 1-based index encoded by *text*, or ``None``.

    Parameters
    ----------
    text:
        Raw transcript text. Punctuation and case are normalised.
    max_n:
        Inclusive upper bound. Values outside ``[1, max_n]`` return ``None``.
        ``max_n <= 0`` always returns ``None``.
    """
    if max_n <= 0:
        return None
    normalised = _normalise(text)
    if not normalised:
        return None

    tokens = normalised.split()
    while tokens and tokens[0] in _PREFIX_TOKENS:
        tokens.pop(0)
    while tokens and tokens[-1] in _SUFFIX_TOKENS and len(tokens) > 1:
        tokens.pop()

    if len(tokens) != 1:
        return None
    token = tokens[0]

    if token.isdigit():
        try:
            n = int(token)
        except ValueError:
            return None
    elif token in _WORDS:
        n = _WORDS[token]
    else:
        return None

    if 1 <= n <= max_n:
        return n
    return None
