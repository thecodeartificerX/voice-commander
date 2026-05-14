"""UI Automation tab enumeration + activation for Chromium-derived browsers.

Saying ``tabs.`` opens a picker of the foreground browser's tab titles;
saying the number activates that tab. Both operations walk the UIA tree
from the browser HWND down to its ``TabControl`` and invoke the chosen
``TabItem``.

Scoped to Chromium-derived browsers (Chrome, Edge, Brave, Comet, ...)
because their UIA shape is identical: ``ClassName == "Chrome_WidgetWin_1"``
plus a single tab-strip ``TabControl`` whose direct children are the page
``TabItem``s. Process name is the cheapest filter — Electron apps like
VS Code share the Chromium class but expose unrelated tab controls
(activity bar, terminal panel, ...) we must not confuse for browser
tabs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# Lowercased process-name allowlist for Chromium-derived browsers we
# recognise as having a single, well-defined page-tab strip. Extend as new
# Chromium forks become relevant. Electron apps (VS Code, Slack, Discord)
# are deliberately excluded — they share the Chromium ClassName but their
# UIA tree contains many unrelated TabControls.
CHROMIUM_BROWSER_PROCESSES: frozenset[str] = frozenset(
    {
        "chrome.exe",
        "msedge.exe",
        "brave.exe",
        "comet.exe",
        "vivaldi.exe",
        "opera.exe",
        "browser.exe",  # Yandex
        "chromium.exe",
        "arc.exe",
    }
)


@dataclass(frozen=True)
class TabInfo:
    """One enumerated browser tab.

    ``index`` is the tab's position in the strip at enumeration time;
    ``title`` is its page title (used as the fallback match when the
    strip has shifted between enumeration and activation).
    """

    index: int
    title: str


def _process_name_for_hwnd(hwnd: int) -> str:
    """Return the lowercased basename of *hwnd*'s owning process, or ``""``."""
    try:
        import psutil  # type: ignore
        import win32process  # type: ignore
    except ImportError:
        return ""
    try:
        _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
        if not pid:
            return ""
        return psutil.Process(pid).name().lower()
    except Exception:
        return ""


def is_chromium_browser(hwnd: int) -> bool:
    """Cheap pre-flight: True when *hwnd*'s process is in the allowlist."""
    return _process_name_for_hwnd(hwnd) in CHROMIUM_BROWSER_PROCESSES


def _collect_tab_items(node, out, depth=0, max_depth=40):  # type: ignore[no-untyped-def]
    """DFS collect every ``TabItemControl`` under *node*.

    Used to enumerate page tabs without assuming any specific parent
    shape — Chromium currently nests ``TabItemControl`` two levels under
    the page ``TabControl`` (``TabControl > Pane > Pane > TabItem``),
    but the depth has historically wandered as the tab strip was
    refactored. Depth is bounded so a pathological tree can't hang us.
    """
    if depth > max_depth:
        return
    try:
        if node.ControlTypeName == "TabItemControl":
            out.append(node)
            # TabItems don't nest under each other in Chromium — stop
            # descending so a malformed UIA tree can't double-count.
            return
        for child in node.GetChildren():
            _collect_tab_items(child, out, depth + 1, max_depth)
    except Exception:
        return


def _find_tab_strip(root):  # type: ignore[no-untyped-def]
    """Breadth-first search for the page-tab ``TabControl`` under *root*.

    Returns the first ``TabControl`` whose subtree contains at least one
    ``TabItemControl``. Chromium nests page TabItems as grandchildren
    of the TabControl (``TabControl > Pane > Pane > TabItem``), so a
    direct-child check is too strict; descendant containment is the
    actual invariant.
    """
    queue = [root]
    visited = 0
    while queue and visited < 1500:
        node = queue.pop(0)
        visited += 1
        try:
            if node.ControlTypeName == "TabControl":
                found: list = []
                _collect_tab_items(node, found)
                if found:
                    return node
            for child in node.GetChildren():
                queue.append(child)
        except Exception:
            continue
    return None


def list_chromium_tabs(hwnd: int) -> list[TabInfo]:
    """Return the page-tab list for the Chromium browser at *hwnd*.

    Empty list when:
      * *hwnd* is not owned by a recognised Chromium browser process;
      * the UIA tree has no recognisable tab strip (rare — e.g. PWA windows
        in app-mode without a tabstrip);
      * UIA itself fails (COM error, hung renderer).

    Returned ``TabInfo.title`` falls back to a placeholder
    (``"(untitled tab N)"``) so the picker always renders something
    selectable — a blank card is worse than a placeholder.
    """
    if not is_chromium_browser(hwnd):
        return []

    try:
        import uiautomation as ua  # type: ignore
    except ImportError:
        logger.warning("uiautomation not installed; tabs picker disabled")
        return []

    try:
        root = ua.ControlFromHandle(int(hwnd))
    except Exception:
        logger.exception("ControlFromHandle failed for hwnd=%d", hwnd)
        return []
    if root is None:
        return []

    strip = _find_tab_strip(root)
    if strip is None:
        logger.info("no tab strip found under hwnd=%d", hwnd)
        return []

    raw: list = []
    _collect_tab_items(strip, raw)
    tabs: list[TabInfo] = []
    for i, item in enumerate(raw):
        try:
            name = item.Name or f"(untitled tab {i + 1})"
        except Exception:
            name = f"(untitled tab {i + 1})"
        tabs.append(TabInfo(index=i, title=name))
    logger.info("enumerated %d tabs for hwnd=%d", len(tabs), hwnd)
    return tabs


def invoke_chromium_tab(hwnd: int, index: int, title_hint: str = "") -> bool:
    """Activate the *index*-th tab in the Chromium browser at *hwnd*.

    The strip can shift between picker open and selection (user opened a
    new tab, closed one, dragged one out). When *title_hint* is supplied
    and the index lookup either falls out of bounds or hits a tab whose
    title no longer matches, we try a title-equality scan before giving
    up. That keeps the picker correct under reasonable concurrent edits
    while staying O(N) and re-enumeration-free.
    """
    try:
        import uiautomation as ua  # type: ignore
    except ImportError:
        return False

    try:
        root = ua.ControlFromHandle(int(hwnd))
    except Exception:
        logger.exception("ControlFromHandle failed for hwnd=%d", hwnd)
        return False
    if root is None:
        return False

    strip = _find_tab_strip(root)
    if strip is None:
        return False

    items: list = []
    _collect_tab_items(strip, items)
    target = None
    if 0 <= index < len(items):
        candidate = items[index]
        if not title_hint or (candidate.Name or "") == title_hint:
            target = candidate
    if target is None and title_hint:
        for it in items:
            if (it.Name or "") == title_hint:
                target = it
                break
    if target is None:
        logger.warning(
            "tab invoke: no match for index=%d hint=%r in %d items",
            index,
            title_hint,
            len(items),
        )
        return False

    # TabItem in Chromium supports SelectionItemPattern (preferred) and
    # LegacyIAccessible.DoDefaultAction. Try Select first because it sets
    # both selection and activation without a synthetic click. Fall back
    # to LegacyIAccessible for forks that only wire that.
    try:
        sel = target.GetSelectionItemPattern()
        if sel is not None:
            sel.Select()
            return True
    except Exception:
        logger.exception("SelectionItemPattern.Select failed")

    try:
        legacy = target.GetLegacyIAccessiblePattern()
        if legacy is not None:
            legacy.DoDefaultAction()
            return True
    except Exception:
        logger.exception("LegacyIAccessible.DoDefaultAction failed")

    return False
