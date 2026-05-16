"""Parse a spoken or written number from a transcript.

Whisper emits numbers either as digits ("17") or as words ("seventeen",
"twenty three"). This module accepts both and rejects everything else;
homophone guessing ("for" -> 4) is deliberately omitted as too error-prone.
"""

from __future__ import annotations

_ONES: dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
}
_TEENS: dict[str, int] = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS: dict[str, int] = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}


def parse_number(text: str) -> int | None:
    """Return the integer named by ``text``, or ``None`` if it is not a number."""
    cleaned = text.strip().lower().replace("-", " ")
    cleaned = "".join(ch for ch in cleaned if ch.isalnum() or ch.isspace())
    tokens = cleaned.split()
    if not tokens:
        return None
    if len(tokens) == 1:
        word = tokens[0]
        if word.isdigit():
            return int(word)
        if word in _ONES:
            return _ONES[word]
        if word in _TEENS:
            return _TEENS[word]
        if word in _TENS:
            return _TENS[word]
        return None
    if len(tokens) == 2:
        tens, ones = tokens
        if tens in _TENS and ones in _ONES and _ONES[ones] != 0:
            return _TENS[tens] + _ONES[ones]
        return None
    return None
