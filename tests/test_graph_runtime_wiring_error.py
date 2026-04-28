"""Test WiringError is defined and importable from graph_runtime."""


def test_wiring_error_importable():
    from voice_commander.commands.graph_runtime import WiringError
    exc = WiringError("test error")
    assert str(exc) == "test error"


def test_wiring_error_is_exception():
    from voice_commander.commands.graph_runtime import WiringError
    assert issubclass(WiringError, Exception)


def test_classify_wiring_error_as_wiring():
    from voice_commander.commands.graph_runtime import WiringError
    from voice_commander.observability.errors import classify
    exc = WiringError("missing kwarg 'right'")
    # classify uses type name; WiringError must match _WIRING_TYPES
    # Update errors.py to import WiringError or match by name
    category = classify(exc, where="graph_runtime")
    assert category == "wiring"
