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


# ----- B-M1: tightened infra classifier (httpx-only + targeted stdlib types) -----


def test_classify_file_not_found_is_program_not_infra():
    """B-M1: FileNotFoundError is a program bug, NOT infra (despite OSError MRO)."""
    from voice_commander.observability.errors import classify
    assert classify(FileNotFoundError("missing config"), where="dispatcher") == "program"


def test_classify_permission_error_is_program_not_infra():
    """B-M1: PermissionError is a program bug, NOT infra."""
    from voice_commander.observability.errors import classify
    assert classify(PermissionError("denied"), where="dispatcher") == "program"


def test_classify_httpx_connect_error_is_infra():
    """B-M1: httpx errors classify as infra via module path, not MRO walking."""
    from voice_commander.observability.errors import classify
    import httpx
    exc = httpx.ConnectError("server down")
    assert classify(exc, where="llm_router") == "infra"


def test_classify_socket_gaierror_from_non_daemon_is_program():
    """B-M1: raw OSError-derived errors outside the daemon audio path are program bugs.

    ``socket.gaierror`` (DNS failure) is in the targeted stdlib set so it stays
    infra. Bare ``OSError`` from a non-daemon caller, however, is program.
    """
    from voice_commander.observability.errors import classify
    # bare OSError from non-daemon path → program (the strict change vs the
    # prior MRO-walking classifier).
    assert classify(OSError("read error"), where="dispatcher") == "program"

    # gaierror is explicitly listed → infra regardless of where.
    import socket
    assert classify(socket.gaierror("no such host"), where="dispatcher") == "infra"
