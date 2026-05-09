"""Unit tests for tools/keyboard_combo.py — alias parsing, pynput
canonicalisation, and validation against pyautogui's KEYBOARD_KEYS.
"""

from __future__ import annotations

import pytest
from pynput import keyboard

from voice_commander.tools.keyboard_combo import (
    UnknownKeyError,
    _pynput_modifier_name,
    format_combo,
    is_known_key,
    is_pynput_modifier,
    parse_combo,
    pynput_key_to_keyname,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Ctrl + L", ["ctrl", "l"]),
        ("control-l", ["ctrl", "l"]),
        ("Ctrl Shift T", ["ctrl", "shift", "t"]),
        ("control and shift and t", ["ctrl", "shift", "t"]),
        ("windows+r", ["win", "r"]),
        ("option,return", ["alt", "enter"]),
        ("PgUp", ["pageup"]),
        ("spacebar", ["space"]),
    ],
)
def test_parse_combo_aliases_and_separators(raw: str, expected: list[str]) -> None:
    assert parse_combo(raw) == expected


def test_parse_combo_empty() -> None:
    assert parse_combo("") == []
    assert parse_combo("   ") == []


def test_modifier_detection() -> None:
    assert _pynput_modifier_name(keyboard.Key.ctrl_l) == "ctrl"
    assert _pynput_modifier_name(keyboard.Key.ctrl_r) == "ctrl"
    assert _pynput_modifier_name(keyboard.Key.alt_l) == "alt"
    assert _pynput_modifier_name(keyboard.Key.shift_l) == "shift"
    assert _pynput_modifier_name(keyboard.Key.cmd) == "win"
    assert _pynput_modifier_name(keyboard.KeyCode.from_char("l")) is None
    assert is_pynput_modifier(keyboard.Key.ctrl_l) is True
    assert is_pynput_modifier(keyboard.KeyCode.from_char("a")) is False


def test_pynput_key_to_keyname_letter_lowercased() -> None:
    assert pynput_key_to_keyname(keyboard.KeyCode.from_char("L")) == "l"
    assert pynput_key_to_keyname(keyboard.KeyCode.from_char("3")) == "3"


def test_pynput_key_to_keyname_named() -> None:
    assert pynput_key_to_keyname(keyboard.Key.f11) == "f11"
    assert pynput_key_to_keyname(keyboard.Key.enter) == "enter"
    assert pynput_key_to_keyname(keyboard.Key.esc) == "esc"
    assert pynput_key_to_keyname(keyboard.Key.page_up) == "pageup"
    assert pynput_key_to_keyname(keyboard.Key.scroll_lock) == "scrolllock"


def test_pynput_key_to_keyname_unrepresentable() -> None:
    class Empty:
        char = None
        name = None

    assert pynput_key_to_keyname(Empty()) is None


def test_format_combo_canonical_modifier_order() -> None:
    # Modifiers always emitted in ctrl, alt, shift, win order.
    assert format_combo({"shift", "ctrl", "win"}, "l") == "ctrl+shift+win+l"
    assert format_combo({"alt"}, "tab") == "alt+tab"


def test_format_combo_main_only() -> None:
    assert format_combo(set(), "f11") == "f11"


def test_format_combo_modifier_only() -> None:
    assert format_combo({"ctrl"}, None) == "ctrl"


def test_format_combo_rejects_empty() -> None:
    with pytest.raises(UnknownKeyError):
        format_combo(set(), None)


def test_format_combo_rejects_unknown_main() -> None:
    with pytest.raises(UnknownKeyError):
        format_combo({"ctrl"}, "definitely-not-a-key")


def test_is_known_key_basics() -> None:
    assert is_known_key("ctrl") is True
    assert is_known_key("l") is True
    assert is_known_key("f11") is True
    assert is_known_key("definitely-not-a-key") is False
