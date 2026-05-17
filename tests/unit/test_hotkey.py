import threading
import time

import pytest
from pynput.keyboard import Controller, Key

from voice_commander.hotkey import KEY_ALIASES, HotkeyController


def test_key_alias_resolves():
    assert KEY_ALIASES["scroll_lock"] is Key.scroll_lock


def test_ctrl_r_alias_resolves():
    assert KEY_ALIASES["ctrl_r"] is Key.ctrl_r


def test_unknown_key_raises():
    with pytest.raises(ValueError):
        HotkeyController(bindings={"no_such_key": lambda: None})


def test_empty_bindings_raises():
    with pytest.raises(ValueError):
        HotkeyController(bindings={})


def test_multi_binding_dispatches_correct_callback():
    """Verify each key in bindings routes to its own callback."""
    fired_a = threading.Event()
    fired_b = threading.Event()
    ctrl = HotkeyController(
        bindings={
            "scroll_lock": fired_a.set,
            "ctrl_r": fired_b.set,
        }
    )
    # Verify internal dispatch table has two entries
    assert len(ctrl._dispatch) == 2


def test_listener_does_not_suppress():
    """pynput Listener must NOT use suppress=True."""
    ctrl = HotkeyController(bindings={"scroll_lock": lambda: None})
    ctrl.start()
    try:
        assert ctrl._listener is not None
        # pynput Listener stores suppress flag; default is False
        assert not getattr(ctrl._listener, "_suppress", True)
    finally:
        ctrl.stop()


@pytest.mark.hardware
def test_toggle_fires_on_scroll_lock(tmp_path):
    fired = threading.Event()
    ctrl = HotkeyController(bindings={"scroll_lock": fired.set})
    ctrl.start()
    try:
        time.sleep(0.2)
        Controller().tap(Key.scroll_lock)
        assert fired.wait(timeout=2.0), "toggle callback did not fire"
    finally:
        ctrl.stop()


# ---------------------------------------------------------------------------
# Debounce tests (Task 3 — ADR 0089)
# Fake clock via monkeypatch — no real sleeps, fully deterministic.
# ---------------------------------------------------------------------------


def test_debounce_drops_second_rapid_release(monkeypatch):
    """Two releases of the same key within 50 ms dispatch the callback only once.

    Uses a fake monotonic clock; no real sleep required.
    """
    import voice_commander.hotkey as _hotkey_mod

    _clock = [0.0]

    def _fake_monotonic() -> float:
        return _clock[0]

    monkeypatch.setattr(_hotkey_mod.time, "monotonic", _fake_monotonic)

    fired = []
    ctrl = HotkeyController(bindings={"scroll_lock": lambda: fired.append(1)})

    from pynput.keyboard import Key
    scroll_key = Key.scroll_lock

    _clock[0] = 1.000  # first release at t=1.000 s
    ctrl._on_release(scroll_key)
    assert len(fired) == 1, "First release must dispatch"

    _clock[0] = 1.020  # 20 ms later — within 50 ms window
    ctrl._on_release(scroll_key)
    assert len(fired) == 1, f"Second release within 50 ms must be dropped; got {len(fired)}"


def test_debounce_allows_second_release_after_window(monkeypatch):
    """Two releases of the same key spaced more than 50 ms apart dispatch twice.

    Uses a fake monotonic clock; no real sleep required.
    """
    import voice_commander.hotkey as _hotkey_mod

    _clock = [0.0]

    def _fake_monotonic() -> float:
        return _clock[0]

    monkeypatch.setattr(_hotkey_mod.time, "monotonic", _fake_monotonic)

    fired = []
    ctrl = HotkeyController(bindings={"scroll_lock": lambda: fired.append(1)})

    from pynput.keyboard import Key
    scroll_key = Key.scroll_lock

    _clock[0] = 1.000
    ctrl._on_release(scroll_key)
    assert len(fired) == 1

    _clock[0] = 1.060  # 60 ms later — outside 50 ms window
    ctrl._on_release(scroll_key)
    assert len(fired) == 2, f"Second release after 60 ms must dispatch; got {len(fired)}"


def test_debounce_different_keys_are_independent(monkeypatch):
    """Two different keys each have their own independent debounce timer.

    Rapid alternation between scroll_lock and ctrl_r should dispatch one
    callback each (two total), never suppressing the other key.
    Uses a fake monotonic clock; both presses are simultaneous at a realistic
    monotonic base (1000.0 s) so the never-fired sentinel (0.0) does not
    falsely debounce either key.
    """
    import voice_commander.hotkey as _hotkey_mod

    _clock = [0.0]

    def _fake_monotonic() -> float:
        return _clock[0]

    monkeypatch.setattr(_hotkey_mod.time, "monotonic", _fake_monotonic)

    fired_sl = []
    fired_cr = []
    ctrl = HotkeyController(
        bindings={
            "scroll_lock": lambda: fired_sl.append(1),
            "ctrl_r": lambda: fired_cr.append(1),
        }
    )

    from pynput.keyboard import Key

    # Fire both at t=1000.0 (realistic monotonic base — well above the 50 ms
    # debounce window so the never-fired sentinel of 0.0 does not falsely
    # suppress either key; both are DIFFERENT keys so independent timers apply)
    _clock[0] = 1000.0
    ctrl._on_release(Key.scroll_lock)
    ctrl._on_release(Key.ctrl_r)

    assert len(fired_sl) == 1, f"scroll_lock fired {len(fired_sl)} times; expected 1"
    assert len(fired_cr) == 1, f"ctrl_r fired {len(fired_cr)} times; expected 1"


def test_debounce_last_fire_dict_populated(monkeypatch):
    """After a dispatched release, _last_fire records the key's timestamp."""
    import voice_commander.hotkey as _hotkey_mod

    _clock = [42.0]

    def _fake_monotonic() -> float:
        return _clock[0]

    monkeypatch.setattr(_hotkey_mod.time, "monotonic", _fake_monotonic)

    ctrl = HotkeyController(bindings={"scroll_lock": lambda: None})

    from pynput.keyboard import Key
    scroll_key = Key.scroll_lock

    ctrl._on_release(scroll_key)

    assert scroll_key in ctrl._last_fire, "_last_fire must record the key after dispatch"
    assert ctrl._last_fire[scroll_key] == 42.0, (
        f"timestamp must equal the fake clock value 42.0; got {ctrl._last_fire[scroll_key]}"
    )
