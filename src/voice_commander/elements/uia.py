"""Isolates the optional ``uiautomation`` dependency and UIA constants.

Keeping the import behind ``uia_available()`` means the rest of the package
imports cleanly on machines without the library, and the daemon can warn
once at startup instead of crashing.
"""

from __future__ import annotations

# UIA ControlTypeName values for controls a user can click. Matched against
# ``control.ControlTypeName`` during the tree walk in scanner.scan_window.
INTERACTIVE_CONTROL_TYPES: frozenset[str] = frozenset(
    {
        "ButtonControl",
        "HyperlinkControl",
        "MenuItemControl",
        "ListItemControl",
        "TabItemControl",
        "CheckBoxControl",
        "RadioButtonControl",
        "ComboBoxControl",
        "EditControl",
        "SplitButtonControl",
        "TreeItemControl",
    }
)


def uia_available() -> bool:
    """Return True if the ``uiautomation`` library can be imported."""
    try:
        import uiautomation  # noqa: F401
    except Exception:
        return False
    return True
