"""Unit tests for spoken-number parsing (ADR 0087)."""

import pytest

from voice_commander.elements.numbers import parse_number


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("7", 7),
        ("17", 17),
        ("200", 200),
        ("seven", 7),
        ("Seven.", 7),
        ("  nine  ", 9),
        ("zero", 0),
        ("ten", 10),
        ("seventeen", 17),
        ("nineteen", 19),
        ("twenty", 20),
        ("ninety", 90),
        ("twenty three", 23),
        ("twenty-three", 23),
        ("Twenty Three!", 23),
        ("forty two", 42),
    ],
)
def test_parses_numbers(text: str, expected: int) -> None:
    assert parse_number(text) == expected


@pytest.mark.parametrize(
    "text",
    ["", "   ", "hello", "click that", "five six", "one hundred", "twenty zero", "thirteen four"],
)
def test_rejects_non_numbers(text: str) -> None:
    assert parse_number(text) is None
