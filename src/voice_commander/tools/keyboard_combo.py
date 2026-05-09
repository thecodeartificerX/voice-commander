"""Shared canonicalisation for keyboard combos used by ``press`` and the
backend key recorder.

The catalog of aliases + the split regex used to live inline in
``primitives.py``; they were extracted here so the recorder can reuse
exactly the same normalisation that ``press(combo=...)`` accepts. The
two helpers added on top — ``parse_combo`` and ``pynput_to_combo`` —
turn raw human strings or pynput key events into a ``pyautogui.hotkey``-
compatible token list.
"""

from __future__ import annotations

import re

import pyautogui

# Whisper / LLM emit human spellings; pyautogui expects short keynames.
PRESS_ALIASES: dict[str, str] = {
    "control": "ctrl",
    "windows": "win",
    "windowskey": "win",
    "winkey": "win",
    "option": "alt",
    "return": "enter",
    "escape": "esc",
    "del": "delete",
    "ins": "insert",
    "pgup": "pageup",
    "pgdn": "pagedown",
    "spacebar": "space",
}

# Splits combo on '+', '-', whitespace, commas, or 'and' between tokens.
# Whisper often transcribes "Ctrl-C" with a hyphen, hence '-' is treated
# as a separator rather than part of a key name.
PRESS_SPLIT_RE = re.compile(r"\s*(?:\+|,|-|\band\b|\s)\s*", re.IGNORECASE)

# Modifier names in canonical order — used when emitting the recorder's
# captured combo so output is stable regardless of press order.
_MODIFIER_ORDER: tuple[str, ...] = ("ctrl", "alt", "shift", "win")
_MODIFIER_SET: frozenset[str] = frozenset(_MODIFIER_ORDER)


class UnknownKeyError(ValueError):
    """Raised when a captured/parsed key is not in ``pyautogui.KEYBOARD_KEYS``."""


def parse_combo(combo: str) -> list[str]:
    """Tokenise + alias a human combo string into pyautogui key names.

    ``parse_combo("Ctrl + L")`` → ``["ctrl", "l"]``. No validation against
    ``KEYBOARD_KEYS`` happens here — callers may want to report an unknown
    key differently from a destructive-chord block.
    """
    raw_tokens = [t for t in PRESS_SPLIT_RE.split(combo.strip()) if t]
    return [PRESS_ALIASES.get(t.lower(), t.lower()) for t in raw_tokens]


def is_known_key(key: str) -> bool:
    """``True`` if *key* is in pyautogui's keyname catalog."""
    valid = getattr(pyautogui, "KEYBOARD_KEYS", None)
    if valid is None:
        return True
    return key in valid


def _pynput_modifier_name(key: object) -> str | None:
    """Map a pynput ``Key.*`` modifier enum value to our canonical name.

    Returns ``None`` if *key* is not a modifier we track.
    """
    name = getattr(key, "name", None)
    if name is None:
        return None
    if name in ("ctrl", "ctrl_l", "ctrl_r"):
        return "ctrl"
    if name in ("alt", "alt_l", "alt_r", "alt_gr"):
        return "alt"
    if name in ("shift", "shift_l", "shift_r"):
        return "shift"
    if name in ("cmd", "cmd_l", "cmd_r", "win"):
        return "win"
    return None


def is_pynput_modifier(key: object) -> bool:
    """``True`` if *key* is one of the four modifier classes we track."""
    return _pynput_modifier_name(key) is not None


_PYNPUT_NAME_OVERRIDES: dict[str, str] = {
    "esc": "esc",
    "escape": "esc",
    "page_up": "pageup",
    "page_down": "pagedown",
    "caps_lock": "capslock",
    "num_lock": "numlock",
    "scroll_lock": "scrolllock",
    "print_screen": "printscreen",
    "media_play_pause": "playpause",
    "media_volume_up": "volumeup",
    "media_volume_down": "volumedown",
    "media_volume_mute": "volumemute",
    "media_next": "nexttrack",
    "media_previous": "prevtrack",
}


def pynput_key_to_keyname(key: object) -> str | None:
    """Turn a pynput ``Key`` / ``KeyCode`` into a pyautogui keyname.

    Letters and digits come from ``KeyCode.char``; named keys from
    ``Key.name``. Returns ``None`` when the key cannot be represented
    (dead keys, IME composition, vendor-specific scancodes).
    """
    char = getattr(key, "char", None)
    if char:
        return char.lower()
    name = getattr(key, "name", None)
    if name is None:
        return None
    return _PYNPUT_NAME_OVERRIDES.get(name, name)


def format_combo(modifiers: set[str], main: str | None) -> str:
    """Build a canonical ``ctrl+alt+shift+win+<key>`` string.

    *modifiers* is a set of canonical names from ``_pynput_modifier_name``;
    *main* is the captured non-modifier keyname (or ``None`` for a
    modifier-only chord). Modifier order is fixed.

    Raises
    ------
    UnknownKeyError
        If any token is not in ``pyautogui.KEYBOARD_KEYS``.
    """
    parts: list[str] = [m for m in _MODIFIER_ORDER if m in modifiers]
    if main is not None:
        parts.append(main)
    if not parts:
        raise UnknownKeyError("empty combo")
    unknown = [p for p in parts if not is_known_key(p)]
    if unknown:
        raise UnknownKeyError(f"unknown key(s): {unknown}")
    return "+".join(parts)


__all__ = [
    "PRESS_ALIASES",
    "PRESS_SPLIT_RE",
    "UnknownKeyError",
    "parse_combo",
    "is_known_key",
    "is_pynput_modifier",
    "pynput_key_to_keyname",
    "format_combo",
]
