from __future__ import annotations

import pytest

from voice_commander.picker.coerce import coerce_number


@pytest.mark.parametrize(
    ("text", "max_n", "expected"),
    [
        # Bare digit forms
        ("1", 5, 1),
        ("5", 5, 5),
        ("3.", 5, 3),
        ("3!", 5, 3),
        ("  4  ", 5, 4),
        # Spelled words (1-9 supported even when max_n smaller)
        ("one", 5, 1),
        ("two", 5, 2),
        ("three", 5, 3),
        ("four", 5, 4),
        ("five", 5, 5),
        ("Three.", 5, 3),
        ("THREE", 5, 3),
        # Prefixed forms
        ("number 3", 5, 3),
        ("number three", 5, 3),
        ("option 3", 5, 3),
        ("the third", 5, 3),
        ("third", 5, 3),
        ("third one", 5, 3),
        ("first", 5, 1),
        ("fifth", 5, 5),
        ("second option", 5, 2),
    ],
)
def test_coerce_accepts(text: str, max_n: int, expected: int) -> None:
    assert coerce_number(text, max_n) == expected


@pytest.mark.parametrize(
    ("text", "max_n"),
    [
        ("", 5),
        ("   ", 5),
        ("hello", 5),
        ("twenty", 5),
        ("thirty", 5),
        ("0", 5),
        ("6", 5),  # > max_n
        ("seven", 5),  # > max_n
        ("seventh", 5),  # > max_n
        ("number 7", 5),  # > max_n
        ("number 0", 5),
        ("-1", 5),
        ("1.5", 5),
    ],
)
def test_coerce_rejects(text: str, max_n: int) -> None:
    assert coerce_number(text, max_n) is None


def test_coerce_zero_max_always_none() -> None:
    assert coerce_number("1", 0) is None
    assert coerce_number("one", 0) is None
