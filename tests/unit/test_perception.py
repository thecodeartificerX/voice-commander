"""Unit tests for the four perception tools in voice_commander.tools.perception.

All win32 and psutil APIs are mocked via patch.dict('sys.modules', ...) because
the imports are lazy (inside each function body) and CI environments lack pywin32.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from voice_commander.tools.perception import (
    get_clipboard,
    get_focused_window,
    list_processes,
    list_windows,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_win32gui(
    *,
    fg_hwnd: int = 12345,
    title: str = "Notepad",
    visible: bool = True,
    iconic: bool = False,
) -> MagicMock:
    """Return a minimal win32gui mock suitable for most tests."""
    m = MagicMock()
    m.GetForegroundWindow.return_value = fg_hwnd
    m.GetWindowText.return_value = title
    m.IsWindowVisible.return_value = visible
    m.IsIconic.return_value = iconic
    return m


def _make_win32process(*, pid: int = 678) -> MagicMock:
    m = MagicMock()
    m.GetWindowThreadProcessId.return_value = (1, pid)
    return m


def _make_psutil(*, process_name: str = "notepad.exe") -> MagicMock:
    m = MagicMock()
    m.Process.return_value.name.return_value = process_name
    return m


def _make_win32clipboard(
    *,
    has_text: bool = True,
    text: str = "hello clipboard",
) -> tuple[MagicMock, MagicMock]:
    """Return (mock_win32clipboard, mock_win32con)."""
    clipboard = MagicMock()
    con = MagicMock()
    con.CF_UNICODETEXT = 13
    clipboard.IsClipboardFormatAvailable.return_value = has_text
    clipboard.GetClipboardData.return_value = text if has_text else None
    return clipboard, con


# ---------------------------------------------------------------------------
# get_focused_window
# ---------------------------------------------------------------------------


def test_get_focused_window_returns_hwnd_title_process():
    """Returns dict with correct hwnd, title, and process name from win32 mocks."""
    mock_win32gui = _make_win32gui(fg_hwnd=12345, title="Notepad")
    mock_win32process = _make_win32process(pid=678)
    mock_psutil = _make_psutil(process_name="notepad.exe")

    with patch.dict(
        sys.modules,
        {
            "win32gui": mock_win32gui,
            "win32process": mock_win32process,
            "psutil": mock_psutil,
        },
    ):
        result = get_focused_window()

    assert result["hwnd"] == 12345
    assert result["title"] == "Notepad"
    assert result["process"] == "notepad.exe"
    mock_win32gui.GetForegroundWindow.assert_called_once()
    mock_win32gui.GetWindowText.assert_called_once_with(12345)
    mock_win32process.GetWindowThreadProcessId.assert_called_once_with(12345)
    mock_psutil.Process.assert_called_once_with(678)


def test_get_focused_window_no_pywin32_returns_empty():
    """Returns empty result dict when win32gui is not importable (no pywin32)."""
    with patch.dict(sys.modules, {"win32gui": None, "win32process": None}):
        result = get_focused_window()

    assert result == {"hwnd": 0, "title": "", "process": ""}


def test_get_focused_window_no_foreground():
    """Returns empty result dict when GetForegroundWindow returns 0 (no focused window)."""
    mock_win32gui = _make_win32gui(fg_hwnd=0)
    mock_win32process = _make_win32process()

    with patch.dict(
        sys.modules,
        {"win32gui": mock_win32gui, "win32process": mock_win32process},
    ):
        result = get_focused_window()

    assert result == {"hwnd": 0, "title": "", "process": ""}
    mock_win32gui.GetWindowText.assert_not_called()


# ---------------------------------------------------------------------------
# list_windows
# ---------------------------------------------------------------------------


def _build_enum_windows_side_effect(windows: list[tuple[int, bool, bool, str]]):
    """Build a side_effect for EnumWindows given a list of (hwnd, visible, iconic, title)."""

    def _side_effect(callback, param):
        for hwnd, _visible, _iconic, _title in windows:
            # callback is the _enum closure defined inside list_windows; we call it directly
            callback(hwnd, param)

    return _side_effect


def test_list_windows_filters_invisible_and_minimized():
    """Only visible, non-minimized, titled windows are included in results."""
    # Windows: (hwnd, visible, iconic, title)
    windows = [
        (1, True, False, "Notepad"),  # included
        (2, False, False, "Hidden"),  # excluded — invisible
        (3, True, True, "Minimized"),  # excluded — iconic
        (4, True, False, ""),  # excluded — empty title
        (5, True, False, "Chrome"),  # included
    ]

    mock_win32gui = MagicMock()
    mock_win32process = MagicMock()
    mock_psutil = MagicMock()
    mock_psutil.Process.return_value.name.return_value = "proc.exe"

    hwnd_props = {hwnd: (vis, ico, title) for hwnd, vis, ico, title in windows}

    def _fake_visible(hwnd):
        return hwnd_props[hwnd][0]

    def _fake_iconic(hwnd):
        return hwnd_props[hwnd][1]

    def _fake_title(hwnd):
        return hwnd_props[hwnd][2]

    mock_win32gui.IsWindowVisible.side_effect = _fake_visible
    mock_win32gui.IsIconic.side_effect = _fake_iconic
    mock_win32gui.GetWindowText.side_effect = _fake_title
    mock_win32process.GetWindowThreadProcessId.return_value = (1, 100)

    def _fake_enum(callback, param):
        for hwnd, _vis, _ico, _title in windows:
            callback(hwnd, param)

    mock_win32gui.EnumWindows.side_effect = _fake_enum

    with patch.dict(
        sys.modules,
        {"win32gui": mock_win32gui, "win32process": mock_win32process, "psutil": mock_psutil},
    ):
        results = list_windows()

    titles = [w["title"] for w in results]
    assert "Notepad" in titles
    assert "Chrome" in titles
    assert "Hidden" not in titles
    assert "Minimized" not in titles
    assert "" not in titles
    assert len(results) == 2


def test_list_windows_caps_at_20():
    """list_windows caps output at 20 entries even when more windows exist."""
    # Create 25 visible, titled, non-minimized windows
    windows = [(i, True, False, f"Window {i:02d}") for i in range(1, 26)]

    mock_win32gui = MagicMock()
    mock_win32process = MagicMock()
    mock_psutil = MagicMock()
    mock_psutil.Process.return_value.name.return_value = "app.exe"

    hwnd_props = {hwnd: (vis, ico, title) for hwnd, vis, ico, title in windows}
    mock_win32gui.IsWindowVisible.side_effect = lambda h: hwnd_props[h][0]
    mock_win32gui.IsIconic.side_effect = lambda h: hwnd_props[h][1]
    mock_win32gui.GetWindowText.side_effect = lambda h: hwnd_props[h][2]
    mock_win32process.GetWindowThreadProcessId.return_value = (1, 50)

    def _fake_enum(callback, param):
        for hwnd, *_ in windows:
            callback(hwnd, param)

    mock_win32gui.EnumWindows.side_effect = _fake_enum

    with patch.dict(
        sys.modules,
        {"win32gui": mock_win32gui, "win32process": mock_win32process, "psutil": mock_psutil},
    ):
        results = list_windows()

    assert len(results) == 20


def test_list_windows_sorted_by_title():
    """Results are sorted alphabetically (case-insensitive) by title."""
    windows = [
        (1, True, False, "Zebra App"),
        (2, True, False, "Alpha Tool"),
        (3, True, False, "mango viewer"),
    ]

    mock_win32gui = MagicMock()
    mock_win32process = MagicMock()
    mock_psutil = MagicMock()
    mock_psutil.Process.return_value.name.return_value = "app.exe"

    hwnd_props = {hwnd: title for hwnd, _v, _i, title in windows}
    mock_win32gui.IsWindowVisible.return_value = True
    mock_win32gui.IsIconic.return_value = False
    mock_win32gui.GetWindowText.side_effect = lambda h: hwnd_props[h]
    mock_win32process.GetWindowThreadProcessId.return_value = (1, 10)

    def _fake_enum(callback, param):
        for hwnd, *_ in windows:
            callback(hwnd, param)

    mock_win32gui.EnumWindows.side_effect = _fake_enum

    with patch.dict(
        sys.modules,
        {"win32gui": mock_win32gui, "win32process": mock_win32process, "psutil": mock_psutil},
    ):
        results = list_windows()

    titles = [str(w["title"]) for w in results]
    assert titles == sorted(titles, key=str.lower), f"Expected sorted titles, got: {titles}"


def test_list_windows_no_pywin32_returns_empty():
    """Returns empty list when win32gui/win32process are not importable."""
    with patch.dict(sys.modules, {"win32gui": None, "win32process": None}):
        results = list_windows()

    assert results == []


# ---------------------------------------------------------------------------
# get_clipboard
# ---------------------------------------------------------------------------


def test_get_clipboard_returns_text():
    """Returns {'text': <string>} when clipboard contains unicode text."""
    clipboard, con = _make_win32clipboard(text="copied text")

    with patch.dict(sys.modules, {"win32clipboard": clipboard, "win32con": con}):
        result = get_clipboard()

    assert result == {"text": "copied text"}
    clipboard.OpenClipboard.assert_called_once()
    clipboard.CloseClipboard.assert_called_once()


def test_get_clipboard_caps_at_500_chars():
    """Clipboard text longer than 500 chars is truncated to 500."""
    long_text = "x" * 1000
    clipboard, con = _make_win32clipboard(text=long_text)

    with patch.dict(sys.modules, {"win32clipboard": clipboard, "win32con": con}):
        result = get_clipboard()

    assert result["text"] is not None
    assert len(result["text"]) == 500
    assert result["text"] == "x" * 500


def test_get_clipboard_no_text_format_returns_none():
    """Returns {'text': None} when CF_UNICODETEXT is not available on the clipboard."""
    clipboard, con = _make_win32clipboard(has_text=False)
    clipboard.IsClipboardFormatAvailable.return_value = False

    with patch.dict(sys.modules, {"win32clipboard": clipboard, "win32con": con}):
        result = get_clipboard()

    assert result == {"text": None}
    clipboard.GetClipboardData.assert_not_called()
    # CloseClipboard must still be called (finally block)
    clipboard.CloseClipboard.assert_called_once()


def test_get_clipboard_no_pywin32_returns_none():
    """Returns {'text': None} when win32clipboard is not importable."""
    with patch.dict(sys.modules, {"win32clipboard": None, "win32con": None}):
        result = get_clipboard()

    assert result == {"text": None}


def test_get_clipboard_open_failure_returns_none():
    """Returns {'text': None} when OpenClipboard raises (e.g. clipboard locked)."""
    clipboard, con = _make_win32clipboard()
    clipboard.OpenClipboard.side_effect = OSError("clipboard locked")

    with patch.dict(sys.modules, {"win32clipboard": clipboard, "win32con": con}):
        result = get_clipboard()

    assert result == {"text": None}
    clipboard.GetClipboardData.assert_not_called()


# ---------------------------------------------------------------------------
# list_processes
# ---------------------------------------------------------------------------


def test_list_processes_returns_windowed_processes():
    """Returns processes cross-referenced with visible window PIDs via win32process."""
    # Two visible windows owned by PIDs 101 and 202.
    mock_win32gui = MagicMock()
    mock_win32process = MagicMock()
    mock_psutil = MagicMock()

    # list_windows() will enumerate these two windows.
    visible_windows_data = [
        (101, True, False, "Notepad"),
        (202, True, False, "Chrome"),
    ]
    hwnd_props = {hwnd: title for hwnd, _v, _i, title in visible_windows_data}
    hwnd_pid_map = {101: 1001, 202: 2002}

    mock_win32gui.IsWindowVisible.return_value = True
    mock_win32gui.IsIconic.return_value = False
    mock_win32gui.GetWindowText.side_effect = lambda h: hwnd_props.get(h, "")

    def _fake_enum(callback, param):
        for hwnd, *_ in visible_windows_data:
            callback(hwnd, param)

    mock_win32gui.EnumWindows.side_effect = _fake_enum

    def _fake_get_thread_pid(hwnd):
        return (1, hwnd_pid_map.get(hwnd, 0))

    mock_win32process.GetWindowThreadProcessId.side_effect = _fake_get_thread_pid

    # psutil yields three processes; only PIDs 1001 and 2002 are in visible_pids.
    proc_notepad = MagicMock()
    proc_notepad.info = {"pid": 1001, "name": "notepad.exe"}
    proc_chrome = MagicMock()
    proc_chrome.info = {"pid": 2002, "name": "chrome.exe"}
    proc_bg = MagicMock()
    proc_bg.info = {"pid": 9999, "name": "background.exe"}  # no window → excluded

    mock_psutil.process_iter.return_value = [proc_notepad, proc_chrome, proc_bg]

    with patch.dict(
        sys.modules,
        {"win32gui": mock_win32gui, "win32process": mock_win32process, "psutil": mock_psutil},
    ):
        results = list_processes()

    names = [p["name"] for p in results]
    assert "notepad.exe" in names
    assert "chrome.exe" in names
    assert "background.exe" not in names


def test_list_processes_no_psutil_returns_empty():
    """Returns empty list when psutil is not importable."""
    with patch.dict(sys.modules, {"psutil": None}):
        results = list_processes()

    assert results == []


def test_list_processes_sorted_by_name():
    """Results are sorted alphabetically (case-insensitive) by process name."""
    mock_win32gui = MagicMock()
    mock_win32process = MagicMock()
    mock_psutil = MagicMock()

    visible_windows_data = [
        (10, True, False, "Zebra"),
        (20, True, False, "Alpha"),
        (30, True, False, "mango"),
    ]
    hwnd_props = {hwnd: title for hwnd, _v, _i, title in visible_windows_data}
    hwnd_pid_map = {10: 1010, 20: 2020, 30: 3030}

    mock_win32gui.IsWindowVisible.return_value = True
    mock_win32gui.IsIconic.return_value = False
    mock_win32gui.GetWindowText.side_effect = lambda h: hwnd_props.get(h, "")

    def _fake_enum(callback, param):
        for hwnd, *_ in visible_windows_data:
            callback(hwnd, param)

    mock_win32gui.EnumWindows.side_effect = _fake_enum
    mock_win32process.GetWindowThreadProcessId.side_effect = lambda h: (1, hwnd_pid_map.get(h, 0))

    proc_z = MagicMock()
    proc_z.info = {"pid": 1010, "name": "zebra.exe"}
    proc_a = MagicMock()
    proc_a.info = {"pid": 2020, "name": "alpha.exe"}
    proc_m = MagicMock()
    proc_m.info = {"pid": 3030, "name": "mango.exe"}

    mock_psutil.process_iter.return_value = [proc_z, proc_a, proc_m]

    with patch.dict(
        sys.modules,
        {"win32gui": mock_win32gui, "win32process": mock_win32process, "psutil": mock_psutil},
    ):
        results = list_processes()

    names = [str(p["name"]) for p in results]
    assert names == sorted(names, key=str.lower), f"Expected sorted names, got: {names}"


def test_list_processes_caps_at_20():
    """list_processes caps output at 20 entries."""
    mock_win32gui = MagicMock()
    mock_win32process = MagicMock()
    mock_psutil = MagicMock()

    # 25 visible windows each owned by a unique PID
    n = 25
    visible_windows_data = [(i, True, False, f"Win{i:02d}") for i in range(1, n + 1)]
    hwnd_props = {hwnd: title for hwnd, _v, _i, title in visible_windows_data}
    # PID = hwnd * 10 to keep them distinct
    hwnd_pid_map = {hwnd: hwnd * 10 for hwnd, *_ in visible_windows_data}

    mock_win32gui.IsWindowVisible.return_value = True
    mock_win32gui.IsIconic.return_value = False
    mock_win32gui.GetWindowText.side_effect = lambda h: hwnd_props.get(h, "")

    def _fake_enum(callback, param):
        for hwnd, *_ in visible_windows_data:
            callback(hwnd, param)

    mock_win32gui.EnumWindows.side_effect = _fake_enum
    mock_win32process.GetWindowThreadProcessId.side_effect = lambda h: (1, hwnd_pid_map.get(h, 0))

    procs = []
    for hwnd, _v, _i, _t in visible_windows_data:
        p = MagicMock()
        p.info = {"pid": hwnd * 10, "name": f"app{hwnd:02d}.exe"}
        procs.append(p)

    mock_psutil.process_iter.return_value = procs

    with patch.dict(
        sys.modules,
        {"win32gui": mock_win32gui, "win32process": mock_win32process, "psutil": mock_psutil},
    ):
        results = list_processes()

    assert len(results) == 20


# ---------------------------------------------------------------------------
# read_clipboard
# ---------------------------------------------------------------------------


def test_read_clipboard_returns_unicode_text():
    import sys
    import types

    fake_win32clipboard = types.SimpleNamespace(
        OpenClipboard=lambda: None,
        CloseClipboard=lambda: None,
        IsClipboardFormatAvailable=lambda fmt: True,
        GetClipboardData=lambda fmt: "hello",
    )
    fake_win32con = types.SimpleNamespace(CF_UNICODETEXT=13)

    from voice_commander.tools.perception import read_clipboard
    mods = {"win32clipboard": fake_win32clipboard, "win32con": fake_win32con}
    with patch.dict(sys.modules, mods):
        assert read_clipboard() == "hello"


def test_read_clipboard_returns_empty_when_no_text():
    import sys
    import types

    fake_win32clipboard = types.SimpleNamespace(
        OpenClipboard=lambda: None,
        CloseClipboard=lambda: None,
        IsClipboardFormatAvailable=lambda fmt: False,
        GetClipboardData=lambda fmt: None,
    )
    fake_win32con = types.SimpleNamespace(CF_UNICODETEXT=13)

    from voice_commander.tools.perception import read_clipboard
    mods = {"win32clipboard": fake_win32clipboard, "win32con": fake_win32con}
    with patch.dict(sys.modules, mods):
        assert read_clipboard() == ""


# ---------------------------------------------------------------------------
# get_active_window_title
# ---------------------------------------------------------------------------


def test_get_active_window_title_returns_text():
    import sys
    import types
    fake = types.SimpleNamespace(
        GetForegroundWindow=lambda: 0x1234,
        GetWindowText=lambda hwnd: "Comet — example.com",
    )

    from voice_commander.tools.perception import get_active_window_title
    with patch.dict(sys.modules, {"win32gui": fake}):
        assert get_active_window_title() == "Comet — example.com"


def test_get_active_window_title_returns_empty_on_no_foreground():
    import sys
    import types
    fake = types.SimpleNamespace(
        GetForegroundWindow=lambda: 0,
        GetWindowText=lambda hwnd: "",
    )

    from voice_commander.tools.perception import get_active_window_title
    with patch.dict(sys.modules, {"win32gui": fake}):
        assert get_active_window_title() == ""


# ---------------------------------------------------------------------------
# get_cursor_pos
# ---------------------------------------------------------------------------


def test_get_cursor_pos_returns_tuple():
    import sys
    import types
    fake_win32api = types.SimpleNamespace(GetCursorPos=lambda: (100, 200))

    from voice_commander.tools.perception import get_cursor_pos
    with patch.dict(sys.modules, {"win32api": fake_win32api}):
        result = get_cursor_pos()
    assert result == (100, 200)


def test_get_cursor_pos_fallback_on_import_error():
    """When win32api is not importable, returns (0, 0)."""
    import sys

    from voice_commander.tools.perception import get_cursor_pos
    with patch.dict(sys.modules, {"win32api": None}):
        result = get_cursor_pos()
    assert result == (0, 0)
