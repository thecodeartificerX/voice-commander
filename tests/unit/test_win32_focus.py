"""Unit tests for voice_commander.tools._win32.focus_window_by_exe.

All Win32 / ctypes / psutil surface is mocked — no real system calls are made.

Test matrix
-----------
- happy_path_attach: different foreground/target threads → AttachThreadInput path runs,
  SetForegroundWindow succeeds, GetForegroundWindow returns target → True, no raise.
- happy_path_same_thread: foreground thread == target thread → AttachThreadInput skipped,
  SetForegroundWindow succeeds → True, no raise.
- minimized_window: IsIconic → True → ShowWindow(SW_RESTORE) called before focus.
- lockout_failure: SetForegroundWindow raises pywintypes.error → verification polls return
  wrong hwnd → FocusWindowError raised.
- attach_detach_on_exception: AttachThreadInput(TRUE) is always paired with
  AttachThreadInput(FALSE) even when SetForegroundWindow raises.
- target_not_found_no_pids: psutil finds no matching process → FocusWindowError, Popen called.
- target_not_found_no_window: process exists but EnumWindows yields no visible window →
  FocusWindowError, no Popen (process is running, just window-less).
- import_error: pywin32 unavailable → FocusWindowError, Popen called.
- verify_foreground_polls: GetForegroundWindow returns wrong hwnd on first two polls then
  correct hwnd → returns True (within poll budget).
- verify_foreground_all_wrong: all polls return wrong hwnd → FocusWindowError.
"""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from voice_commander.tools._win32 import FocusWindowError, focus_window_by_exe

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TARGET_HWND = 1001
_FG_HWND = 2002
_FG_TID = 100
_TARGET_TID = 200
_TARGET_PID = 9999


def _make_psutil_proc(name: str, pid: int) -> MagicMock:
    p = MagicMock()
    p.pid = pid
    p.info = {"name": name}
    return p


def _build_win32_mocks(
    *,
    fg_hwnd: int = _FG_HWND,
    fg_tid: int = _FG_TID,
    target_hwnd: int = _TARGET_HWND,
    target_pid: int = _TARGET_PID,
    target_tid: int = _TARGET_TID,
    is_iconic: bool = False,
    sfg_side_effect: Exception | None = None,
    get_fg_sequence: list[int] | None = None,
) -> dict:
    """Return a dict of mock objects matching the win32 / psutil surface."""
    win32gui = MagicMock()
    win32process = MagicMock()
    win32con = MagicMock()
    psutil_mod = MagicMock()

    win32con.SW_RESTORE = 9

    # IsWindowVisible always True for our target hwnd
    win32gui.IsWindowVisible.return_value = True
    # GetWindowThreadProcessId: (tid, pid)
    win32process.GetWindowThreadProcessId.side_effect = lambda hwnd, *_: (
        (fg_tid, 0) if hwnd == fg_hwnd else (target_tid, target_pid)
    )
    # IsIconic
    win32gui.IsIconic.return_value = is_iconic
    # GetForegroundWindow — first call returns fg_hwnd; subsequent calls for
    # verification use get_fg_sequence (defaults to returning target_hwnd each time).
    _verify_seq = get_fg_sequence if get_fg_sequence is not None else [target_hwnd] * 5
    _gfw_calls: list[int] = []

    def _gfw():
        # First call is always during the "get fg thread" phase, return fg_hwnd.
        # Subsequent calls are verification polls.
        if not _gfw_calls:
            _gfw_calls.append(1)
            return fg_hwnd
        idx = len(_gfw_calls) - 1
        _gfw_calls.append(1)
        return _verify_seq[idx] if idx < len(_verify_seq) else _verify_seq[-1]

    win32gui.GetForegroundWindow.side_effect = _gfw

    # EnumWindows — call the callback once with target_hwnd
    def _enum(callback, extra):
        callback(target_hwnd, extra)

    win32gui.EnumWindows.side_effect = _enum

    # SetForegroundWindow
    if sfg_side_effect is not None:
        win32gui.SetForegroundWindow.side_effect = sfg_side_effect
    else:
        win32gui.SetForegroundWindow.return_value = True

    # psutil
    psutil_mod.process_iter.return_value = [_make_psutil_proc("target.exe", target_pid)]

    return {
        "win32gui": win32gui,
        "win32process": win32process,
        "win32con": win32con,
        "psutil": psutil_mod,
    }


def _patch_modules(mocks: dict):
    """Return a patch.dict context manager for sys.modules."""
    return patch.dict(
        "sys.modules",
        {
            "win32gui": mocks["win32gui"],
            "win32process": mocks["win32process"],
            "win32con": mocks["win32con"],
            "psutil": mocks["psutil"],
        },
    )


# ---------------------------------------------------------------------------
# Happy path: different threads → AttachThreadInput path
# ---------------------------------------------------------------------------

def test_happy_path_attach_thread_input(monkeypatch):
    """Different foreground/target threads → full AttachThreadInput dance → True returned."""
    mocks = _build_win32_mocks()

    attach_calls: list[tuple] = []

    def fake_attach(from_tid, to_tid, attach):
        attach_calls.append((from_tid, to_tid, attach))

    monkeypatch.setattr(
        "voice_commander.tools._win32._attach_thread_input", fake_attach
    )
    monkeypatch.setattr(
        "voice_commander.tools._win32._allow_set_foreground", lambda: None
    )

    with _patch_modules(mocks):
        result = focus_window_by_exe("target.exe")

    assert result is True
    # AttachThreadInput called with TRUE then FALSE
    assert call(_FG_TID, _TARGET_TID, True) in [call(*c) for c in attach_calls]
    assert call(_FG_TID, _TARGET_TID, False) in [call(*c) for c in attach_calls]
    # Attach TRUE comes before FALSE
    true_idx = next(i for i, c in enumerate(attach_calls) if c == (_FG_TID, _TARGET_TID, True))
    false_idx = next(i for i, c in enumerate(attach_calls) if c == (_FG_TID, _TARGET_TID, False))
    assert true_idx < false_idx

    mocks["win32gui"].BringWindowToTop.assert_called_once_with(_TARGET_HWND)
    mocks["win32gui"].SetForegroundWindow.assert_called_once_with(_TARGET_HWND)


# ---------------------------------------------------------------------------
# Happy path: same thread → AttachThreadInput skipped
# ---------------------------------------------------------------------------

def test_happy_path_same_thread_skips_attach(monkeypatch):
    """Same foreground/target thread → AttachThreadInput NOT called."""
    mocks = _build_win32_mocks(fg_tid=_TARGET_TID, target_tid=_TARGET_TID)

    attach_calls: list[tuple] = []

    def fake_attach(from_tid, to_tid, attach):
        attach_calls.append((from_tid, to_tid, attach))

    monkeypatch.setattr(
        "voice_commander.tools._win32._attach_thread_input", fake_attach
    )
    monkeypatch.setattr(
        "voice_commander.tools._win32._allow_set_foreground", lambda: None
    )

    with _patch_modules(mocks):
        result = focus_window_by_exe("target.exe")

    assert result is True
    assert len(attach_calls) == 0


# ---------------------------------------------------------------------------
# Minimized window: ShowWindow(SW_RESTORE) called first
# ---------------------------------------------------------------------------

def test_minimized_window_restores_before_focus(monkeypatch):
    """IsIconic returns True → ShowWindow(SW_RESTORE) is called before the focus sequence."""
    mocks = _build_win32_mocks(is_iconic=True)

    monkeypatch.setattr("voice_commander.tools._win32._attach_thread_input", lambda *a: None)
    monkeypatch.setattr("voice_commander.tools._win32._allow_set_foreground", lambda: None)

    with _patch_modules(mocks):
        result = focus_window_by_exe("target.exe")

    assert result is True
    mocks["win32gui"].ShowWindow.assert_called_once_with(_TARGET_HWND, 9)  # SW_RESTORE = 9


def test_non_minimized_window_does_not_restore(monkeypatch):
    """IsIconic returns False → ShowWindow is NOT called."""
    mocks = _build_win32_mocks(is_iconic=False)

    monkeypatch.setattr("voice_commander.tools._win32._attach_thread_input", lambda *a: None)
    monkeypatch.setattr("voice_commander.tools._win32._allow_set_foreground", lambda: None)

    with _patch_modules(mocks):
        focus_window_by_exe("target.exe")

    mocks["win32gui"].ShowWindow.assert_not_called()


# ---------------------------------------------------------------------------
# Lockout failure: SetForegroundWindow raises → FocusWindowError
# ---------------------------------------------------------------------------

def test_lockout_failure_raises_focus_window_error(monkeypatch):
    """SetForegroundWindow raises → FocusWindowError is raised (not swallowed)."""
    class FakePyWinError(OSError):
        pass

    mocks = _build_win32_mocks(sfg_side_effect=FakePyWinError("(0, 'SetForegroundWindow', 'No error message is available')"))

    monkeypatch.setattr("voice_commander.tools._win32._attach_thread_input", lambda *a: None)
    monkeypatch.setattr("voice_commander.tools._win32._allow_set_foreground", lambda: None)

    with _patch_modules(mocks), pytest.raises(FocusWindowError) as exc_info:
        focus_window_by_exe("target.exe")

    assert "target.exe" in str(exc_info.value)


# ---------------------------------------------------------------------------
# AttachThreadInput detach guaranteed even on exception (try/finally)
# ---------------------------------------------------------------------------

def test_attach_detach_paired_on_exception(monkeypatch):
    """AttachThreadInput(FALSE) is called even when SetForegroundWindow raises."""
    class FakePyWinError(OSError):
        pass

    mocks = _build_win32_mocks(sfg_side_effect=FakePyWinError("SetForegroundWindow denied"))

    attach_calls: list[tuple] = []

    def fake_attach(from_tid, to_tid, attach):
        attach_calls.append((from_tid, to_tid, attach))

    monkeypatch.setattr("voice_commander.tools._win32._attach_thread_input", fake_attach)
    monkeypatch.setattr("voice_commander.tools._win32._allow_set_foreground", lambda: None)

    with _patch_modules(mocks):
        with pytest.raises(FocusWindowError):
            focus_window_by_exe("target.exe")

    # Must have both TRUE and FALSE calls
    attaches = [c for c in attach_calls if c[2] is True]
    detaches = [c for c in attach_calls if c[2] is False]
    assert len(attaches) >= 1, "Expected at least one AttachThreadInput(TRUE)"
    assert len(detaches) >= 1, "Expected at least one AttachThreadInput(FALSE) (detach)"


# ---------------------------------------------------------------------------
# Target not found: no matching process
# ---------------------------------------------------------------------------

def test_target_not_found_no_pids_raises_and_launches(monkeypatch):
    """No running process with that exe name → FocusWindowError raised, Popen called."""
    mocks = _build_win32_mocks()
    mocks["psutil"].process_iter.return_value = []  # no matching processes

    monkeypatch.setattr("voice_commander.tools._win32._attach_thread_input", lambda *a: None)
    monkeypatch.setattr("voice_commander.tools._win32._allow_set_foreground", lambda: None)

    with _patch_modules(mocks), patch("voice_commander.tools._win32.Popen") as mock_popen:
        with pytest.raises(FocusWindowError) as exc_info:
            focus_window_by_exe("target.exe", launch_path="C:/Apps/target.exe")

    mock_popen.assert_called_once_with(["C:/Apps/target.exe"])
    assert "target.exe" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Target not found: process running but no visible window
# ---------------------------------------------------------------------------

def test_target_not_found_no_window_raises(monkeypatch):
    """Process running but EnumWindows yields no visible window → FocusWindowError."""
    mocks = _build_win32_mocks()

    # Override EnumWindows to call the callback with a window owned by a DIFFERENT pid
    def _enum_no_match(callback, extra):
        # pid doesn't match target_pid
        mocks["win32process"].GetWindowThreadProcessId.return_value = (_FG_TID, 8888)
        callback(3003, extra)

    mocks["win32gui"].EnumWindows.side_effect = _enum_no_match

    monkeypatch.setattr("voice_commander.tools._win32._attach_thread_input", lambda *a: None)
    monkeypatch.setattr("voice_commander.tools._win32._allow_set_foreground", lambda: None)

    with _patch_modules(mocks):
        with pytest.raises(FocusWindowError) as exc_info:
            focus_window_by_exe("target.exe")

    assert "no visible" in str(exc_info.value).lower() or "target.exe" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Import error: pywin32 unavailable
# ---------------------------------------------------------------------------

def test_import_error_raises_and_launches():
    """When pywin32/psutil is unavailable, FocusWindowError is raised and Popen called."""
    with (
        patch.dict("sys.modules", {"win32gui": None, "win32con": None, "win32process": None, "psutil": None}),
        patch("voice_commander.tools._win32.Popen") as mock_popen,
        pytest.raises(FocusWindowError),
    ):
        focus_window_by_exe("target.exe", launch_path="C:/Apps/target.exe")

    mock_popen.assert_called_once_with(["C:/Apps/target.exe"])


# ---------------------------------------------------------------------------
# Verification polling: succeeds on 3rd poll
# ---------------------------------------------------------------------------

def test_verify_foreground_succeeds_on_third_poll(monkeypatch):
    """Verification polls return wrong hwnd twice then correct → returns True."""
    # First call = get fg thread (returns fg_hwnd = 2002)
    # Then verification polls: wrong, wrong, correct
    mocks = _build_win32_mocks(
        get_fg_sequence=[9999, 9999, _TARGET_HWND, _TARGET_HWND]
    )

    monkeypatch.setattr("voice_commander.tools._win32._attach_thread_input", lambda *a: None)
    monkeypatch.setattr("voice_commander.tools._win32._allow_set_foreground", lambda: None)
    # Patch time.sleep so the test doesn't actually wait 60 ms
    with _patch_modules(mocks), patch("voice_commander.tools._win32.time.sleep"):
        result = focus_window_by_exe("target.exe")

    assert result is True


# ---------------------------------------------------------------------------
# Verification polling: all wrong → FocusWindowError
# ---------------------------------------------------------------------------

def test_verify_foreground_all_wrong_raises(monkeypatch):
    """All verification polls return wrong hwnd → FocusWindowError raised."""
    mocks = _build_win32_mocks(
        get_fg_sequence=[9999, 9999, 9999, 9999, 9999]  # never matches TARGET_HWND
    )

    monkeypatch.setattr("voice_commander.tools._win32._attach_thread_input", lambda *a: None)
    monkeypatch.setattr("voice_commander.tools._win32._allow_set_foreground", lambda: None)

    with _patch_modules(mocks), patch("voice_commander.tools._win32.time.sleep"):
        with pytest.raises(FocusWindowError) as exc_info:
            focus_window_by_exe("target.exe")

    assert "verification" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# AllowSetForegroundWindow is called before the focus attempt
# ---------------------------------------------------------------------------

def test_allow_set_foreground_called(monkeypatch):
    """_allow_set_foreground() is called as part of the focus sequence."""
    mocks = _build_win32_mocks()

    allow_calls: list[int] = []

    def fake_allow():
        allow_calls.append(1)

    monkeypatch.setattr("voice_commander.tools._win32._allow_set_foreground", fake_allow)
    monkeypatch.setattr("voice_commander.tools._win32._attach_thread_input", lambda *a: None)

    with _patch_modules(mocks):
        focus_window_by_exe("target.exe")

    assert len(allow_calls) == 1, "_allow_set_foreground should be called exactly once"


# ---------------------------------------------------------------------------
# FocusWindowError is exported from the module
# ---------------------------------------------------------------------------

def test_focus_window_error_is_runtime_error():
    """FocusWindowError is a RuntimeError subclass (for dispatcher compatibility)."""
    err = FocusWindowError("test")
    assert isinstance(err, RuntimeError)
    assert str(err) == "test"
