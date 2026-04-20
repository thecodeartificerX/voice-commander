import threading
import time

import pytest
from pynput.keyboard import Controller, Key

from voice_commander.hotkey import KEY_ALIASES, HotkeyController


def test_key_alias_resolves():
    assert KEY_ALIASES["scroll_lock"] is Key.scroll_lock


def test_unknown_key_raises():
    with pytest.raises(ValueError):
        HotkeyController(key="no_such_key", on_toggle=lambda: None)


@pytest.mark.hardware
def test_toggle_fires_on_scroll_lock(tmp_path):
    fired = threading.Event()
    ctrl = HotkeyController(key="scroll_lock", on_toggle=fired.set)
    ctrl.start()
    try:
        time.sleep(0.2)
        Controller().tap(Key.scroll_lock)
        assert fired.wait(timeout=2.0), "toggle callback did not fire"
    finally:
        ctrl.stop()
