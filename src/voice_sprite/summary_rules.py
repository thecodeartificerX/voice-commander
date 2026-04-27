"""Rule table + chain detectors for one-line HUD summaries.

Covers the nine-verb primitive catalog (ADR 0043) plus the bonus
``scroll`` verb and the sentinel ``no_match``. Chain detectors
recognise canonical multi-step patterns and replace the last-step-wins
default with a more natural phrase.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from voice_commander.plan import PlanOutcome, ToolCall


def _typed_snip(text: str, cap: int = 30) -> str:
    text = text.replace('"', "'")
    if len(text) <= cap:
        return text
    return text[: cap - 1] + "\u2026"


RULES: dict[str, Callable[[dict[str, Any], PlanOutcome], str]] = {
    # Nine-verb primitives (ADR 0043) + scroll + sentinel
    "focus": lambda kw, _o: f"focused {kw.get('target', 'window')}",
    "minimize": lambda _kw, _o: "minimized window",
    "maximize": lambda _kw, _o: "maximized window",
    "close": lambda _kw, _o: "closed tab",
    "close_window": lambda _kw, _o: "closed window",
    "open": lambda kw, _o: f"opened {kw.get('target', 'app')}",
    "type": lambda kw, _o: f'typed "{_typed_snip(str(kw.get("text", "")))}"',
    "press": lambda kw, _o: f"pressed {kw.get('combo', '')}".rstrip(),
    "wait": lambda kw, _o: f"waited {kw.get('ms', 0)}ms",
    "click": lambda _kw, _o: "clicked",
    "scroll": lambda kw, _o: f"scrolled {kw.get('direction', '')}".rstrip(),
    "no_match": lambda _kw, _o: "no match",
    # Common user-defined commands (commands.json) — keep prose tight
    "new_tab": lambda _kw, _o: "opened new tab",
    "next_tab": lambda _kw, _o: "next tab",
    "previous_tab": lambda _kw, _o: "previous tab",
    "reopen_tab": lambda _kw, _o: "reopened tab",
    "new_window": lambda _kw, _o: "opened new window",
    "last_window": lambda _kw, _o: "switched to last window",
    "copy": lambda _kw, _o: "copied",
    "paste": lambda _kw, _o: "pasted",
    "cut": lambda _kw, _o: "cut",
    "undo": lambda _kw, _o: "undone",
    "redo": lambda _kw, _o: "redone",
    "save": lambda _kw, _o: "saved",
    "find": lambda _kw, _o: "find",
    "select_all": lambda _kw, _o: "selected all",
    "refresh": lambda _kw, _o: "refreshed",
    "address_bar": lambda _kw, _o: "focused address bar",
    "lock_screen": lambda _kw, _o: "locked screen",
    "screenshot": lambda _kw, _o: "screenshot",
    "search_web": lambda kw, _o: f'searched for "{_typed_snip(str(kw.get("query", "")), cap=40)}"',
    # Perception primitives — usually feed branches, rarely terminal,
    # but cover them so HUD reads cleanly when they ARE the last step.
    "read_clipboard": lambda _kw, _o: "read clipboard",
    "get_active_window_title": lambda _kw, _o: "got active window",
    "get_cursor_pos": lambda _kw, _o: "got cursor position",
    "ocr_region": lambda _kw, _o: "ran OCR",
}


def detect_search_chain(steps: tuple[ToolCall, ...]) -> str | None:
    """Detect canonical ``search <query> in <browser>`` chain.

    Pattern: focus(target=browser) -> press(ctrl+t) -> press(ctrl+l) ->
    type(text=query) -> press(enter).
    """
    if len(steps) < 5:
        return None
    s = steps
    if s[0].name != "focus":
        return None
    if s[1].name != "press" or s[1].kwargs.get("combo") != "ctrl+t":
        return None
    if s[2].name != "press" or s[2].kwargs.get("combo") != "ctrl+l":
        return None
    if s[3].name != "type":
        return None
    if s[4].name != "press" or s[4].kwargs.get("combo") != "enter":
        return None
    browser = str(s[0].kwargs.get("target", "browser"))
    query = _typed_snip(str(s[3].kwargs.get("text", "")), cap=40)
    return f'searched {browser} for "{query}"'


CHAIN_DETECTORS: list[Callable[[tuple[ToolCall, ...]], str | None]] = [
    detect_search_chain,
]
