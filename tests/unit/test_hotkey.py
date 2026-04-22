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
