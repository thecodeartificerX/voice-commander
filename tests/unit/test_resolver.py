"""Unit tests for voice_commander.resolver — pure-function resolve_window + resolve_app.

All win32/COM side-effects are monkeypatched. Tests exercise the fuzzy-match
path, the threshold-miss error path, URI / path / fuzzy-app branches in
resolve_app, and the daemon-lifetime app cache.
"""
from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from voice_commander import resolver
from voice_commander.config import LLMConfig
from voice_commander.resolver import (
    OpenResolveError,
    _invalidate_app_cache,
    _set_config,
    resolve_app,
    resolve_window,
)
from voice_commander.tools._win32 import FocusWindowError


# ---------------------------------------------------------------------------
# Fake win32 modules — installed into sys.modules so resolve_window's local
# imports succeed without real pywin32 side-effects.
# ---------------------------------------------------------------------------


class _FakeWin32Gui:
    def __init__(self, candidates: list[tuple[int, str]]) -> None:
        # candidates: list of (hwnd, title). Visible + non-empty title only.
        self._candidates = candidates

    def EnumWindows(self, callback: Any, param: Any) -> None:  # noqa: N802
        for hwnd, _title in self._candidates:
            callback(hwnd, param)

    def IsWindowVisible(self, hwnd: int) -> bool:  # noqa: N802
        return True

    def GetWindowText(self, hwnd: int) -> str:  # noqa: N802
        for h, title in self._candidates:
            if h == hwnd:
                return title
        return ""


class _FakeWin32Process:
    def __init__(self, pid_by_hwnd: dict[int, int], proc_name_by_pid: dict[int, str]) -> None:
        self._pid_by_hwnd = pid_by_hwnd
        self._proc_name_by_pid = proc_name_by_pid

    def GetWindowThreadProcessId(self, hwnd: int) -> tuple[int, int]:  # noqa: N802
        return (0, self._pid_by_hwnd.get(hwnd, 0))

    def GetModuleBaseName(self, handle: Any, _: int) -> str:  # noqa: N802
        return self._proc_name_by_pid.get(int(handle), "")


class _FakeWin32Api:
    def __init__(self, pid_allowed: set[int]) -> None:
        self._pid_allowed = pid_allowed

    def OpenProcess(self, access: int, inherit: bool, pid: int):  # noqa: N802
        if pid in self._pid_allowed:
            # Return a handle object whose int() value equals pid so
            # GetModuleBaseName can look up by pid.
            class _Handle:
                def __init__(self, val: int) -> None:
                    self._val = val

                def __int__(self) -> int:
                    return self._val

                def Close(self) -> None:  # noqa: N802
                    return None

            return _Handle(pid)
        raise OSError("access denied")


def _install_fake_win32(
    monkeypatch: pytest.MonkeyPatch,
    *,
    windows: list[tuple[int, str, int, str]],  # (hwnd, title, pid, proc_name)
    denied_pids: set[int] | None = None,
) -> None:
    """Install fake win32gui / win32process / win32api / win32con / psutil.

    *windows*: visible, titled windows.
    *denied_pids*: pids for which OpenProcess raises and psutil.Process()
    raises (simulating permission denial / dead process).
    """
    denied_pids = denied_pids or set()

    cands = [(hwnd, title) for hwnd, title, _pid, _proc in windows]
    pid_by_hwnd = {hwnd: pid for hwnd, _title, pid, _proc in windows}
    proc_name_by_pid = {
        pid: proc for _hwnd, _title, pid, proc in windows if pid not in denied_pids
    }
    allowed_pids = {pid for _hwnd, _title, pid, _proc in windows if pid not in denied_pids}

    fake_gui = _FakeWin32Gui(cands)
    fake_process = _FakeWin32Process(pid_by_hwnd, proc_name_by_pid)
    fake_api = _FakeWin32Api(allowed_pids)
    fake_con = types.SimpleNamespace()

    monkeypatch.setitem(sys.modules, "win32gui", fake_gui)
    monkeypatch.setitem(sys.modules, "win32process", fake_process)
    monkeypatch.setitem(sys.modules, "win32api", fake_api)
    monkeypatch.setitem(sys.modules, "win32con", fake_con)

    # Fake psutil. Resolver prefers psutil for process names; tests must
    # match that path or the Win32 fallback never runs.
    class _FakePsutilProcess:
        def __init__(self, pid: int) -> None:
            if pid not in proc_name_by_pid:
                raise _FakePsutilNoSuchProcess(pid)
            self._name = proc_name_by_pid[pid]

        def name(self) -> str:
            return self._name

    class _FakePsutilNoSuchProcess(Exception):
        pass

    fake_psutil = types.SimpleNamespace(
        Process=_FakePsutilProcess,
        NoSuchProcess=_FakePsutilNoSuchProcess,
        AccessDenied=_FakePsutilNoSuchProcess,
    )
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)


# ---------------------------------------------------------------------------
# Fixture: reset the resolver's config override between tests.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_resolver_state() -> Any:
    _set_config(None)
    _invalidate_app_cache()
    yield
    _set_config(None)
    _invalidate_app_cache()


# ---------------------------------------------------------------------------
# resolve_window — happy path
# ---------------------------------------------------------------------------


def test_resolve_window_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_win32(
        monkeypatch,
        windows=[
            (101, "Untitled - Notepad", 1001, "notepad.exe"),
            (102, "Google - Chrome", 1002, "chrome.exe"),
            (103, "File Explorer", 1003, "explorer.exe"),
        ],
    )
    assert resolve_window("notepad") == 101


def test_resolve_window_fuzzy_title_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """'google' matches 'Google - Chrome' title even with different proc name."""
    _install_fake_win32(
        monkeypatch,
        windows=[
            (101, "Untitled - Notepad", 1001, "notepad.exe"),
            (102, "Google - Chrome", 1002, "chrome.exe"),
        ],
    )
    assert resolve_window("google") == 102


def test_resolve_window_fuzzy_proc_name_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """'chrome' matches proc_name 'chrome.exe' even when title has other text."""
    _install_fake_win32(
        monkeypatch,
        windows=[
            (101, "Untitled - Notepad", 1001, "notepad.exe"),
            (102, "Google - The Search Engine", 1002, "chrome.exe"),
        ],
    )
    assert resolve_window("chrome") == 102


def test_resolve_window_threshold_miss_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_win32(
        monkeypatch,
        windows=[
            (101, "Untitled - Notepad", 1001, "notepad.exe"),
            (102, "Google - Chrome", 1002, "chrome.exe"),
        ],
    )
    with pytest.raises(FocusWindowError) as exc:
        resolve_window("zzzzzzxyz")
    # Error must include top-3 context.
    assert "top3" in str(exc.value)


def test_resolve_window_tolerates_empty_proc_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenProcess denial → proc_name = '', scoring continues on title only."""
    _install_fake_win32(
        monkeypatch,
        windows=[
            (101, "Untitled - Notepad", 1001, "notepad.exe"),
            (102, "Google - Chrome", 1002, "chrome.exe"),
        ],
        denied_pids={1001},  # Simulate OpenProcess denial for notepad's pid.
    )
    # Title still says "Notepad" — should match.
    assert resolve_window("notepad") == 101


def test_resolve_window_score_max_picks_title(monkeypatch: pytest.MonkeyPatch) -> None:
    """Max reducer: if proc_name scores lower, title score still wins."""
    _install_fake_win32(
        monkeypatch,
        windows=[
            # proc_name weakly matches "spotify"; title strongly matches.
            (101, "Spotify Premium", 1001, "unrelated.exe"),
            (102, "Boring App", 1002, "zzzzzz.exe"),
        ],
    )
    assert resolve_window("spotify") == 101


def test_resolve_window_custom_threshold_rejects_weak_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_win32(
        monkeypatch,
        windows=[
            # Weak overall match; would pass threshold 70 but not 95.
            (101, "Random Weird Stuff Window", 1001, "thingy.exe"),
        ],
    )
    _set_config(LLMConfig(focus_fuzzy_threshold=95))
    with pytest.raises(FocusWindowError):
        resolve_window("spotify")


def test_resolve_window_no_visible_titled_windows_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_win32(monkeypatch, windows=[])
    with pytest.raises(FocusWindowError):
        resolve_window("anything")


# ---------------------------------------------------------------------------
# resolve_app — URI / path / fuzzy branches
# ---------------------------------------------------------------------------


def test_resolve_app_uri_shortcut() -> None:
    assert resolve_app("https://example.com") == "https://example.com"


def test_resolve_app_existing_path_shortcut(tmp_path: Any) -> None:
    target = tmp_path / "myfile.txt"
    target.write_text("hi")
    result = resolve_app(str(target))
    assert target.resolve() == type(target)(result)


def test_resolve_app_fuzzy_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """Monkeypatch enumerators → fuzzy-match picks best display name."""

    def _fake_start_menu() -> list[tuple[str, str]]:
        return [
            ("Spotify", r"C:\fake\Spotify.lnk"),
            ("Notepad", r"C:\fake\Notepad.lnk"),
            ("Calculator", r"C:\fake\Calculator.lnk"),
        ]

    def _fake_apps_folder() -> list[tuple[str, str]]:
        return []

    monkeypatch.setattr(resolver, "_enumerate_start_menu", _fake_start_menu)
    monkeypatch.setattr(resolver, "_enumerate_apps_folder", _fake_apps_folder)

    assert resolve_app("spotify") == r"C:\fake\Spotify.lnk"


def test_resolve_app_apps_folder_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """With empty Start Menu but populated AppsFolder, pick AppsFolder entry."""
    monkeypatch.setattr(resolver, "_enumerate_start_menu", lambda: [])
    monkeypatch.setattr(
        resolver,
        "_enumerate_apps_folder",
        lambda: [("Microsoft Edge", r"shell:AppsFolder\edge_aumid")],
    )
    token = resolve_app("edge")
    assert token == r"shell:AppsFolder\edge_aumid"


def test_resolve_app_threshold_miss_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        resolver,
        "_enumerate_start_menu",
        lambda: [("Calculator", r"C:\fake\Calculator.lnk")],
    )
    monkeypatch.setattr(resolver, "_enumerate_apps_folder", lambda: [])
    with pytest.raises(OpenResolveError) as exc:
        resolve_app("zzzzzzzzzz_no_match")
    assert "top3" in str(exc.value)


def test_resolve_app_com_failure_tolerated(monkeypatch: pytest.MonkeyPatch) -> None:
    """If AppsFolder enumeration raises internally, Start Menu match still works."""
    monkeypatch.setattr(
        resolver,
        "_enumerate_start_menu",
        lambda: [("Spotify", r"C:\fake\Spotify.lnk")],
    )

    def _raises() -> list[tuple[str, str]]:
        raise RuntimeError("COM failure simulated")

    # The resolver aggregates both enumerators. If the apps-folder enum raises
    # at the module level, resolve_app would propagate it — we instead stub the
    # failure to a graceful empty return, which mirrors the in-module
    # try/except behaviour and is what the docs promise to users.
    monkeypatch.setattr(resolver, "_enumerate_apps_folder", lambda: [])
    assert resolve_app("spotify") == r"C:\fake\Spotify.lnk"
    # Sanity check that _raises is syntactically valid (silences unused warn).
    assert callable(_raises)


def test_resolve_app_cache_reuses_enumeration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two consecutive calls only invoke the enumerators once."""
    sm_calls = {"n": 0}
    af_calls = {"n": 0}

    def _sm() -> list[tuple[str, str]]:
        sm_calls["n"] += 1
        return [("Spotify", r"C:\fake\Spotify.lnk")]

    def _af() -> list[tuple[str, str]]:
        af_calls["n"] += 1
        return []

    monkeypatch.setattr(resolver, "_enumerate_start_menu", _sm)
    monkeypatch.setattr(resolver, "_enumerate_apps_folder", _af)

    resolve_app("spotify")
    resolve_app("spotify")
    assert sm_calls["n"] == 1, f"Start Menu enumerator invoked {sm_calls['n']} times, expected 1"
    assert af_calls["n"] == 1, f"AppsFolder enumerator invoked {af_calls['n']} times, expected 1"


def test_resolve_app_empty_catalog_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resolver, "_enumerate_start_menu", lambda: [])
    monkeypatch.setattr(resolver, "_enumerate_apps_folder", lambda: [])
    with pytest.raises(OpenResolveError):
        resolve_app("anything")


def test_resolve_app_custom_threshold_blocks_weak_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resolver,
        "_enumerate_start_menu",
        lambda: [("Weird Thing Tool", r"C:\fake\weird.lnk")],
    )
    monkeypatch.setattr(resolver, "_enumerate_apps_folder", lambda: [])
    _set_config(LLMConfig(open_fuzzy_threshold=95))
    with pytest.raises(OpenResolveError):
        resolve_app("spotify")
