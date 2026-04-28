"""Unit tests for observability/errors.py classify()."""
import pytest


def test_classify_imports():
    """errors module is importable."""
    from voice_commander.observability.errors import classify, Category  # noqa: F401


def test_classify_program_default():
    from voice_commander.observability.errors import classify
    assert classify(ValueError("bad"), where="test") == "program"


def test_classify_wiring_error():
    from voice_commander.observability.errors import classify
    # Simulate WiringError by creating a class with that name
    class WiringError(Exception):
        pass
    exc = WiringError("missing kwarg")
    assert classify(exc, where="graph_runtime") == "wiring"


def test_classify_llm_error():
    from voice_commander.observability.errors import classify
    class LLMPlanError(Exception):
        pass
    exc = LLMPlanError("bad json")
    assert classify(exc, where="llm_router") == "llm"


def test_classify_connection_refused_infra():
    from voice_commander.observability.errors import classify
    assert classify(ConnectionRefusedError("refused"), where="daemon") == "infra"


def test_classify_timeout_infra():
    from voice_commander.observability.errors import classify
    assert classify(TimeoutError("timed out"), where="llm_router") == "infra"


def test_classify_os_error_infra():
    from voice_commander.observability.errors import classify
    assert classify(OSError("device not found"), where="daemon") == "infra"


def test_classify_runtime_error_program():
    from voice_commander.observability.errors import classify
    assert classify(RuntimeError("boom"), where="dispatcher") == "program"
